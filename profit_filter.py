# -*- coding: utf-8 -*-
"""
profit_filter.py (v18.2) - 수익형 여부 판단 및 카테고리 가중치 부여 전담

[이 파일의 역할]
- collector.py Discovery 결과(keyword, category, mentions, articles, recency 등)를 입력으로 받는다.
- 다음 네 가지를 판단하여 각 후보에 정보를 추가한다.
  1) 수익형 카테고리 소속 여부 재확인
  2) 카테고리별 광고 단가(CPC)/전환율 근사치를 반영한 CategoryWeight 부여
  3) 검색의도(신청/조회/계산/방법/조건/환급/할인 등) 포함 여부 판별
  4) 제외 카테고리(정치/연예/스포츠/국제/일반사회/날씨 등)에만 속한 후보는 완전히 탈락시킴

[이 파일이 하지 않는 일 - 다른 파일의 책임]
- 뉴스/기사 수집 -> collector.py
- API 호출(검색량/문서수/DataLab) -> naver_search_api.py
- 최종 점수 계산, 등급(TOP5/TOP10/보류/위험) 분류 -> scorer.py (이 파일은 category_weight 등
  '재료'만 제공하고, 최종 점수식에 어떻게 반영할지는 scorer.py가 결정한다)
- 화면 표시 -> app.py

[핵심 설계]
- '반도체'처럼 검색량/CPC는 높아도 검색 의도가 정보성/투자성/뉴스성이 혼재되어
  블로그 애드포스트 수익 효율이 떨어지는 카테고리는 낮은 가중치(0.7)를 부여한다.
- '환급', '지원금'처럼 신청/조회 등 명확한 행동 의도가 있는 카테고리는 높은 가중치(1.5)를 부여한다.
- '정치', '연예', '스포츠', '국제'처럼 애드포스트 수익과 거의 무관한 카테고리는 후보 자체를 탈락시킨다.
"""

# ------------------------------------------------------------------
# 카테고리별 가중치 (블로그 애드포스트 수익 관점의 CPC/전환율 근사치)
# 값이 클수록 "오늘 써서 돈이 될 가능성이 높은" 카테고리라는 의미.
# ------------------------------------------------------------------
CATEGORY_WEIGHTS = {
    "환급": 1.5,
    "지원금": 1.5,
    "보험": 1.4,
    "대출": 1.4,
    "세금": 1.4,
    "청약": 1.3,
    "연금": 1.3,
    "카드": 1.2,
    "부동산": 1.2,
    "자동차": 1.1,
    "반도체": 0.7,
    "정치": 0.2,
    "국제": 0.2,
    "연예": 0.1,
    "스포츠": 0.1,
    "사회": 0.3,
    "날씨": 0.2,
}

# 목록에 없는(=collector.py의 CATEGORY_SEEDS에 없는 새 카테고리) 경우 기본값
DEFAULT_WEIGHT = 0.8

# ------------------------------------------------------------------
# 완전히 배제할 카테고리 - 수익형 후보로 절대 인정하지 않는다.
# 후보의 category 필드가 이 목록에 속한 카테고리로만 이루어져 있으면 통째로 탈락시킨다.
# ------------------------------------------------------------------
EXCLUDE_CATEGORIES = {"정치", "연예", "스포츠", "국제", "사회", "날씨"}

# ------------------------------------------------------------------
# 검색의도 단어 (collector.py의 INTENT_WORDS와 동일한 기준을 사용해 일관성을 유지한다)
# ------------------------------------------------------------------
INTENT_WORDS = {
    "신청", "대상", "조건", "방법", "신청방법", "신청기간", "접수", "마감",
    "지급", "지급일", "지급대상", "환급", "환급금", "할인", "인상", "인하",
    "한도", "기준", "개정", "변경", "확대", "축소", "서류", "절차", "발표",
    "공고", "조회", "계산", "계산법", "신청서", "자격", "대상자", "만기",
    "갱신", "가입", "해지", "보장", "특약", "공제", "감면", "면제", "혜택",
}


def _category_weight(category_str):
    """
    후보의 category 필드(콤마로 구분된 문자열, 예: "보험, 대출")를 받아
    그 중 가장 높은 가중치를 대표값으로 반환한다.
    (하나의 후보가 여러 카테고리에 걸쳐 있으면 더 수익성이 높은 쪽을 우선시)
    """
    if not category_str:
        return DEFAULT_WEIGHT
    cats = [c.strip() for c in category_str.split(",") if c.strip()]
    if not cats:
        return DEFAULT_WEIGHT
    return max(CATEGORY_WEIGHTS.get(c, DEFAULT_WEIGHT) for c in cats)


def _has_intent(keyword):
    """키워드 문구 안에 검색의도 단어가 포함되어 있는지 확인한다."""
    return any(w in keyword for w in INTENT_WORDS)


def _is_excluded(category_str):
    """
    category 필드에 나열된 모든 카테고리가 EXCLUDE_CATEGORIES에만 속하면 True.
    카테고리가 비어있는 경우(=collector.py에서 카테고리 태깅이 안 된 경우)는
    배제하지 않고 통과시킨다(과도한 탈락 방지).
    """
    if not category_str:
        return False
    cats = [c.strip() for c in category_str.split(",") if c.strip()]
    if not cats:
        return False
    return all(cat in EXCLUDE_CATEGORIES for cat in cats)


def filter_candidates(candidates, log=None):
    """
    collector.py의 후보 리스트를 받아 다음을 수행한다.
    1) 제외 카테고리에만 속한 후보를 탈락시킨다.
    2) 살아남은 후보에 category_weight(카테고리 가중치), has_intent(검색의도 포함 여부),
       profit_tags(사람이 읽을 수 있는 태그 목록)를 부여한다.
    3) category_weight가 높은 순, 그 다음 mentions가 높은 순으로 정렬하여 반환한다.

    이 함수가 반환하는 각 후보는 collector.py가 준 필드(keyword/category/mentions/
    articles/recency/matched_seed/generic_flag)에 category_weight/has_intent/profit_tags
    3개 필드가 추가된 형태이며, 이 형식 그대로 scorer.py의 score_candidates()에 전달된다.
    """
    def _log(msg):
        if log:
            log(msg)

    kept = []
    dropped_excluded = 0

    for c in candidates:
        category_str = c.get("category", "")

        if _is_excluded(category_str):
            dropped_excluded += 1
            continue

        c["category_weight"] = _category_weight(category_str)
        c["has_intent"] = _has_intent(c["keyword"])

        tags = []
        if c["has_intent"]:
            tags.append("검색의도 포함")
        if c["category_weight"] >= 1.3:
            tags.append("고단가 카테고리")
        elif c["category_weight"] <= 0.5:
            tags.append("저단가 카테고리(주의)")
        c["profit_tags"] = tags

        kept.append(c)

    _log(f"[profit_filter] 입력 {len(candidates)}개 -> 제외카테고리 탈락 {dropped_excluded}개 -> 통과 {len(kept)}개")

    kept.sort(key=lambda x: (x["category_weight"], x["mentions"]), reverse=True)
    return kept
