# -*- coding: utf-8 -*-
"""
content_engine.py (v1.0)
키워드 + article_engine 결과를 바탕으로 제목(검색용/홈판용/혼합용), 개요,
FAQ, 초안을 생성하는 콘텐츠 생성 전담 모듈.

[설계 원칙]
- LLM 미사용. article_engine.py의 발췌 요약/화제어를 재료로 규칙 기반
  문장 조합만 수행한다.
- articles가 비어 있으면(뉴스 매칭이 없는 신규 발굴 키워드 등) 기존
  템플릿 방식으로 자동 전환(fallback)한다.
- 표준 라이브러리만 사용, PyInstaller 호환.
"""

import article_engine as ae

_HOME_HOOK_WORDS = ["꼭 확인하세요", "놓치면 후회", "지금 바로 확인",
                     "이렇게 달라졌습니다", "몰랐다면 손해"]
_RECENCY_WORDS = ["최신", "이번 달", "최근", "지금"]


def _dedupe_list(items):
    seen = set()
    out = []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out


def _prep(candidate):
    keyword = candidate.get("keyword", "")
    category = candidate.get("category", "")
    intent_word = candidate.get("intent_word") or ""
    articles = ae.deduplicate_articles(candidate.get("articles", []) or [])
    return keyword, category, intent_word, articles


# =========================================================================
# 1. 제목 생성 - 검색용 / 홈판용 / 혼합용
# =========================================================================
def make_search_titles(candidate, max_titles=3):
    """검색 유입용: 키워드 + intent_word를 그대로 살려 일치도를 우선한다."""
    keyword, category, intent_word, articles = _prep(candidate)
    titles = []

    if intent_word:
        titles.append(f"{keyword} {intent_word} 총정리")
        titles.append(f"{keyword} {intent_word}, 이것만 확인하세요")
    else:
        titles.append(f"{keyword} 총정리")
        titles.append(f"{keyword} 확인 방법 안내")

    topics = ae.extract_topics(articles, top_n=2) if articles else []
    if topics:
        titles.append(f"{keyword} {topics[0]} 관련 정리")
    else:
        titles.append(f"{category} {keyword} 안내")

    return _dedupe_list(titles)[:max_titles]


def make_home_titles(candidate, max_titles=3):
    """홈판(메인 노출)용: 감정적 어휘/최신성 어휘를 섞어 클릭을 유도한다."""
    keyword, category, intent_word, articles = _prep(candidate)
    titles = []
    hook1 = _HOME_HOOK_WORDS[0]
    hook2 = _HOME_HOOK_WORDS[1]
    recency = _RECENCY_WORDS[0]

    if articles:
        summary = ae.summarize_articles(articles, keyword, max_sentences=1)
        if summary:
            titles.append(f"{recency} 화제의 {keyword}, {hook1}")
        titles.append(f"{keyword} {intent_word or '이슈'}, {hook2}")
    else:
        titles.append(f"{recency} {keyword} 소식, {hook1}")
        titles.append(f"{keyword}, {hook2}")

    titles.append(f"{keyword} 지금 이렇게 달라졌습니다")
    return _dedupe_list(titles)[:max_titles]


def make_mix_titles(candidate, max_titles=3):
    """검색용과 홈판용 특징을 절반씩 섞은 혼합형 제목."""
    keyword, category, intent_word, articles = _prep(candidate)
    search_t = make_search_titles(candidate, max_titles=2)
    home_t = make_home_titles(candidate, max_titles=2)

    mixed = []
    for s, h in zip(search_t, home_t):
        left = s.split(" 총정리")[0].split(",")[0]
        right = h.split(",")[-1].strip()
        mixed.append(f"{left} - {right}")

    if intent_word:
        mixed.append(f"{keyword} {intent_word}, 최신 기준으로 정리했습니다")
    else:
        mixed.append(f"{keyword}, 최신 기준으로 정리했습니다")

    return _dedupe_list(mixed)[:max_titles]


# =========================================================================
# 2. 개요(outline) 생성
# =========================================================================
def make_outline(candidate):
    """
    글의 목차(섹션 제목 리스트)를 생성한다. articles가 있으면 화제어를
    반영하고, 없으면 기본 템플릿 목차로 대체한다.
    """
    keyword, category, intent_word, articles = _prep(candidate)
    outline = [f"{keyword}란 무엇인가"]

    if articles:
        topics = ae.extract_topics(articles, top_n=3)
        for t in topics:
            outline.append(f"{t} 관련 최신 내용")
    else:
        outline.append(f"{keyword} {intent_word or '기본'} 조건")

    outline.append(f"{keyword} {intent_word or '신청'} 방법")
    outline.append(f"{keyword} 관련 자주 묻는 질문")
    return _dedupe_list(outline)


