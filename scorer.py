# scorer.py
# v16.5 - 실질 애드포스트 수익 추정 기반 스코어러
# 변경 사항(2026-07):
#   1) profit_score를 "검색량+카테고리"의 대략적 조합이 아니라
#      "실제로 이 글을 쓰면 한 달에 몇 원 정도 벌릴 것인가"를 직접 추정하는 방식으로 전환
#   2) 검색량을 그대로 쓰지 않고, 키워드 구체성(word_count/is_generic)에 따라
#      다른 지수(alpha)로 감쇠시켜 "신규 블로그가 실제로 받을 수 있는 유입"을 근사
#      (compIdx는 검색광고 입찰 경쟁도라 블로그 SEO 경쟁도와 다르므로 보조 지표로만 사용)
#   3) 카테고리별 실제 CPC(원) 추정치를 곱해 예상 월수익(원)을 산출
#   4) 이 예상 월수익을 로그 스케일로 압축해 profit_score로 사용

import math

# ---------------------------------------------------------------
# 1. 키워드 구체성에 따른 "실질 유입 감쇠 지수"와 "트래픽 포착률"
#    - generic: '대출','환급','보험' 등 범용 단일어. 이미 대형 매체/파워블로거가
#      상위를 점유하고 있어 검색량이 커도 신규 글의 실제 유입은 극히 일부에 불과함.
#    - specific_single: '피해지원금'처럼 압축된 한 단어 고유명사.
#    - multiword: '고유가 피해지원금'처럼 2단어 이상 구체적 문구. 경쟁이 상대적으로
#      약해 검색량 증가가 실질 유입 증가로 거의 그대로 이어짐.
# ---------------------------------------------------------------
EXPONENT_BY_SPECIFICITY = {
    "generic": 0.30,
    "specific_single": 0.60,
    "multiword": 0.85,
}

TRAFFIC_CAPTURE_RATE = {
    "generic": 0.5,
    "specific_single": 0.4,
    "multiword": 0.3,
}

# 방문자 중 실제로 광고를 클릭하는 비율 (업계 평균 1~3% 참고치)
AD_CLICK_RATE = 0.02

# ---------------------------------------------------------------
# 2. 카테고리별 실제 CPC(원) 추정치
#    ※ 실측 데이터가 아니라 블로거들이 공개한 애드포스트 수익 사례를 참고한
#      근사값입니다. 절대 수치가 아니라 카테고리 간 "상대적 우선순위"로 활용하세요.
# ---------------------------------------------------------------
CATEGORY_CPC_KRW = {
    "보험": 100,
    "대출": 80,
    "세금": 60,
    "연금": 55,
    "지원금": 25,
    "환급": 20,
    "상품권": 20,
}

MONEY_CATEGORY_KEYWORDS = list(CATEGORY_CPC_KRW.keys())
DEFAULT_CPC_KRW = 15  # 매칭 안 되는 기타 카테고리

NON_MONEY_PROFIT_CAP = 8.0
NEW_ISSUE_MENTION_THRESHOLD = 4


def _detect_category(keyword: str) -> str:
    for cat in MONEY_CATEGORY_KEYWORDS:
        if cat in keyword:
            return cat
    return "기타"


def _get_specificity_tier(candidate: dict) -> str:
    if candidate.get("is_generic"):
        return "generic"
    if candidate.get("word_count", 1) == 1:
        return "specific_single"
    return "multiword"


def _get_issue_weight(candidate: dict) -> float:
    tier = _get_specificity_tier(candidate)
    return {"generic": 0.15, "specific_single": 0.7, "multiword": 1.0}[tier]


def _apply_relkeyword_discount(candidate: dict, naver_data: dict) -> float:
    """네이버 API의 relKeyword가 시드 키워드를 포함하지 않으면 관련성이 낮다고 보고 할인"""
    seed = candidate["keyword"]
    rel_keyword = naver_data.get("relKeyword", seed) if naver_data else seed
    if seed not in rel_keyword:
        return 0.3
    return 1.0


def _calculate_issue_score(candidate: dict, naver_data: dict) -> float:
    weight = _get_issue_weight(candidate)
    discount = _apply_relkeyword_discount(candidate, naver_data)
    adjusted_mentions = candidate["mentions"] * weight * discount
    return round(math.log2(adjusted_mentions + 1) * 10, 2)


def estimate_monthly_revenue(candidate: dict, naver_data: dict) -> dict:
    """
    실제로 이 키워드로 글을 썼을 때 한 달에 대략 몇 원의 애드포스트 수익이
    날 것인지 추정한다. 검색량을 그대로 쓰지 않고, 키워드 구체성에 따라
    감쇠시켜 '신규 블로그가 실제로 받을 수 있는 유입'을 근사한다.
    """
    pc_count = int(naver_data.get("monthlyPcQcCnt", 0) or 0)
    mobile_count = int(naver_data.get("monthlyMobileQcCnt", 0) or 0)
    total_search = pc_count + mobile_count

    category = _detect_category(candidate["keyword"])
    is_money_category = category != "기타"
    cpc_krw = CATEGORY_CPC_KRW.get(category, DEFAULT_CPC_KRW)

    tier = _get_specificity_tier(candidate)
    alpha = EXPONENT_BY_SPECIFICITY[tier]
    capture_rate = TRAFFIC_CAPTURE_RATE[tier]

    new_issue_special_case = False

    if total_search > 0:
        effective_demand = total_search ** alpha
        estimated_visits = effective_demand * capture_rate
    else:
        # 검색량 0: 신규 이슈라 데이터 미반영일 가능성 -> 뉴스 언급량으로 대체 추정
        if candidate["mentions"] >= NEW_ISSUE_MENTION_THRESHOLD and is_money_category:
            new_issue_special_case = True
            proxy_demand = candidate["mentions"] * 10  # 언급량을 검색량 대용으로 환산(러프한 추정)
            effective_demand = proxy_demand ** alpha
            estimated_visits = effective_demand * capture_rate
        else:
            estimated_visits = 0.5  # 거의 죽은 키워드

    estimated_clicks = estimated_visits * AD_CLICK_RATE
    estimated_revenue_krw = estimated_clicks * cpc_krw

    return {
        "category": category,
        "is_money_category": is_money_category,
        "pc": pc_count,
        "mobile": mobile_count,
        "estimated_visits": round(estimated_visits, 1),
        "estimated_revenue_krw": round(estimated_revenue_krw, 1),
        "new_issue_special_case": new_issue_special_case,
        "tier": tier,
    }


