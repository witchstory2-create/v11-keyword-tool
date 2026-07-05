# scorer.py
# 뉴스 분석 + 전략 판단 + 제목 생성 (v6 통합 / GUI 호환)
# 외부 라이브러리 의존 없음 (표준 라이브러리만 사용)

import re
from collections import Counter

# ------------------------------------------------------------
# 0. 안전 유틸 (모든 타입 불일치 방어 지점)
# ------------------------------------------------------------

def _to_text(x):
    """list/tuple/None 등 어떤 타입이 와도 안전한 문자열로 정규화"""
    if x is None:
        return ""
    if isinstance(x, (list, tuple)):
        return " ".join(_to_text(i) for i in x if i is not None)
    if isinstance(x, dict):
        return " ".join(_to_text(v) for v in x.values())
    return str(x)


def _join(parts, sep=" "):
    """리스트를 문자열로 합칠 때는 무조건 이 함수만 사용 (+ 연산 금지)"""
    return sep.join(_to_text(p) for p in parts if p is not None and _to_text(p) != "")


STOPWORDS = set("""
이 그 저 것 등 및 를 은 는 가 의 에 에서 으로 로 와 과 도 만 까지
있다 없다 하다 되다 한다 됩니다 합니다 위해 대한 통해 관련 지난 오늘
""".split())

SEARCH_INTENT_SIGNALS = [
    "신청", "조회", "방법", "자격", "조건", "대상", "기한", "마감",
    "얼마", "언제", "how", "신청방법", "받는법", "대상자",
]

CTR_SIGNALS = [
    "충격", "논란", "결국", "터졌다", "발칵", "초유", "역대", "최초",
    "긴급", "속보", "단독", "폭등", "폭락", "화제",
]

ACTION_WORDS = ["신청", "접수", "지급", "환급", "마감", "시작", "종료", "연장", "발표", "인상", "인하"]

EVENT_VERBS = ["시작", "마감", "종료", "연장", "발표", "지급", "인상", "인하", "확대", "축소"]


# ------------------------------------------------------------
# 1. 키워드 / 이벤트 / 의도 추출
# ------------------------------------------------------------

def extract_keywords(title, summary, topn=6):
    title = _to_text(title)
    summary = _to_text(summary)

    tokens = re.findall(r"[가-힣A-Za-z0-9]+", title + " " + summary)
    tokens = [t for t in tokens if len(t) > 1 and t not in STOPWORDS]

    weighted = tokens + re.findall(r"[가-힣A-Za-z0-9]+", title) * 2
    weighted = [t for t in weighted if len(t) > 1 and t not in STOPWORDS]

    counter = Counter(weighted)
    ranked = [w for w, _ in counter.most_common(topn)]
    return ranked


def split_main_related(keywords):
    if not keywords:
        return "", []
    main_keyword = keywords[0]
    related_keywords = keywords[1:] if len(keywords) > 1 else []
    return main_keyword, related_keywords


def extract_action_intents(text):
    text = _to_text(text)
    found = [w for w in ACTION_WORDS if w in text]
    return found


def extract_event_verb(text):
    text = _to_text(text)
    for v in EVENT_VERBS:
        if v in text:
            return v
    return ""


def build_event(main_keyword, action_intents, text):
    action = action_intents[0] if action_intents else ""
    verb = extract_event_verb(text)

    parts = [p for p in [main_keyword, action, verb] if p]
    if not parts:
        return _to_text(main_keyword)
    return _join(parts)


def extract_detail(text):
    """나이/기한/날짜/금액 등 구체 정보 추출"""
    text = _to_text(text)
    patterns = [
        r"\d+세", r"\d+월\s?\d+일", r"\d+월말", r"\d+만원", r"\d+억원",
        r"\d+%", r"\d+년",
    ]
    found = []
    for p in patterns:
        m = re.findall(p, text)
        found.extend(m)
    return found[:3]


# ------------------------------------------------------------
# 2. 전략 판단 (검색형 / 홈판형 / 혼합형)
# ------------------------------------------------------------

