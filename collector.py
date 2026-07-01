import requests
import re
from collections import Counter

RSS_URLS = [
    "https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko"
]

BLOCK_WORDS = [
    "조선일보", "중앙일보", "동아일보", "한겨레", "경향신문", "매일경제", "한국경제",
    "연합뉴스", "뉴스", "신문", "기자", "단독", "속보", "종합", "광고", "구독",
    "포토", "영상", "인터뷰", "사설", "칼럼", "논평", "오늘", "관련"
]

MONEY_TOPICS = [
    "환급", "지원금", "연금", "보험", "세금", "청약", "대출", "금리",
    "건강보험", "자동차보험", "카드", "절세", "세액공제", "공제",
    "실업급여", "국민연금", "퇴직연금", "주택청약", "전세대출"
]

ISSUE_WORDS = [
    "인상", "인하", "변경", "개편", "확대", "축소", "시행", "신청",
    "지급", "대상", "조건", "조회", "환급", "지원", "발표", "기준"
]

def fetch_news_titles():
    titles = []

    for url in RSS_URLS:
        try:
            res = requests.get(url, timeout=10)
            text = res.text
            raw_titles = re.findall(r"<title>(.*?)</title>", text)

            for title in raw_titles:
                title = re.sub(r"<!\[CDATA\[|\]\]>", "", title)
                title = re.sub(r"\s-\s.*$", "", title)
                titles.append(title.strip())
        except:
            continue

    return titles

def is_bad_word(word):
    if len(word) < 2:
        return True
    if any(b in word for b in BLOCK_WORDS):
        return True
    if re.search(r"[a-zA-Z]{3,}", word):
        return True
    return False

def extract_clean_words(titles):
    words = []

    for title in titles:
        clean = re.sub(r"[^가-힣0-9 ]", " ", title)
        parts = clean.split()

        for p in parts:
            if not is_bad_word(p):
                words.append(p)

    return words

def build_candidates(words):
    counter = Counter(words)
    common_words = [w for w, c in counter.most_common(80)]

    candidates = []

    for w in common_words:
        if any(topic in w for topic in MONEY_TOPICS):
            candidates.append(w)

        for topic in MONEY_TOPICS:
            if topic in w or w in topic:
                candidates.append(topic)
            else:
                for issue in ISSUE_WORDS:
                    candidates.append(f"{w} {topic}")
                    candidates.append(f"{topic} {issue}")

    cleaned = []
    for c in candidates:
        c = c.strip()
        if 3 <= len(c) <= 20 and not is_bad_word(c):
            cleaned.append(c)

    return list(dict.fromkeys(cleaned))

def collect_issue_keywords():
    titles = fetch_news_titles()
    words = extract_clean_words(titles)

    candidates = build_candidates(words)

    default_keywords = [
        "건강보험료 환급",
        "국민연금 개편",
        "퇴직연금 수령방법",
        "연금저축 세액공제",
        "청약 조건 변경",
        "전세대출 금리",
        "자동차보험료 인상",
        "실업급여 조건",
        "지원금 신청",
        "세금 환급 조회"
    ]

    final = candidates + default_keywords

    return list(dict.fromkeys(final))[:80]
