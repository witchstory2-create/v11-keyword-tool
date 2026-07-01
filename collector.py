# -*- coding: utf-8 -*-
"""
collector.py (v17.2) - Discovery 단계
- Google News RSS + 네이버 뉴스 검색 API에서 최신 기사 제목을 모아
  '같은 기사 제목 안에서만' 인접 단어를 조합(bigram)하여 후보 키워드를 만든다.
- HTML 엔티티(&quot; 등) 및 영어 잔재어(and, the, com 등)를 원천 제거한다.
- 상시성 범용 키워드(보험/대출/연금 등)는 사전 차단.
"""

import re
import html
import time
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
import json

# ------------------------------------------------------------------
# 범용/상시성 키워드 (위험 등급 판정에 사용)
# ------------------------------------------------------------------
GENERIC_ROOT_WORDS = {
    "보험", "대출", "연금", "정부지원", "지원금", "정책자금",
    "신용대출", "국민연금", "자동차보험", "실손보험", "카드", "대출금리",
    "전세대출", "예금", "적금", "펀드", "주식", "부동산", "아파트",
    "다이어트", "운동", "건강보험", "국민건강보험", "저축", "재테크",
}

# ------------------------------------------------------------------
# 한글 불용어 (조사/접속어/보조어 등)
# ------------------------------------------------------------------
STOPWORDS = {
    "그리고", "하지만", "그러나", "이번", "관련", "위해", "통해", "대한",
    "오늘", "내일", "어제", "이날", "한편", "이에", "따라", "가장",
    "완전", "정말", "너무", "매우", "관계자는", "밝혔다", "전했다",
    "것으로", "것이다", "등을", "등이", "됐다", "됐다는", "라며",
    "있다", "없다", "한다", "했다", "하는", "이라", "이라는", "이런",
    "저런", "그런", "이제", "지금", "바로", "역시", "또한", "이후",
}

# ------------------------------------------------------------------
# 영어/HTML 잔재어 (RSS 파싱 시 흔히 섞이는 노이즈)
# ------------------------------------------------------------------
ENGLISH_STOPWORDS = {
    "and", "the", "quot", "amp", "com", "for", "with", "you", "are",
    "this", "that", "was", "will", "have", "from", "your", "pop",
    "top", "www", "net", "org", "html", "news", "the", "was", "not",
    "but", "all", "can", "has", "had", "her", "his", "one", "our",
    "out", "who", "get", "how", "now", "new", "vs",
}

PARTICLE_SUFFIX = ("은", "는", "이", "가", "을", "를", "의", "에", "에서",
                    "으로", "로", "와", "과", "도", "만", "까지", "부터")


def _strip_particle(token):
    for p in sorted(PARTICLE_SUFFIX, key=len, reverse=True):
        if len(token) > len(p) + 1 and token.endswith(p):
            return token[: -len(p)]
    return token