def judge_strategy(text):
    text = _to_text(text)
    search_hits = sum(1 for w in SEARCH_INTENT_SIGNALS if w in text)
    ctr_hits = sum(1 for w in CTR_SIGNALS if w in text)

    if search_hits > ctr_hits:
        return "검색형"
    elif ctr_hits > search_hits:
        return "홈판형"
    else:
        return "혼합형"


# ------------------------------------------------------------
# 3. 핵심 요약 / 질문 / 목차
# ------------------------------------------------------------

def build_core_summary(title, summary, max_sentences=3):
    summary = _to_text(summary)
    title = _to_text(title)

    sentences = re.split(r"(?<=[.!?다요])\s+", summary)
    sentences = [s.strip() for s in sentences if s.strip()]

    if not sentences:
        return title

    picked = sentences[:max_sentences]
    return _join(picked)


def generate_questions(main_keyword, action_intents, detail, related_keywords):
    questions = []

    if main_keyword:
        questions.append(f"{main_keyword}란 무엇인가요?")

    if action_intents:
        action = action_intents[0]
        questions.append(f"{main_keyword} {action} 방법은 어떻게 되나요?")
        questions.append(f"{main_keyword} {action} 대상은 누구인가요?")

    if detail:
        questions.append(f"{main_keyword} 관련 {_to_text(detail[0])} 기준은 어떻게 되나요?")

    if related_keywords:
        questions.append(f"{main_keyword}와 {related_keywords[0]}의 차이는 무엇인가요?")

    # 최소 3개, 최대 5개로 보정
    if len(questions) < 3:
        questions.append(f"{main_keyword} 관련 주의할 점은 무엇인가요?")

    return questions[:5]


def generate_outline(main_keyword, action_intents, questions):
    outline = []
    outline.append(f"{main_keyword} 핵심 내용 정리")

    if action_intents:
        outline.append(f"{main_keyword} {action_intents[0]} 대상 및 조건")
        outline.append(f"{main_keyword} {action_intents[0]} 방법 및 절차")

    for q in questions[:2]:
        outline.append(q.replace("?", "") + " 정리")

    outline.append("마무리 및 유의사항")

    # 중복 제거하면서 순서 유지
    seen = set()
    deduped = []
    for item in outline:
        if item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped[:6]


# ------------------------------------------------------------
# 4. 제목 생성 (검색형 / 클릭베이트 / 혼합형 각 5개)
# ------------------------------------------------------------

def generate_titles(main_keyword, related_keywords, action_intents, detail, event, strategy):
    action = action_intents[0] if action_intents else "정리"
    detail_str = _to_text(detail[0]) if detail else ""
    related_str = related_keywords[0] if related_keywords else ""

    seo_titles = [
        f"{main_keyword} {action} 방법 총정리",
        f"{main_keyword} {action} 대상 및 조건 안내",
        f"{event} 신청 자격 확인하기",
        f"{main_keyword} {detail_str} 기준 정리".strip(),
        f"{main_keyword} {action} 절차 A to Z",
    ]

    clickbait_titles = [
        f"{main_keyword}, 지금 안 하면 손해",
        f"몰랐다면 지금 확인하세요, {main_keyword}",
        f"{event}, 이렇게 달라집니다",
        f"{main_keyword} 놓치면 후회하는 이유",
        f"{main_keyword} 화제 된 이유",
    ]

    hybrid_titles = [
        f"{main_keyword} {action} 방법, 이것만 알면 끝",
        f"{event}, {related_str} 함께 챙기세요".strip(),
        f"{main_keyword} {detail_str} 기준 이렇게 바뀝니다".strip(),
        f"{main_keyword} {action} 꿀팁 모음",
        f"{main_keyword} 지금 확인해야 하는 이유",
    ]

    return {
        "seo_titles": seo_titles,
        "clickbait_titles": clickbait_titles,
        "hybrid_titles": hybrid_titles,
    }


