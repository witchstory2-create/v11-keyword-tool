# -*- coding: utf-8 -*-
"""
collector.py (v17 - 수익형 키워드 발굴기)
Discovery + 경량 필터 단계.

1) Google News RSS + 네이버 뉴스 검색 API에서 기사를 모은다.
2) 기사 단위로만 bigram/unigram 후보를 생성한다(서로 다른 기사 결합 금지).
3) GENERIC_ROOT_BLOCK 단어가 '단독 전체 키워드'인 경우 후보에서 원천 제외한다.
4) 후보 풀 최대 100개 -> 경량 필터(비-API) -> 상위 40개로 축소해서 반환한다.
"""

import re
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) KeywordTool/17.0"

NEWS_QUERIES = {
    "보험":     "자동차보험 OR 실손보험 OR 화재보험 OR 보험료",
    "대출금융": "신용대출 OR 대출금리 OR 정책자금대출 OR 대환대출",
    "세금":     "종합소득세 OR 연말정산 OR 세금환급 OR 부가세",
    "연금":     "국민연금 OR 퇴직연금 OR IRP OR 연금저축",
    "부동산":   "전세대출 OR 청약 OR 임대주택 OR 부동산세",
    "지원금":   "정부지원금 OR 지원금 신청 OR 온누리상품권 OR 바우처",
}

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"

RAW_POOL_CAP = 100
PREFILTER_KEEP_N = 40

# 문법적 연결어 / 잡단어 (bigram의 구성 요소로도 금지)
BLOCKED_WORDS = {
    "대한", "위한", "관한", "따른", "대해", "관련", "이번", "지난", "오늘",
    "내년", "올해", "최근", "당초", "예정", "이후", "이전", "그동안",
    "및", "등", "또", "혹은", "그리고", "하지만", "그러나", "한편",
    "발표", "밝혔다", "전했다", "말했다", "강조", "설명했다", "한국",
}

# 단독(전체 키워드)으로는 절대 허용하지 않는 범용 명사 사전.
# 단, 이 단어가 구체적 수식어와 결합된 2어 조합(예: '건강보험료 환급')은
# collector 단계에서는 통과시키고, scorer 단계에서 문서수 기반으로 위험 판정한다.
GENERIC_ROOT_BLOCK = {
    "보험", "대출", "연금", "환급", "지원", "지원금", "정부", "세금", "카드",
}

PARTICLE_SUFFIXES = [
    "으로부터", "에게서", "이라도", "에서는", "까지는",
    "으로는", "에는", "에서", "부터", "까지", "보다", "이나",
    "이란", "이며", "이고", "이지만",
    "으로", "로써", "로서",
    "은", "는", "이", "가", "을", "를", "의", "에", "로", "도", "만",
]

PROFIT_KEYWORDS = [
    "대출", "보험", "연금", "환급", "지원금", "수당", "보조금", "감면",
    "할인", "카드", "적금", "예금", "청약", "전세", "월세", "분양",
    "세금", "소득세", "부가세", "연말정산", "IRP", "ISA", "상품권",
    "바우처", "리모델링", "중도상환", "대환", "보장", "실비",
]


def strip_particle(word: str) -> str:
    for suf in PARTICLE_SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 2:
            return word[: -len(suf)]
    return word