def _calculate_profit_score(revenue_info: dict) -> float:
    """예상 월수익(원)을 로그 스케일로 압축해 issue_score와 비슷한 범위의 점수로 변환"""
    raw = math.log2(revenue_info["estimated_revenue_krw"] + 1) * 10
    if not revenue_info["is_money_category"]:
        raw = min(raw, NON_MONEY_PROFIT_CAP)
    return round(raw, 2)


def _calculate_final_score(issue_score: float, profit_score: float, is_money_category: bool) -> float:
    base = issue_score * 0.6 + profit_score * 0.4
    if not is_money_category:
        base *= 0.5
    return round(base, 2)


def get_difficulty(candidate: dict, naver_data: dict) -> str:
    competition = naver_data.get("compIdx", "중간")
    category = _detect_category(candidate["keyword"])

    high_effort_categories = {"보험", "대출", "세금"}
    if category in high_effort_categories or competition == "높음":
        return "어려움"
    if competition == "낮음" and candidate.get("word_count", 1) >= 2:
        return "쉬움"
    return "보통"


def _build_reason_checklist(candidate: dict, revenue_info: dict) -> list:
    reasons = []
    revenue_text = f"예상 월수익 약 {revenue_info['estimated_revenue_krw']:,.0f}원 (추정, 실제와 다를 수 있음)"
    reasons.append(revenue_text)

    if candidate["mentions"] >= 5 and not candidate.get("is_generic"):
        reasons.append("뉴스 언급 증가")
    if candidate.get("word_count", 1) >= 2:
        reasons.append("구체적 이슈 (신규 블로그도 상위노출 가능성 있음)")
    if revenue_info["is_money_category"]:
        reasons.append("장기 검색 가능 카테고리")
    if revenue_info.get("new_issue_special_case"):
        reasons.append("신규 이슈 (검색량 데이터 미반영, 뉴스 언급 기반 추정)")
    if candidate.get("is_generic"):
        reasons.append("⚠ 범용 레드오션 키워드 - 검색량은 크지만 실제 유입은 매우 제한적")
    if not revenue_info["is_money_category"]:
        reasons.append("⚠ 수익 카테고리 미매칭 - 애드포스트 수익성 낮을 가능성")
    return reasons


def _categorize(final_score: float, issue_score: float, profit_score: float,
                 is_money_category: bool, is_generic: bool) -> str:
    if not is_money_category:
        return "비수익(제외권장)"
    # HOT 이슈는 '오늘의 이슈'라는 신선도가 핵심이므로 범용 상시 키워드는 제외
    if issue_score >= 25 and profit_score >= 40 and not is_generic:
        return "HOT 이슈"
    if profit_score >= 45:
        return "수익형 정기"
    if final_score < 8:
        return "보류"
    return "주의"


def score_keyword(candidate: dict, naver_data: dict = None) -> dict:
    naver_data = naver_data or {}

    issue_score = _calculate_issue_score(candidate, naver_data)
    revenue_info = estimate_monthly_revenue(candidate, naver_data)
    profit_score = _calculate_profit_score(revenue_info)
    final_score = _calculate_final_score(issue_score, profit_score, revenue_info["is_money_category"])
    difficulty = get_difficulty(candidate, naver_data)
    keyword_type = _categorize(
        final_score, issue_score, profit_score,
        revenue_info["is_money_category"], candidate.get("is_generic", False)
    )
    reasons = _build_reason_checklist(candidate, revenue_info)

    return {
        "keyword": candidate["keyword"],
        "mentions": candidate["mentions"],
        "pc": revenue_info["pc"],
        "mobile": revenue_info["mobile"],
        "competition": naver_data.get("compIdx", "중간"),
        "issue_score": issue_score,
        "profit_score": profit_score,
        "final_score": final_score,
        "type": keyword_type,
        "difficulty": difficulty,
        "money_category": revenue_info["category"],
        "is_money_category": revenue_info["is_money_category"],
        "estimated_revenue_krw": revenue_info["estimated_revenue_krw"],
        "estimated_visits": revenue_info["estimated_visits"],
        "new_issue_special_case": revenue_info["new_issue_special_case"],
        "reasons": reasons,
    }


def filter_top5(scored_keywords: list) -> list:
    """비수익 키워드를 제외하고 예상 수익(final_score) 기준 상위 5개 반환"""
    money_only = [k for k in scored_keywords if k["is_money_category"]]
    money_only.sort(key=lambda x: x["final_score"], reverse=True)
    return money_only[:5]