# ------------------------------------------------------------
# 5. 핵심 함수: analyze_news (반환 구조 고정)
# ------------------------------------------------------------

def analyze_news(news_title, news_summary):
    # --- 입력 정규화 (타입 방어 1차 지점) ---
    news_title = _to_text(news_title)
    news_summary = _to_text(news_summary)

    text = _join([news_title, news_summary])  # "+" 대신 안전한 join만 사용

    keywords = extract_keywords(news_title, news_summary)
    main_keyword, related_keywords = split_main_related(keywords)

    action_intents = extract_action_intents(text)
    detail = extract_detail(text)
    event = build_event(main_keyword, action_intents, text)

    core_summary = build_core_summary(news_title, news_summary)
    questions = generate_questions(main_keyword, action_intents, detail, related_keywords)
    outline = generate_outline(main_keyword, action_intents, questions)

    strategy = judge_strategy(text)
    titles = generate_titles(main_keyword, related_keywords, action_intents, detail, event, strategy)

    return {
        "analysis": {
            "core_summary": core_summary,
            "event": event,
            "questions": questions,
            "outline": outline,
            "main_keyword": main_keyword,
            "related_keywords": related_keywords,
            "action_intents": action_intents,
        },
        "strategy": strategy,
        "titles": titles,
    }


# ------------------------------------------------------------
# 6. GUI 호환 wrapper: score_candidates
#    - GUI가 어떤 인자를 넘기든 TypeError 없이 흡수
#    - candidates: list[dict] 형태 (title/summary 필드가 str이든 list든 안전 처리)
# ------------------------------------------------------------

def _extract_title_summary(candidate):
    """candidate dict에서 title/summary를 안전하게 뽑아 문자열로 정규화"""
    if isinstance(candidate, dict):
        title = candidate.get("title") or candidate.get("news_title") or ""
        summary = (
            candidate.get("summary")
            or candidate.get("content")
            or candidate.get("news_summary")
            or ""
        )
    else:
        # candidate가 (title, summary) 튜플 등으로 오는 경우 방어
        title = candidate[0] if len(candidate) > 0 else ""
        summary = candidate[1] if len(candidate) > 1 else ""

    return _to_text(title), _to_text(summary)


def score_candidates(candidates=None, log=None, progress_callback=None, *args, **kwargs):
    """
    GUI(app.py)가 어떤 방식으로 호출해도 TypeError 없이 동작하도록
    *args, **kwargs로 모든 추가 인자를 흡수.
    candidates: list[dict] (필수, 각 dict는 title/summary 포함)
    log: 콜러블(로그 함수) 또는 None
    progress_callback: 콜러블(진행률 함수) 또는 None
    """
    def _log(msg):
        if callable(log):
            try:
                log(msg)
            except Exception:
                pass

    def _progress(done, total):
        if callable(progress_callback):
            try:
                progress_callback(done, total)
            except Exception:
                pass

    if candidates is None:
        candidates = []

    total = len(candidates)
    results = []

    for idx, candidate in enumerate(candidates, start=1):
        title, summary = _extract_title_summary(candidate)

        try:
            result = analyze_news(title, summary)
        except Exception as e:
            # 개별 후보 실패가 전체 배치를 죽이지 않도록 격리
            _log(f"[score_candidates] {idx}/{total} 분석 실패: {e}")
            result = {
                "analysis": {
                    "core_summary": "",
                    "event": "",
                    "questions": [],
                    "outline": [],
                    "main_keyword": "",
                    "related_keywords": [],
                    "action_intents": [],
                },
                "strategy": "unknown",
                "titles": {"seo_titles": [], "clickbait_titles": [], "hybrid_titles": []},
                "error": str(e),
            }

        merged = dict(candidate) if isinstance(candidate, dict) else {"title": title, "summary": summary}
        merged["analysis"] = result["analysis"]
        merged["strategy"] = result["strategy"]
        merged["titles"] = result["titles"]

        results.append(merged)

        _log(f"[score_candidates] {idx}/{total} 완료: {title[:30]}")
        _progress(idx, total)

    return results
