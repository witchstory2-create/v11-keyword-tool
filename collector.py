# collector.py
# v16.4 - 이슈 키워드 후보 수집기
# 변경 사항(2026-07):
#   1) 구글 뉴스 RSS 제목 끝에 붙는 " - 언론사명" 부분을 제거
#      (기존에는 이게 그대로 남아 "머니투데이", "조선일보" 등이 키워드처럼 카운트됨)
#   2) 한 단어(unigram) 후보는 GENERIC_SEED_WORDS 이거나 수익 카테고리 단어를
#      직접 포함하는 경우에만 인정. 그 외("고유가" 등 개념 파편)는 후보에서 제외
#   3) [NEW] BLOCKED_WORDS에 '대한', '위한', '관한', '따른', '대해' 등 문법적 연결어를
#      대폭 보강. 이런 단어들은 "~에 대한", "~을 위한"처럼 명사 앞에서 문장을 이어줄 뿐
#      독립적인 의미가 없는데, 지금까지는 걸러지지 않아서 바로 옆 단어와 묶여
#      "대한 대출"처럼 실제로 존재하지 않는 조합(붙이면 '대환대출'과 혼동됨)을 만들어냈음.

import re
import feedparser
from collections import Counter

RSS_FEEDS = [
    "https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko",
    "https://news.google.com/rss/search?q=지원금&hl=ko&gl=KR&ceid=KR:ko",
    "https://news.google.com/rss/search?q=환급&hl=ko&gl=KR&ceid=KR:ko",
    "https://news.google.com/rss/search?q=대출&hl=ko&gl=KR&ceid=KR:ko",
    "https://news.google.com/rss/search?q=보험&hl=ko&gl=KR&ceid=KR:ko",
    "https://news.google.com/rss/search?q=연금&hl=ko&gl=KR&ceid=KR:ko",
]

# 언론사명 접미사 패턴: "...제목... - 언론사명" 형태에서 뒤쪽 언론사 부분을 통째로 제거
PRESS_SUFFIX_PATTERN = re.compile(r"\s*-\s*[^-]{1,20}$")

CLEAN_PATTERN = re.compile(r"[\[\(【].*?[\]\)】]|[\"“”'‘’]|…|·")

DATE_TOKEN_PATTERN = re.compile(
    r"^\d{1,4}(년|월|일|시|분)$|^\d{1,2}(월|일)\d{0,2}(일)?$|^(오늘|내일|어제|이번주|다음주)$"
)

STOPWORD_TAIL = {
    "출시", "이벤트", "마감", "매출", "전망", "시작", "종료",
    "연장", "개최", "안내", "발표", "확대", "축소", "논란",
    "인상", "인하", "우려", "주의", "경고",
}

GENERIC_SEED_WORDS = {
    "환급", "대출", "보험", "지원금", "연금", "국민연금", "적금",
    "예금", "세금", "카드", "수당", "급여", "복지", "혜택",
}

# [CHANGED] 문법적 연결어(조사성 표현)를 대폭 보강.
# 이 단어들은 뉴스 제목에서 "OOO에 대한", "OOO을 위한", "OOO에 따른"처럼
# 명사 뒤/앞에 붙어 문장을 이어줄 뿐, 그 자체로는 독립적인 키워드 의미가 없음.
# 걸러지지 않으면 바로 옆 단어와 묶여 "대한 대출"처럼 실존하지 않는 조합을 만들어냄.
BLOCKED_WORDS = {
    "그리고", "하지만", "이번", "정부", "관련", "위해", "통해",
    "가장", "모든", "많은", "전국", "오늘", "내일",
    # 여기부터 신규 추가
    "대한", "위한", "관한", "따른", "대해", "관해", "인한", "의한",
    "따라", "위해서", "관련해", "대해서", "관해서", "따라서",
    "이런", "그런", "저런", "이러한", "그러한", "저러한",
    "같은", "같이", "이후", "이전", "동안", "만큼", "처럼",
}

MONEY_KEYWORDS = [
    "지원금", "환급", "대출", "보험", "연금", "수당", "복지",
    "세금", "카드", "혜택", "적금", "예금", "급여", "상품권",
]

