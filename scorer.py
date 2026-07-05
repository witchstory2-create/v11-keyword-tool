# scorer.py
# ------------------------------------------------------------
# 구조: collector -> score_candidates (schema adapter)
#                 -> analyze_news (pure text processing + type guard)
# 목표: TypeError(list+str) 완전 방지 + schema mismatch 로깅
# 외부 라이브러리 사용 없음 (표준 라이브러리만)
# ------------------------------------------------------------

import re
from collections import Counter


# ============================================================
# 0. TYPE NORMALIZATION (모든 함수가 공유하는 최종 안전장치)
# ============================================================

def _to_text(x):
    """
    어떤 타입이 와도 안전한 문자열로 변환.
    - None -> ""
    - list/tuple -> 공백으로 join
    - dict -> value들을 공백으로 join
    - 그 외 -> str() 변환
    스키마 지식은 전혀 없음. 오직 "타입"만 다룸.
    """
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    if isinstance(x, (list, tuple)):
        return " ".join(_to_text(i) for i in x if i is not None)
    if isinstance(x, dict):
        return " ".join(_to_text(v) for v in x.values())
    return str(x)


def _join(parts, sep=" "):
    """리스트를 문자열로 합칠 때 항상 이 함수만 사용 ('+' 직접 연산 금지)"""
    return sep.join(_to_text(p) for p in parts if p is not None and _to_text(p) != "")


# ============================================================
# 1. SCHEMA ADAPTER: collector dict -> (title, summary)
#    스키마 지식은 오직 이 함수만 가진다.
#    실패 시 반드시 로그로 남긴다 (침묵 실패 금지).
# ============================================================

TITLE_KEYS = ["title", "news_title", "제목", "headline", "subject", "news_headline"]
SUMMARY_KEYS = ["summary", "content", "news_summary", "본문", "요약",
                "body", "text", "description", "article", "news_content"]


def _first_present(d, keys):
    """dict에서 keys 순서대로 먼저 발견되는 non-empty 값을 반환. 없으면 None."""
    for k in keys:
        if k in d and d[k] not in (None, "", [], {}):
            return d[k]
    return None


def _extract_title_summary(candidate, idx=None, log=None):
    """
    collector가 만든 candidate(dict 또는 그 외 형태)에서
    title/summary를 최대한 안전하게 추출하는 유일한 스키마 어댑터.

    - dict가 아니면: 통째로 title로 취급 (summary는 빈 값)
    - dict면: TITLE_KEYS / SUMMARY_KEYS 순서로 탐색
    - 못 찾으면 log에 실제 키 목록을 반드시 남김 (누락 침묵 금지)
    - 반환값은 항상 (title_raw, summary_raw) - 타입 변환은 하지 않음
      (타입 변환은 analyze_news의 책임)
    """
    def _log(msg):
        if callable(log):
            try:
                log(msg)
            except Exception:
                pass

    tag = f"[{idx}]" if idx is not None else ""

    if not isinstance(candidate, dict):
        _log(f"[schema]{tag} candidate가 dict가 아님 (type={type(candidate).__name__}). 원본을 title로 사용.")
        return candidate, None

    title_raw = _first_present(candidate, TITLE_KEYS)
    summary_raw = _first_present(candidate, SUMMARY_KEYS)

    if title_raw is None:
        _log(f"[schema]{tag} title 필드를 찾지 못함. 실제 키: {list(candidate.keys())}")
    if summary_raw is None:
        _log(f"[schema]{tag} summary 필드를 찾지 못함. 실제 키: {list(candidate.keys())}")

    return title_raw, summary_raw


# ============================================================
# 2. ANALYZER 내부 로직 (기존 로직 유지, 변경 최소화)
# ============================================================

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


def extract_keywords(title, summary, topn=6):
    tokens = re.findall(r"[가-힣A-Za-z0-9]+", title + " " + summary)
    tokens = [t for t in tokens if len(t) > 1 and t not in STOPWORDS]

    weighted = tokens + re.findall(r"[가-힣A-Za-z0-9]+", title) * 2
    weighted = [t for t in weighted if len(t) > 1 and t not in STOPWORDS]

    counter = Counter(weighted)
    return [w for w, _ in counter.most_common(topn)]


def split_main_related(keywords):
    if not keywords:
        return "", []
    return keywords[0], keywords[1:] if len(keywords) > 1 else []


def extract_action_intents(text):
    return [w for w in ACTION_WORDS if w in text]


def extract_event_verb(text):
    for v in EVENT_VERBS:
        if v in text:
            return v
    return ""


def build_event(main_keyword, action_intents, text):
    action = action_intents[0] if action_intents else ""
    verb = extract_event_verb(text)
    parts = [p for p in [main_keyword, action, verb] if p]
    return _join(parts) if parts else main_keyword


def extract_detail(text):
    patterns = [r"\d+세", r"\d+월\s?\d+일", r"\d+월말", r"\d+만원", r"\d+억원", r"\d+%", r"\d+년"]
    found = []
    for p in patterns:
        found.extend(re.findall(p, text))
    return found[:3]


