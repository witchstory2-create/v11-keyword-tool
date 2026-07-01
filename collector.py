# collector.py
# v16.2 - 이슈 키워드 후보 수집기

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

BLOCKED_WORDS = {
    "그리고", "하지만", "이번", "정부", "관련", "위해", "통해",
    "가장", "모든", "많은", "전국", "오늘", "내일",
}

MIN_MENTION_COUNT = 2  # 결과가 너무 적으면 1로 낮춰서 튜닝


def _clean_title(title: str) -> str:
    title = CLEAN_PATTERN.sub(" ", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


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


def _is_money_topic(phrase: str) -> bool:
    money_keywords = [
        "지원금", "환급", "대출", "보험", "연금", "수당", "복지",
        "세금", "카드", "혜택", "적금", "예금", "급여", "상품권",
    ]
    return any(mk in phrase for mk in money_keywords)


def extract_candidates(titles: list) -> list:
    counter = Counter()

    for title in titles:
        tokens = [tok for tok in title.split(" ") if tok]

        for tok in tokens:
            if _is_valid_token(tok):
                counter[tok] += 1

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
    """app.py에서 호출하는 진입점 함수. 뉴스 수집 + 후보 추출을 한 번에 수행."""
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