MIN_MENTION_COUNT = 2


def _strip_press_suffix(title: str) -> str:
    """구글 뉴스 제목 끝의 ' - 언론사명' 부분을 제거"""
    return PRESS_SUFFIX_PATTERN.sub("", title).strip()


def _clean_title(title: str) -> str:
    title = _strip_press_suffix(title)
    title = CLEAN_PATTERN.sub(" ", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def _clean_token(token: str) -> str:
    return token.strip(",.!?;:·…\"'()[]{}<>")


def _is_valid_token(token: str) -> bool:
    if len(token) < 2:
        return False
    if DATE_TOKEN_PATTERN.match(token):
        return False
    if token in BLOCKED_WORDS:
        return False
    if token in STOPWORD_TAIL:
        return False
    if token.isdigit():
        return False
    return True


def _is_money_topic(phrase: str) -> bool:
    return any(mk in phrase for mk in MONEY_KEYWORDS)


def _unigram_allowed(token: str) -> bool:
    """
    한 단어짜리 후보는 아래 둘 중 하나에 해당할 때만 후보로 인정한다.
      1) GENERIC_SEED_WORDS에 정확히 일치 (환급/대출/보험 등, scorer.py에서 가중치 낮게 처리됨)
      2) 수익 카테고리 단어를 그 자체로 포함 (예: '피해지원금'처럼 압축된 명사)
    이 조건에 해당하지 않는 '고유가', '머니투데이' 같은 파편/고유명사는 후보에서 제외한다.
    """
    if token in GENERIC_SEED_WORDS:
        return True
    if _is_money_topic(token):
        return True
    return False


def fetch_all_titles() -> list:
    titles = []
    seen = set()
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                t = _clean_title(entry.title)
                if t and t not in seen:
                    seen.add(t)
                    titles.append(t)
        except Exception as e:
            print(f"[RSS 수집 실패] {url} -> {e}")
    return titles


def extract_candidates(titles: list) -> list:
    counter = Counter()

    for title in titles:
        raw_tokens = [tok for tok in title.split(" ") if tok]
        tokens = [_clean_token(tok) for tok in raw_tokens]
        tokens = [tok for tok in tokens if tok]

        # 1) unigram - 허용된 경우에만 후보로 인정
        for tok in tokens:
            if _is_valid_token(tok) and _unigram_allowed(tok):
                counter[tok] += 1

        # 2) bigram - 인접 두 어절
        for i in range(len(tokens) - 1):
            a, b = tokens[i], tokens[i + 1]
            if not _is_valid_token(a) or not _is_valid_token(b):
                continue
            if b in STOPWORD_TAIL:
                continue
            phrase = f"{a} {b}"
            counter[phrase] += 1

    candidates = []
    for phrase, mentions in counter.items():
        if mentions < MIN_MENTION_COUNT:
            continue

        word_count = len(phrase.split(" "))
        is_generic = word_count == 1 and phrase in GENERIC_SEED_WORDS
        money_topic = _is_money_topic(phrase)

        candidates.append({
            "keyword": phrase,
            "mentions": mentions,
            "word_count": word_count,
            "is_generic": is_generic,
            "money_topic": money_topic,
        })

    candidates.sort(key=lambda x: x["mentions"], reverse=True)
    return candidates[:100]


def collect_issue_keywords() -> list:
    titles = fetch_all_titles()
    return extract_candidates(titles)


if __name__ == "__main__":
    titles = fetch_all_titles()
    print(f"=== 수집된 뉴스 제목 총 {len(titles)}개 ===")
    for t in titles[:30]:
        print("-", t)

    candidates = extract_candidates(titles)
    print(f"\n=== 최종 후보 키워드 총 {len(candidates)}개 ===")
    for c in candidates[:50]:
        print(
            f"{c['keyword']:<20} | mentions={c['mentions']:<3} | "
            f"word_count={c['word_count']} | is_generic={c['is_generic']} | "
            f"money_topic={c['money_topic']}"
        )