def judge_strategy(text):
    search_hits = sum(1 for w in SEARCH_INTENT_SIGNALS if w in text)
    ctr_hits = sum(1 for w in CTR_SIGNALS if w in text)
    if search_hits > ctr_hits:
        return "검색형"
    elif ctr_hits > search_hits:
        return "홈판형"
    return "혼합형"


def build_core_summary(title, summary, max_sentences=3):
    sentences = re.split(r"(?<=[.!?다요])\s+", summary)
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return title
    return _join(sentences[:max_sentences])


def generate_questions(main_keyword, action_intents, detail, related_keywords):
    questions = []
    if main_keyword:
        questions.append(f"{main_keyword}란 무엇인가요?")
    if action_intents:
        action = action_intents[0]
        questions.append(f"{main_keyword} {action} 방법은 어떻게 되나요?")
        questions.append(f"{main_keyword} {action} 대상은 누구인가요?")
    if detail:
        questions.append(f"{main_keyword} 관련 {detail[0]} 기준은 어떻게 되나요?")
    if related_keywords:
        questions.append(f"{main_keyword}와 {related_keywords[0]}의 차이는 무엇인가요?")
    if len(questions) < 3:
        questions.append(f"{main_keyword} 관련 주의할 점은 무엇인가요?")
    return questions[:5]


def generate_outline(main_keyword, action_intents, questions):
    outline = [f"{main_keyword} 핵심 내용 정리"]
    if action_intents:
        outline.append(f"{main_keyword} {action_intents[0]} 대상 및 조건")
        outline.append(f"{main_keyword} {action_intents[0]} 방법 및 절차")
    for q in questions[:2]:
        outline.append(q.replace("?", "") + " 정리")
    outline.append("마무리 및 유의사항")

    seen, deduped = set(), []
    for item in outline:
        if item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped[:6]


def generate_titles(main_keyword, related_keywords, action_intents, detail, event, strategy):
    action = action_intents[0] if action_intents else "정리"
    detail_str = detail[0] if detail else ""
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
    return {"seo_titles": seo_titles, "clickbait_titles": clickbait_titles, "hybrid_titles": hybrid_titles}


# ============================================================
# 3. analyze_news: PURE FUNCTION + MINIMAL TYPE GUARD
#    - 스키마 해석 없음 (title/summary가 무엇을 의미하는지는 몰라도 됨)
#    - 오직 "문자열이 아니면 문자열로 만든다"는 타입 방어만 수행
#    - 이 한 줄의 방어만으로 TypeError는 이 함수 내부에서 원천적으로 불가능
# ============================================================

def analyze_news(news_title, news_summary):
    # --- 타입 가드 (스키마 지식 없음, 순수 타입 방어) ---
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


# ============================================================
# 4. score_candidates: ENTRY POINT (schema adapter 호출부)
#    - GUI/runner가 어떤 방식으로 호출해도 안전하도록
#      candidates 뒤의 위치 인자는 *args로 흡수
#    - log/progress_callback은 키워드 전용으로 고정
#      (이전에 발생했던 'multiple values for log' 문제 재발 방지)
# ============================================================

def score_candidates(candidates=None, *args, log=None, progress_callback=None, **kwargs):
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
    empty_title_count = 0
    empty_summary_count = 0

    for idx, candidate in enumerate(candidates, start=1):
        # --- 1) schema adapter: collector 구조 해석 (여기서만 스키마를 안다) ---
        title_raw, summary_raw = _extract_title_summary(candidate, idx=idx, log=_log)

        if title_raw is None:
            empty_title_count += 1
        if summary_raw is None:
            empty_summary_count += 1

        # --- 2) analyze_news 호출: pure function, 여기서 최종 타입 가드가 다시 걸림 ---
        try:
            result = analyze_news(title_raw, summary_raw)
        except Exception as e:
            # analyze_news가 pure function + type guard이므로 이 경로는
            # 이론상 거의 발생하지 않지만, 예기치 못한 예외까지 완전히 격리한다.
            _log(f"[score_candidates] {idx}/{total} 분석 실패(예상 외 예외): {e}")
            result = {
                "analysis": {
                    "core_summary": "", "event": "", "questions": [], "outline": [],
                    "main_keyword": "", "related_keywords": [], "action_intents": [],
                },
                "strategy": "unknown",
                "titles": {"seo_titles": [], "clickbait_titles": [], "hybrid_titles": []},
                "error": str(e),
            }

        merged = dict(candidate) if isinstance(candidate, dict) else {"raw": candidate}
        merged["analysis"] = result["analysis"]
        merged["strategy"] = result["strategy"]
        merged["titles"] = result["titles"]

        results.append(merged)

        title_preview = _to_text(title_raw)[:30]
        _log(f"[score_candidates] {idx}/{total} 완료: {title_preview}")
        _progress(idx, total)

    if empty_title_count or empty_summary_count:
        _log(
            f"[score_candidates] 스키마 누락 요약: "
            f"title 누락 {empty_title_count}건 / summary 누락 {empty_summary_count}건 "
            f"(총 {total}건 중)"
        )

    return results
