"""
scorer.py (v6 - Event -> Question Generation 구조)
---------
파이프라인:
뉴스 -> 핵심요약(2~3문장) -> 메인/연관 키워드 -> 사건(event) 추출
     -> 질문 생성(3~5개, 신호 기반 조건부) -> 검색의도 판단
     -> 검색형 제목(질문 기반) / 홈판형 제목(관심유도형) / 혼합형 제목(질문+유도)
     -> 목차(outline) 생성

지키는 원칙:
- Revenue Score, Confidence Score, 복잡한 점수 모델 없음
- 대형 카테고리 사전 없음, 표준 라이브러리만 사용
- analyze_news() 반환 구조(analysis/strategy/titles) 고정
"""

import re
from collections import Counter

# -----------------------------
# 1. 규칙/사전
# -----------------------------

STOPWORDS = {
    "은", "는", "이", "가", "을", "를", "의", "에", "에서", "으로", "로",
    "와", "과", "도", "만", "부터", "까지", "하다", "했다", "한다", "된다",
    "있다", "없다", "이다", "그", "그리고", "하지만", "이번", "관련", "위해",
}

SEARCH_INTENT_SIGNALS = [
    "방법", "신청", "조건", "대상", "기준", "총정리", "확인", "자격",
    "지원금", "환급", "세금", "할인", "가격", "비교", "기한", "서류",
]
CTR_SIGNALS = [
    "논란", "반전", "결국", "충격", "사망", "폭행", "이혼", "열애",
    "파문", "폭로", "긴급", "발칵", "역대급", "속보", "단독",
]
ACTION_SIGNALS = ["접수", "신청", "등록", "발급", "예약", "조회", "지급", "환급", "계산"]
EVENT_VERB_SIGNALS = [
    "시작", "마감", "확정", "발표", "인상", "인하",
    "확대", "축소", "연장", "종료", "개시", "중단",
]
DETAIL_PATTERNS = [
    r"만?\s*\d+세\s*[~-]\s*\d+세",
    r"\d+년\s*\d+월\s*\d+일",
    r"\d+월\s*(초|중|말)?까지",
    r"\d+%",
    r"\d+[,\d]*\s?만?\s?원",
]

QUESTION_SPECS = [
    {"question": "{event}, 신청 대상은 누구일까?",
     "seo_title": "{event} 신청 대상 총정리, 나도 받을 수 있을까",
     "requires": "always"},
    {"question": "{event}, 신청 방법은 어떻게 될까?",
     "seo_title": "{event} 신청 방법 총정리 (절차·서류 안내)",
     "requires": "always"},
    {"question": "{event}, 신청 시 주의할 점은 무엇일까?",
     "seo_title": "{event} 신청 전 반드시 확인해야 할 주의사항",
     "requires": "always"},
    {"question": "{event}, 언제까지 확인해야 할까?",
     "seo_title": "{event} 신청 기한{detail_suffix}, 놓치면 못 받는다",
     "requires": "detail"},
    {"question": "{event}, {related}와는 어떤 관련이 있을까?",
     "seo_title": "{event}, {related} 함께 확인하면 좋은 정보",
     "requires": "related"},
]

CLICKBAIT_TEMPLATES = {
    "soft": [
        "{event}, 모르면 손해 보는 이유",
        "{event}, 놓치면 못 받는다 지금 확인",
        "{event}, 다들 헷갈리는 이 부분",
        "{event}, 신청 안 하면 후회하는 이유",
        "{event}, 이 조건 모르고 지나치면 손해",
    ],
    "strong": [
        "{event}, 알고 보니 이런 반전이 있었다",
        "{event} 소식에 다들 이렇게 반응했다",
        "결국 터졌다... {event}의 실제 배경",
        "{event}, 이 사실 알고 계셨나요?",
        "{event} 논란, 어디까지 이어질까",
    ],
}