def clean_title(raw_title: str) -> str:
    title = raw_title.strip()
    title = re.sub(r"\s*[-|–]\s*[^-|–]{1,20}$", "", title)
    title = re.sub(r"[\"'“”‘’\[\]()<>]", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def strip_html(text: str) -> str:
    import html as html_lib
    text = re.sub(r"<[^>]+>", "", text or "")
    return html_lib.unescape(text).strip()


def tokenize(title: str):
    raw_tokens = title.split(" ")
    tokens = []
    for t in raw_tokens:
        t = re.sub(r"[,\.!?…·:;]", "", t)
        if not t:
            continue
        t = strip_particle(t)
        tokens.append(t)
    return tokens


def is_blocked_token(word: str) -> bool:
    if word in BLOCKED_WORDS:
        return True
    if len(word) < 2:
        return True
    if re.fullmatch(r"[0-9]+[가-힣]{0,1}", word):
        return True
    return False


def is_generic_standalone(word: str) -> bool:
    """단독 전체 키워드가 범용 명사 그 자체인지 확인."""
    return word in GENERIC_ROOT_BLOCK


def is_profit_related(word: str) -> bool:
    return any(pk in word for pk in PROFIT_KEYWORDS)


def detect_category(text: str) -> str:
    for cat, kws in {
        "보험": ["보험", "실비", "화재보험", "자동차보험"],
        "대출금융": ["대출", "대환", "중도상환", "금리"],
        "세금": ["세금", "소득세", "부가세", "연말정산", "환급"],
        "연금": ["연금", "IRP", "퇴직", "국민연금"],
        "부동산": ["전세", "청약", "분양", "임대", "부동산"],
        "지원금": ["지원금", "바우처", "상품권", "수당", "보조금"],
    }.items():
        if any(kw in text for kw in kws):
            return cat
    return "기타"


def parse_pubdate(pubdate_raw: str):
    try:
        dt = parsedate_to_datetime(pubdate_raw)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return None


def recency_score(pubdate_raw: str) -> float:
    dt = parse_pubdate(pubdate_raw)
    if dt is None:
        return 0.3
    hours = (datetime.utcnow() - dt).total_seconds() / 3600.0
    if hours < 0:
        hours = 0
    if hours <= 3:
        return 1.0
    if hours <= 12:
        return 0.8
    if hours <= 24:
        return 0.5
    if hours <= 48:
        return 0.3
    return 0.1


def fetch_google_news_rss(query: str, timeout=8):
    url = GOOGLE_NEWS_RSS.format(query=urllib.parse.quote(query))
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    root = ET.fromstring(data)
    items = []
    for item in root.iter("item"):
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        pubdate_raw = item.findtext("pubDate") or ""
        items.append({"title": title, "link": link, "pubdate_raw": pubdate_raw, "source": "google"})
    return items


def fetch_naver_news(query: str, open_api, display=20):
    raw_items = open_api.search_news(query, display=display)
    items = []
    for it in raw_items:
        title = strip_html(it.get("title", ""))
        link = it.get("originallink") or it.get("link", "")
        pubdate_raw = it.get("pubDate", "")
        items.append({"title": title, "link": link, "pubdate_raw": pubdate_raw, "source": "naver"})
    return items


def generate_candidates_from_article(title: str):
    """
    기사 하나(title)에서만 unigram/bigram 후보를 생성한다.
    - 범용 명사(GENERIC_ROOT_BLOCK)가 '단독 전체 키워드'가 되는 것은 원천 제외.
    - 서로 다른 기사와는 절대 결합하지 않는다.
    """
    cleaned = clean_title(title)
    tokens = tokenize(cleaned)
    candidates = set()

    for tok in tokens:
        if is_blocked_token(tok):
            continue
        if is_generic_standalone(tok):
            continue  # '보험', '대출' 단독은 후보 자체에서 제외
        if is_profit_related(tok) and len(tok) >= 4:
            candidates.add(tok)

    for i in range(len(tokens) - 1):
        w1, w2 = tokens[i], tokens[i + 1]
        if is_blocked_token(w1) or is_blocked_token(w2):
            continue
        phrase = f"{w1} {w2}"
        if phrase in cleaned:
            candidates.add(phrase)

    return candidates, cleaned


def _collect_raw_pool(open_api, progress_cb=None):
    pool = {}
    seen_article_titles = set()
    total_queries = len(NEWS_QUERIES)
    done = 0

    for category, query in NEWS_QUERIES.items():
        merged_items = []
        try:
            merged_items.extend(fetch_google_news_rss(query))
        except Exception as e:
            if progress_cb:
                progress_cb(f"[경고] Google RSS '{category}' 수집 실패: {e}")
        try:
            merged_items.extend(fetch_naver_news(query, open_api))
        except Exception as e:
            if progress_cb:
                progress_cb(f"[경고] 네이버뉴스 '{category}' 수집 실패: {e}")

        for item in merged_items:
            raw_title = item["title"]
            if not raw_title:
                continue
            candidates, cleaned = generate_candidates_from_article(raw_title)
            if cleaned in seen_article_titles:
                continue
            seen_article_titles.add(cleaned)

            rscore = recency_score(item["pubdate_raw"])
            for kw in candidates:
                entry = pool.setdefault(kw, {
                    "keyword": kw, "mentions": 0, "categories": {},
                    "articles": [], "recency": 0.0, "sources": set(),
                })
                entry["mentions"] += 1
                cat = detect_category(kw + " " + cleaned)
                entry["categories"][cat] = entry["categories"].get(cat, 0) + 1
                entry["recency"] = max(entry["recency"], rscore)
                entry["sources"].add(item["source"])
                if len(entry["articles"]) < 3:
                    entry["articles"].append({
                        "title": cleaned, "link": item["link"], "pubdate": item["pubdate_raw"],
                    })

        done += 1
        if progress_cb:
            progress_cb(f"Discovery 진행 중... ({done}/{total_queries}) '{category}' 완료 "
                        f"(누적 후보 {len(pool)}개)")
        time.sleep(0.2)

    result = []
    for entry in pool.values():
        entry["category"] = max(entry["categories"], key=entry["categories"].get) if entry["categories"] else "기타"
        entry["source_count"] = len(entry["sources"])
        del entry["categories"]
        del entry["sources"]
        result.append(entry)

    result.sort(key=lambda e: e["mentions"], reverse=True)
    return result[:RAW_POOL_CAP]


def _lightweight_prefilter(raw_pool, keep_n=PREFILTER_KEEP_N):
    scored = []
    for entry in raw_pool:
        base = entry["mentions"] * 1.0
        base += entry["recency"] * 1.5
        base += 0.7 if entry["source_count"] >= 2 else 0.0
        base += 0.5 if is_profit_related(entry["keyword"]) else 0.0
        entry["prefilter_score"] = round(base, 2)
        scored.append(entry)
    scored.sort(key=lambda e: e["prefilter_score"], reverse=True)
    return scored[:keep_n]


def collect_candidates(open_api, progress_cb=None):
    raw_pool = _collect_raw_pool(open_api, progress_cb=progress_cb)
    if progress_cb:
        progress_cb(f"경량 필터 적용 중... (후보 {len(raw_pool)}개 -> 상위 {PREFILTER_KEEP_N}개 선별)")
    filtered = _lightweight_prefilter(raw_pool, keep_n=PREFILTER_KEEP_N)
    return filtered
