# scorer.py
# v16.6 - 실제 광고 경쟁도(plAvgDepth) 및 실측 CTR 기반 수익 추정
# 변경 사항(2026-07):
#   1) 카테고리 고정 CPC 대신, 네이버 API가 무료로 제공하는 plAvgDepth(평균 광고 노출 개수)를
#      배율로 사용해 "검색량은 적어도 광고주 경쟁이 치열한 키워드"의 실제 CPC를 더 높게 추정
#      -> 조회수가 낮아도 수익이 높게 나올 수 있는 현상을 반영
#   2) 임의로 고정했던 2% 클릭률 대신, API가 돌려주는 실측 CTR(monthlyAvePcCtr/MobileCtr)을
#      우선 사용하고, 값이 없을 때만 2%를 기본값으로 사용
#   3) 추천 이유에 "조회수는 낮지만 광고 경쟁 치열 -> 실제 단가 높을 가능성" 문구 추가

import math

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

# API에 실측 CTR이 없을 때만 사용하는 기본 클릭률
DEFAULT_AD_CLICK_RATE = 0.02

# 카테고리별 "기본" CPC(원) 추정치. 여기에 plAvgDepth 배율을 곱해 실제 단가를 보정한다.
# ※ 실측 데이터가 아니라 상대적 우선순위를 위한 근사값입니다.
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
DEFAULT_CPC_KRW = 15

# plAvgDepth(평균 광고 노출 개수)를 CPC 배율로 변환할 때 사용하는 정규화 기준
# depth가 이 값과 같으면 배율 1.0, 이보다 크면 배율이 커지고 최대 AD_DEPTH_MAX_MULTIPLIER로 캡
AD_DEPTH_BASELINE = 3.0
AD_DEPTH_MAX_MULTIPLIER = 4.0

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


def _get_ad_depth_multiplier(naver_data: dict) -> float:
    """
    plAvgDepth(평균 파워링크 광고 노출 개수)가 높을수록 광고주 경쟁이 치열하다는 뜻이고,
    이는 실제 CPC가 카테고리 평균보다 훨씬 높을 가능성을 의미한다.
    검색량이 적어도 이 값이 높으면 수익 추정치를 끌어올려 준다.
    """
    depth = float(naver_data.get("plAvgDepth", 0) or 0)
    multiplier = 1.0 + (depth / AD_DEPTH_BASELINE)
    return min(multiplier, AD_DEPTH_MAX_MULTIPLIER)


def _get_effective_click_rate(naver_data: dict) -> float:
    """API가 돌려준 실측 CTR을 우선 사용하고, 없으면 기본값(2%)을 사용"""
    ctr_pc = float(naver_data.get("monthlyAvePcCtr", 0) or 0)
    ctr_mobile = float(naver_data.get("monthlyAveMobileCtr", 0) or 0)
    ctr_values = [c for c in (ctr_pc, ctr_mobile) if c > 0]
    if not ctr_values:
        return DEFAULT_AD_CLICK_RATE
    avg_ctr_percent = sum(ctr_values) / len(ctr_values)
    return avg_ctr_percent / 100.0


def estimate_monthly_revenue(candidate: dict, naver_data: dict) -> dict:
    pc_count = int(naver_data.get("monthlyPcQcCnt", 0) or 0)
    mobile_count = int(naver_data.get("monthlyMobileQcCnt", 0) or 0)
    total_search = pc_count + mobile_count

    category = _detect_category(candidate["keyword"])
    is_money_category = category != "기타"
    base_cpc = CATEGORY_CPC_KRW.get(category, DEFAULT_CPC_KRW)

    ad_depth_multiplier = _get_ad_depth_multiplier(naver_data)
    cpc_estimate = base_cpc * ad_depth_multiplier
    click_rate = _get_effective_click_rate(naver_data)

    tier = _get_specificity_tier(candidate)
    alpha = EXPONENT_BY_SPECIFICITY[tier]
    capture_rate = TRAFFIC_CAPTURE_RATE[tier]

    new_issue_special_case = False
    low_search_high_value = False

    if total_search > 0:
        effective_demand = total_search ** alpha
        estimated_visits = effective_demand * capture_rate
        # 검색량은 적은데(1000 미만) 광고 경쟁도가 높으면 "숨은 고수익 키워드" 신호로 표시
        if total_search < 1000 and ad_depth_multiplier >= 1.8:
            low_search_high_value = True
    else:
        if candidate["mentions"] >= NEW_ISSUE_MENTION_THRESHOLD and is_money_category:
            new_issue_special_case = True
            proxy_demand = candidate["mentions"] * 10
            effective_demand = proxy_demand ** alpha
            estimated_visits = effective_demand * capture_rate
        else:
            estimated_visits = 0.5

    estimated_clicks = estimated_visits * click_rate
    estimated_revenue_krw = estimated_clicks * cpc_estimate

    return {
        "category": category,
        "is_money_category": is_money_category,
        "pc": pc_count,
        "mobile": mobile_count,
        "estimated_visits": round(estimated_visits, 1),
        "estimated_revenue_krw": round(estimated_revenue_krw, 1),
        "ad_depth_multiplier": round(ad_depth_multiplier, 2),
        "click_rate_used": round(click_rate * 100, 2),  # %로 표시
        "new_issue_special_case": new_issue_special_case,
        "low_search_high_value": low_search_high_value,
        "tier": tier,
    }


def _calculate_profit_score(revenue_info: dict) -> float:
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

    if revenue_info.get("low_search_high_value"):
        reasons.append(
            f"⭐ 조회수는 낮지만 광고 경쟁도 높음(배율 x{revenue_info['ad_depth_multiplier']}) "
            "- 실제 단가가 높아 숨은 고수익 키워드일 가능성"
        )
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
        "ad_depth_multiplier": revenue_info["ad_depth_multiplier"],
        "low_search_high_value": revenue_info["low_search_high_value"],
        "new_issue_special_case": revenue_info["new_issue_special_case"],
        "reasons": reasons,
    }


def filter_top5(scored_keywords: list) -> list:
    money_only = [k for k in scored_keywords if k["is_money_category"]]
    money_only.sort(key=lambda x: x["final_score"], reverse=True)
    return money_only[:5]