HYBRID_TEMPLATES = {
    "soft": [
        "{event} 신청 대상은 누구일까? 놓치면 후회하는 이유",
        "{event} 신청 방법, 궁금하다면 지금 확인",
        "{event} 주의할 점, 모르면 손해 보는 이유",
        "{event} 신청 기한 임박, 지금 확인해야{detail_suffix}",
        "{event}, {related}까지 한번에 정리 (놓치지 마세요)",
    ],
    "strong": [
        "{event} 신청 대상 논란, 나도 해당될까?",
        "{event} 신청 방법, 다들 이렇게 반응했다",
        "{event} 주의할 점, 알고 보니 반전 있었다",
        "{event} 마감 임박, 몰랐다면 충격{detail_suffix}",
        "{event}, {related} 관련 진실은 이것",
    ],
}


# -----------------------------
# 2. 정제
# -----------------------------

def _tokenize(text: str):
    tokens = re.findall(r"[가-힣A-Za-z0-9]+", text)
    return [t for t in tokens if len(t) > 1 and t not in STOPWORDS]


def extract_keywords(title: str, summary: str, top_n: int = 6):
    title_tokens, summary_tokens = _tokenize(title), _tokenize(summary)
    all_tokens = title_tokens + summary_tokens

    unigram = Counter(summary_tokens)
    for t in title_tokens:
        unigram[t] += 2

    bigram = Counter()
    for a, b in zip(all_tokens, all_tokens[1:]):
        bigram[f"{a} {b}"] += 1
    for a, b in zip(title_tokens, title_tokens[1:]):
        bigram[f"{a} {b}"] += 2

    keywords = [kw for kw, c in bigram.items() if c >= 2][:top_n]
    if len(keywords) < top_n:
        used = {w for kw in keywords for w in kw.split()}
        for word, _ in unigram.most_common(top_n * 2):
            if word not in used and word not in keywords:
                keywords.append(word)
            if len(keywords) >= top_n:
                break
    return keywords[:top_n]


def build_core_summary(summary: str, max_sentences: int = 3) -> str:
    cleaned = summary.strip()
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned) if s.strip()]
    if not sentences:
        return cleaned

    picked = sentences[:max_sentences] if len(sentences) >= 2 else sentences[:1]
    result = []
    for s in picked:
        if not s.endswith((".", "!", "?")):
            s += "."
        result.append(s)
    return " ".join(result)


def extract_event(text: str, main_keyword: str, action_hits: list) -> str:
    components = []
    if main_keyword:
        components.append(main_keyword)
    if action_hits:
        components.append(action_hits[0])
    for verb in EVENT_VERB_SIGNALS:
        if verb in text:
            components.append(verb)
            break

    seen, event_parts = set(), []
    for c in components:
        if c not in seen:
            seen.add(c)
            event_parts.append(c)
    return " ".join(event_parts) if event_parts else (main_keyword or "이 뉴스")


def extract_signals(text: str):
    search_hits = [w for w in SEARCH_INTENT_SIGNALS if w in text]
    ctr_hits = [w for w in CTR_SIGNALS if w in text]
    action_hits = [w for w in ACTION_SIGNALS if w in text]
    return search_hits, ctr_hits, action_hits


def extract_detail(text: str):
    for pattern in DETAIL_PATTERNS:
        match = re.search(pattern, text)
        if match:
            return match.group()
    return None


def pick_topic(title: str, keywords: list):
    for kw in keywords:
        if " " in kw and len(kw) <= 15:
            return kw
    cleaned = title.strip().rstrip(".")
    return cleaned if len(cleaned) <= 20 else (" ".join(keywords[:2]) or cleaned[:20])


def generate_questions(event: str, related: list, has_detail: bool) -> list:
    related_word = related[0] if related else None
    questions = []
    for spec in QUESTION_SPECS:
        req = spec["requires"]
        include = (
            req == "always"
            or (req == "detail" and has_detail)
            or (req == "related" and related_word)
        )
        if include:
            questions.append(spec["question"].format(event=event, related=related_word or event))
    return questions


def build_outline(event: str, related: list, has_detail: bool) -> list:
    outline = [
        f"{event}, 무엇이 달라지나 (개요)",
        "신청 대상 및 조건",
        "신청 방법 및 절차",
    ]
    if has_detail:
        outline.append("신청 기간 및 주의사항")
    if related:
        outline.append(f"{related[0]} 함께 확인하기")
    outline.append("마무리 및 핵심 요약")
    return outline


