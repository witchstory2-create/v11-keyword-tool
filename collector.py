import requests
import re
from collections import Counter

def collect_issue_keywords():
    url = "https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko"

    try:
        res = requests.get(url, timeout=10)
        text = res.text
    except:
        return []

    titles = re.findall(r"<title>(.*?)</title>", text)

    stopwords = ["Google", "뉴스", "속보", "종합", "단독", "오늘", "관련", "기자"]

    words = []

    for title in titles:
        clean = re.sub(r"[^가-힣A-Za-z0-9 ]", " ", title)
        for w in clean.split():
            if len(w) >= 2 and w not in stopwords:
                words.append(w)

    counted = Counter(words)
    common = [w for w, c in counted.most_common(50)]

    money_seeds = ["환급", "지원금", "연금", "보험", "세금", "청약", "대출", "카드", "건강보험", "자동차보험"]

    candidates = []

    for w in common:
        candidates.append(w)
        for seed in money_seeds:
            candidates.append(f"{w} {seed}")

    return list(dict.fromkeys(candidates))
