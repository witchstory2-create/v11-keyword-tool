import requests
import re
from collections import Counter

RSS_URLS = [
    "https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko",
    "https://news.google.com/rss/search?q=지원금&hl=ko&gl=KR&ceid=KR:ko",
    "https://news.google.com/rss/search?q=환급&hl=ko&gl=KR&ceid=KR:ko",
    "https://news.google.com/rss/search?q=연금&hl=ko&gl=KR&ceid=KR:ko",
    "https://news.google.com/rss/search?q=대출&hl=ko&gl=KR&ceid=KR:ko",
    "https://news.google.com/rss/search?q=보험&hl=ko&gl=KR&ceid=KR:ko",
]

BLOCK_WORDS = [
    "조선일보", "중앙일보", "동아일보", "한겨레", "경향신문", "매일경제", "한국경제",
    "연합뉴스", "뉴스", "신문", "기자", "단독", "속보", "종합", "광고", "구독",
    "포토", "영상", "인터뷰", "사설", "칼럼", "논평", "관련", "본문",
    "무단", "전재", "배포", "금지"
]

MONEY_TOPICS = [
    "환급", "지원금", "연금", "보험", "세금", "청약", "대출", "금리",
    "건강보험", "자동차보험", "카드", "절세", "세액공제", "공제",
    "실업급여", "국민연금", "퇴직연금", "주택청약", "전세대출"
]

MIN_MENTION_COUNT = 2  # 최소 2회 이상 실제 등장해야 후보로 인정

DEFAULT_KEYWORDS = [
    "건강보험료 환급", "국민연금 개편", "퇴직연금 수령방법", "연금저축 세액공제",
    "청약 조건", "전세대출 금리", "자동차보험료 인상", "실업급여 조건",
    "지원금 신청", "세금 환급 조회", "IRP 세액공제", "연말정산 환급",
    "주택청약 조건", "자동차보험 비교", "건강보험료 조회"
]


def fetch_news_items():
    """RSS에서 제목 리스트만 수집한다."""
    items = []
    for url in RSS_URLS:
        try:
            res = requests.get(url, timeout=10)
            text = res.text
            blocks = re.findall(r"<item>(.*?)</item>", text, re.S)
            for block in blocks:
                title_match = re.search(r"<title>(.*?)</title>", block)
                if not title_match:
                    continue
                title = re.sub(r"<!\[CDATA\[|\]\]>", "", title_match.group(1))
                title = re.sub(r"\s-\s.*$", "", title).strip()
                if title:
                    items.append(title)
        except Exception:
            continue
    return items


def is_bad_word(word):
    if len(word) < 2:
        return True
    if any(block in word for block in BLOCK_WORDS):
        return True
    if re.search(r"[a-zA-Z]{3,}", word):
        return True
    return False


def extract_bigrams(title):
    """실제 뉴스 제목에서 인접한 단어쌍만 뽑는다. 조합 생성이 아니라 실제 등장 순서 기반."""
    clean = re.sub(r"[^가-힣0-9 ]", " ", title)
    parts = [p.strip() for p in clean.split() if not is_bad_word(p.strip())]

    bigrams = []
    for i in range(len(parts) - 1):
        pair = f"{parts[i]} {parts[i+1]}"
        if 3 <= len(pair) <= 25:
            bigrams.append(pair)

    return parts, bigrams


def collect_issue_keywords():
    """
    반환값: [{"keyword": str, "mentions": int, "is_money_topic": bool}, ...]
    뉴스에 실제로 등장한 단어/단어쌍만 후보로 인정한다.
    돈이 되는 주제와 무관한 bigram은 후보에서 제외한다(노이즈 필터링).
    """
    titles = fetch_news_items()

    word_counter = Counter()
    bigram_counter = Counter()

    for title in titles:
        words, bigrams = extract_bigrams(title)
        word_counter.update(words)
        bigram_counter.update(bigrams)

    candidates = {}

    # 단일 단어 후보: 돈이 되는 주제어가 실제 빈도 이상 등장했을 때만
    for word, cnt in word_counter.items():
        if cnt >= MIN_MENTION_COUNT and any(topic in word for topic in MONEY_TOPICS):
            candidates[word] = {
                "keyword": word,
                "mentions": cnt,
                "is_money_topic": True,
            }

    # 인접 단어쌍 후보: 실제로 나란히 등장한 조합 중, 돈이 되는 주제와 관련 있는 것만
    for pair, cnt in bigram_counter.items():
        if cnt < MIN_MENTION_COUNT:
            continue

        has_money = any(topic in pair for topic in MONEY_TOPICS)
        if not has_money:
            continue  # 돈과 무관한 노이즈 조합은 후보에서 제외

        if pair not in candidates:
            candidates[pair] = {
                "keyword": pair,
                "mentions": cnt,
                "is_money_topic": has_money,
            }

    # 오늘 뉴스에서 후보가 부족할 경우를 대비한 기본 시드 키워드 (mentions=0, 신뢰도 낮음)
    for kw in DEFAULT_KEYWORDS:
        if kw not in candidates:
            candidates[kw] = {
                "keyword": kw,
                "mentions": 0,
                "is_money_topic": True,
            }

    result = sorted(candidates.values(), key=lambda x: x["mentions"], reverse=True)
    return result[:100]