# -----------------------------
# 3. 전략 판단
# -----------------------------

def classify_strategy(search_hits: list, ctr_hits: list):
    if len(search_hits) > len(ctr_hits):
        s_type, reason = "검색형", f"정보 탐색 신호 {search_hits} 우세, 화제성 신호 {ctr_hits}"
    elif len(ctr_hits) > len(search_hits):
        s_type, reason = "홈판형", f"화제성 신호 {ctr_hits} 우세, 정보 탐색 신호 {search_hits}"
    else:
        s_type, reason = "혼합형", f"검색 신호({search_hits})와 화제성 신호({ctr_hits})가 비슷함"

    return {
        "type": s_type, "reason": reason,
        "search_signals_found": search_hits, "ctr_signals_found": ctr_hits,
    }


# -----------------------------
# 4. 제목 생성
# -----------------------------

def build_seo_titles(event: str, related: list, detail) -> list:
    detail_suffix = f" ({detail} 기준)" if detail else ""
    related_word = related[0] if related else event
    return [
        spec["seo_title"].format(event=event, related=related_word, detail_suffix=detail_suffix)
        for spec in QUESTION_SPECS
    ]


def build_clickbait_titles(event: str, tone: str) -> list:
    return [t.format(event=event) for t in CLICKBAIT_TEMPLATES[tone]]


def build_hybrid_titles(event: str, related: list, detail, tone: str) -> list:
    detail_suffix = f" ({detail} 기준)" if detail else ""
    related_word = related[0] if related else event
    return [
        t.format(event=event, related=related_word, detail_suffix=detail_suffix)
        for t in HYBRID_TEMPLATES[tone]
    ]


def generate_titles(event: str, related: list, detail, search_hits: list, ctr_hits: list) -> dict:
    tone = "strong" if len(ctr_hits) > len(search_hits) else "soft"
    return {
        "seo_titles": build_seo_titles(event, related, detail),
        "clickbait_titles": build_clickbait_titles(event, tone),
        "hybrid_titles": build_hybrid_titles(event, related, detail, tone),
    }


# -----------------------------
# 5. 메인 분석 함수
# -----------------------------

def analyze_news(news_title: str, news_summary: str) -> dict:
    text = news_title + " " + news_summary

    keywords = extract_keywords(news_title, news_summary)
    main_keyword = keywords[0] if keywords else None
    related_keywords = keywords[1:] if len(keywords) > 1 else []
    topic = pick_topic(news_title, keywords)

    search_hits, ctr_hits, action_hits = extract_signals(text)
    detail = extract_detail(text)
    core_summary = build_core_summary(news_summary)
    event = extract_event(text, main_keyword, action_hits)

    questions = generate_questions(event, related_keywords, bool(detail))
    outline = build_outline(event, related_keywords, bool(detail))

    strategy = classify_strategy(search_hits, ctr_hits)
    titles = generate_titles(event, related_keywords, detail, search_hits, ctr_hits)

    return {
        "analysis": {
            "summary": news_summary.strip(),
            "core_summary": core_summary,
            "keywords": keywords,
            "topic": topic,
            "main_keyword": main_keyword,
            "related_keywords": related_keywords,
            "action_intents": action_hits,
            "event": event,
            "questions": questions,
            "outline": outline,
        },
        "strategy": strategy,
        "titles": titles,
    }


# -----------------------------
# 6. 실행 예시
# -----------------------------

if __name__ == "__main__":
    import json

    sample_title = "정부, 청년 월세 지원금 신청 접수 시작"
    sample_summary = (
        "국토교통부는 오늘부터 청년 월세 지원금 신청 접수를 시작한다고 밝혔다. "
        "지원 대상은 만 19세~34세 무주택 청년으로, 신청은 정부24 홈페이지에서 가능하다. "
        "지원 기간은 오는 8월 말까지다."
    )
    print(json.dumps(analyze_news(sample_title, sample_summary), ensure_ascii=False, indent=2))

def score_candidates(news_title, news_summary):
    return analyze_news(news_title, news_summary)