# =========================================================================
# 3. FAQ 생성
# =========================================================================
def make_faq(candidate, max_items=3):
    """
    질문-답변 쌍을 생성한다. articles가 있으면 핵심 문장을 답변에 발췌
    반영하고, 없으면 기존 템플릿 답변으로 대체한다.
    """
    keyword, category, intent_word, articles = _prep(candidate)
    key_sentences = ae.extract_key_sentences(articles, keyword, max_sentences=max_items) if articles else []

    faqs = []
    q1 = f"{keyword}은 누가 신청할 수 있나요?"
    a1 = key_sentences[0] if len(key_sentences) > 0 else f"{keyword}은 관련 요건을 충족하는 대상자가 신청할 수 있습니다."
    faqs.append((q1, a1))

    q2 = f"{keyword} {intent_word or '신청'}은 어떻게 하나요?"
    a2 = key_sentences[1] if len(key_sentences) > 1 else f"{keyword} {intent_word or '신청'}은 관련 기관 홈페이지 또는 창구를 통해 진행할 수 있습니다."
    faqs.append((q2, a2))

    q3 = f"{keyword} 관련해서 최근 어떤 변화가 있었나요?"
    a3 = key_sentences[2] if len(key_sentences) > 2 else f"{keyword}과 관련된 세부 조건은 시기에 따라 달라질 수 있어 최신 공고 확인이 필요합니다."
    faqs.append((q3, a3))

    return faqs[:max_items]


# =========================================================================
# 4. 초안(draft) 생성
# =========================================================================
def make_draft(candidate):
    """
    개요 + 발췌 요약 + FAQ를 조합해 글 초안 전체 텍스트를 만든다.
    articles가 없을 때의 fallback은 make_outline/make_faq가 각자
    처리하므로, 여기서는 조합만 담당한다.
    """
    keyword, category, intent_word, articles = _prep(candidate)
    outline = make_outline(candidate)
    faqs = make_faq(candidate)

    lines = []
    lines.append(f"## {keyword} {intent_word or ''} 안내".strip())
    lines.append("")

    if articles:
        summary = ae.summarize_articles(articles, keyword, max_sentences=3)
        if summary:
            lines.append(summary)
            lines.append("")
    else:
        lines.append(f"{keyword}는 {category} 분야에서 최근 관심이 높아지고 있는 주제입니다.")
        lines.append("")

    for section in outline:
        lines.append(f"### {section}")
        lines.append(f"{section}에 대한 세부 내용을 정리했습니다.")
        lines.append("")

    lines.append("### 자주 묻는 질문 (FAQ)")
    for q, a in faqs:
        lines.append(f"Q. {q}")
        lines.append(f"A. {a}")
        lines.append("")

    return "\n".join(lines).strip()


if __name__ == "__main__":
    _sample_candidate = {
        "keyword": "전세보증금 반환대출",
        "category": "대출",
        "intent_word": "조건",
        "articles": [
            {"title": "전세보증금 반환대출 조건 완화 발표", "url": "https://example.com/1",
             "snippet": "최근 전세보증금 반환대출 신청이 급증하면서 은행권이 조건을 완화하고 있다. 대출 한도도 늘어난다.",
             "date": "2026-07-01"},
            {"title": "전세보증금 반환대출 신청 폭주", "url": "https://example.com/2",
             "snippet": "전세보증금 반환대출 신청자가 몰리면서 대기 기간이 길어지고 있다.",
             "date": "2026-07-02"},
        ],
    }
    _empty_candidate = {"keyword": "지원금 신청", "category": "지원금", "intent_word": "신청", "articles": []}

    print("=== articles 있음 ===")
    print("검색용:", make_search_titles(_sample_candidate))
    print("홈판용:", make_home_titles(_sample_candidate))
    print("혼합형:", make_mix_titles(_sample_candidate))
    print("개요:", make_outline(_sample_candidate))
    print("FAQ:", make_faq(_sample_candidate))
    print("초안:\n", make_draft(_sample_candidate))

    print("\n=== articles 없음 (fallback) ===")
    print("검색용:", make_search_titles(_empty_candidate))
    print("홈판용:", make_home_titles(_empty_candidate))
    print("초안:\n", make_draft(_empty_candidate))
