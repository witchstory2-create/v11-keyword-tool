# -*- coding: utf-8 -*-
"""
article_engine.py (v1.0)
기사 원문(articles) 정제·중복 제거·발췌 요약·화제어 추출 전담 모듈.

[설계 원칙]
- LLM/외부 AI API 사용 안 함. 표준 라이브러리(re, html, collections)만 사용.
- "발췌 재조합(extractive reuse)" 방식: 기사 제목/스니펫에서 키워드와
  관련된 문장/구절을 선별하여 재배열하는 근사적 요약을 만든다. 새로운
  문장을 창작하지 않는다.
- collector.py가 만든 articles=[{title, url, snippet, date}, ...] 구조를
  입력으로 받아, content_engine.py가 바로 쓸 수 있는 정제 결과를 돌려준다.
"""

import re
import html
from collections import Counter

HTML_TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?다요됨함음])\s+")

_PARTICLES = ["으로부터", "에서는", "에게서", "으로는", "에서", "으로",
              "에게", "까지", "부터", "이라", "라는", "이나", "보다",
              "처럼", "만큼", "은", "는", "이", "가", "을", "를",
              "의", "에", "도", "만", "과", "와", "로"]

_STOPWORDS = {"기사", "오늘", "이번", "지난", "관련", "내용", "확인",
              "발표", "위해", "대한", "통해", "따르면", "밝혔다", "전했다"}


def clean_html(text):
    """HTML 태그/엔티티를 제거하고 공백을 정규화한다."""
    if not text:
        return ""
    text = html.unescape(text)
    text = HTML_TAG_RE.sub("", text)
    text = WHITESPACE_RE.sub(" ", text).strip()
    return text


def normalize_article(article):
    """단일 article dict의 title/snippet을 정제한 새 dict를 반환한다."""
    return {
        "title": clean_html(article.get("title", "")),
        "url": article.get("url", ""),
        "snippet": clean_html(article.get("snippet", "")),
        "date": article.get("date", ""),
    }


def deduplicate_articles(articles, limit=5):
    """URL 기준 중복 제거 후 최대 limit개만 반환한다."""
    seen = set()
    result = []
    for a in articles:
        na = normalize_article(a)
        url = na["url"]
        if url and url in seen:
            continue
        if url:
            seen.add(url)
        result.append(na)
        if len(result) >= limit:
            break
    return result


def _strip_particle(word):
    for suf in _PARTICLES:
        if word.endswith(suf) and len(word) - len(suf) >= 2:
            return word[: -len(suf)]
    return word


def _split_sentences(text):
    if not text:
        return []
    parts = SENTENCE_SPLIT_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


def extract_key_sentences(articles, keyword, max_sentences=3):
    """
    articles의 title/snippet에서 keyword(구성 단어 포함)가 들어간 문장을
    우선순위로 선별한다. 새 문장을 생성하지 않고 원문에서 발췌만 한다.
    """
    if not articles:
        return []

    kw_tokens = [t for t in keyword.split(" ") if t]
    candidates = []

    for art in articles:
        title = art.get("title", "")
        snippet = art.get("snippet", "")
        if title:
            candidates.append(title)
        for sent in _split_sentences(snippet):
            candidates.append(sent)

    seen = set()
    uniq = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            uniq.append(c)

    scored = []
    for c in uniq:
        score = sum(1 for t in kw_tokens if t and t in c)
        scored.append((score, c))
    scored.sort(key=lambda x: x[0], reverse=True)

    key_sentences = [c for score, c in scored if score > 0][:max_sentences]
    if len(key_sentences) < max_sentences:
        for score, c in scored:
            if c not in key_sentences:
                key_sentences.append(c)
            if len(key_sentences) >= max_sentences:
                break

    return key_sentences[:max_sentences]


def summarize_articles(articles, keyword, max_sentences=3):
    """
    extract_key_sentences 결과를 이어붙인 발췌 요약 문자열을 만든다.
    기사가 없으면 빈 문자열을 반환한다(호출측에서 fallback 처리).
    """
    sentences = extract_key_sentences(articles, keyword, max_sentences=max_sentences)
    if not sentences:
        return ""
    normalized = []
    for s in sentences:
        s = s.strip()
        if s and s[-1] not in ".!?":
            s += "."
        normalized.append(s)
    return " ".join(normalized)


def extract_topics(articles, top_n=5):
    """
    articles의 title+snippet에서 2음절 이상 토큰의 빈도를 세어 상위
    top_n개를 화제어로 반환한다. 별도 사전 없이 통계적으로만 추출한다.
    """
    if not articles:
        return []

    counter = Counter()
    for art in articles:
        text = f"{art.get('title', '')} {art.get('snippet', '')}"
        for raw in text.split(" "):
            tok = _strip_particle(raw.strip())
            tok = re.sub(r"[^가-힣a-zA-Z0-9]", "", tok)
            if len(tok) < 2:
                continue
            if tok in _STOPWORDS:
                continue
            counter[tok] += 1

    return [tok for tok, _ in counter.most_common(top_n)]


if __name__ == "__main__":
    _sample_articles = [
        {"title": "전세보증금 반환대출 조건 완화 발표", "url": "https://example.com/1",
         "snippet": "최근 전세보증금 반환대출 신청이 급증하면서 은행권이 조건을 완화하고 있다. 대출 한도도 늘어난다.",
         "date": "2026-07-01"},
        {"title": "전세보증금 반환대출 신청 폭주", "url": "https://example.com/2",
         "snippet": "전세보증금 반환대출 신청자가 몰리면서 대기 기간이 길어지고 있다.",
         "date": "2026-07-02"},
    ]
    arts = deduplicate_articles(_sample_articles)
    print("정제된 articles:", arts)
    print("핵심문장:", extract_key_sentences(arts, "전세보증금 반환대출"))
    print("요약:", summarize_articles(arts, "전세보증금 반환대출"))
    print("화제어:", extract_topics(arts))