def _clean_title(title):
    """HTML 엔티티 해제 + 대괄호/괄호/특수문자 제거"""
    title = html.unescape(title)  # &quot; &amp; &#39; 등을 실제 문자로 변환 후 제거
    title = re.sub(r"\[[^\]]*\]", " ", title)
    title = re.sub(r"\([^)]*\)", " ", title)
    title = re.sub(r"<[^>]+>", " ", title)
    title = re.sub(r"[^가-힣0-9A-Za-z%\s]", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def _is_meaningless_token(t):
    """의미 없는 토큰(영어 짧은 단어, 숫자만, 잔재어) 판정"""
    if not t:
        return True
    if t.isdigit():
        return True
    low = t.lower()
    if low in ENGLISH_STOPWORDS:
        return True
    # 순수 영어(ascii) + 4자 이하 -> 대부분 잔재어/조사/약어이므로 차단
    if t.isascii() and t.isalpha() and len(t) <= 4:
        return True
    return False


def _tokenize(title):
    tokens = []
    for raw in _clean_title(title).split(" "):
        if not raw:
            continue
        t = _strip_particle(raw)
        if len(t) < 2:
            continue
        if t in STOPWORDS:
            continue
        if _is_meaningless_token(t):
            continue
        tokens.append(t)
    return tokens


def _is_blocked_generic(phrase):
    for g in GENERIC_ROOT_WORDS:
        if g in phrase:
            return True
    return False


def extract_bigrams_from_title(title):
    """같은 기사 제목 안에서만 인접 토큰 2개를 조합. 다른 기사 토큰과는 절대 섞지 않는다."""
    tokens = _tokenize(title)
    candidates = []
    for t in tokens:
        if len(t) >= 3 and not _is_blocked_generic(t):
            candidates.append(t)
    for i in range(len(tokens) - 1):
        a, b = tokens[i], tokens[i + 1]
        phrase = a + " " + b
        if _is_blocked_generic(phrase):
            continue
        candidates.append(phrase)
    return candidates


def fetch_google_news_rss(query=None, limit=100, timeout=6):
    if query:
        url = "https://news.google.com/rss/search?q=%s&hl=ko&gl=KR&ceid=KR:ko" % urllib.parse.quote(query)
    else:
        url = "https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko"
    items = []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as res:
            data = res.read()
        root = ET.fromstring(data)
        for item in root.findall(".//item")[:limit]:
            title = item.findtext("title") or ""
            link = item.findtext("link") or ""
            pub = item.findtext("pubDate") or ""
            if title:
                items.append({"title": title, "link": link, "pub": pub, "source": "google_rss"})
    except Exception:
        pass
    return items


NEWS_SEED_CATEGORIES = ["정치", "경제", "사회", "생활", "IT", "세계"]


def fetch_naver_news(search_api, categories=None, display=20, timeout=6):
    items = []
    cats = categories or NEWS_SEED_CATEGORIES
    for cat in cats:
        url = ("https://openapi.naver.com/v1/search/news.json?query=%s&display=%d&sort=date"
               % (urllib.parse.quote(cat), display))
        try:
            req = urllib.request.Request(url, headers={
                "X-Naver-Client-Id": search_api.client_id,
                "X-Naver-Client-Secret": search_api.client_secret,
            })
            with urllib.request.urlopen(req, timeout=timeout) as res:
                j = json.loads(res.read().decode("utf-8", errors="replace"))
            for it in j.get("items", []):
                title = re.sub(r"<[^>]+>", "", it.get("title", ""))
                title = html.unescape(title)
                items.append({"title": title, "link": it.get("link", ""),
                              "pub": it.get("pubDate", ""), "source": "naver_news"})
        except Exception:
            continue
    return items


def collect_candidates(search_api=None, discovery_target=100, light_filter_target=40, log=None):
    def _log(msg):
        if log:
            log(msg)

    articles = []
    articles += fetch_google_news_rss(limit=discovery_target)
    if search_api is not None:
        try:
            articles += fetch_naver_news(search_api, display=20)
        except Exception as e:
            _log(f"[collector] 네이버 뉴스 수집 실패: {e}")

    _log(f"[collector] 수집 기사 수: {len(articles)}")

    cand_map = {}
    now = time.time()
    for art in articles:
        title = art.get("title", "")
        if not title:
            continue
        phrases = extract_bigrams_from_title(title)
        for p in set(phrases):
            entry = cand_map.setdefault(p, {
                "keyword": p, "news_count": 0, "sources": set(),
                "first_seen": now, "sample_title": title, "sample_link": art.get("link", ""),
            })
            entry["news_count"] += 1
            entry["sources"].add(art.get("source", "unknown"))

    candidates = list(cand_map.values())
    candidates.sort(key=lambda x: x["news_count"], reverse=True)
    candidates = candidates[:discovery_target]
    _log(f"[collector] 1차 후보 수: {len(candidates)}")

    filtered = []
    for c in candidates:
        kw = c["keyword"]
        c["generic_flag"] = _is_blocked_generic(kw)
        if len(kw.replace(" ", "")) < 2:
            continue
        filtered.append(c)

    filtered.sort(key=lambda x: (x["generic_flag"], -x["news_count"]))
    result = filtered[:light_filter_target]
    _log(f"[collector] 경량 필터 후 검증 대상: {len(result)}")
    return result
