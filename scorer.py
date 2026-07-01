# scorer.py
# v16.9 - 이모지(🔥,ℹ,⚠) 표시가 Windows 기본 글꼴에서 깨지는 문제를 해결하기 위해
#         모든 표시 문구를 일반 텍스트([급증], [평이], [주의] 등)로 교체.
#         나머지 로직(광고 경쟁도 배율, 글작성순위, 트렌드 재검증)은 v16.8과 완전히 동일.

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

DEFAULT_AD_CLICK_RATE = 0.02

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

AD_DEPTH_BASELINE = 3.0
AD_DEPTH_MAX_MULTIPLIER = 4.0

NON_MONEY_PROFIT_CAP = 8.0
NEW_ISSUE_MENTION_THRESHOLD = 4

TYPE_CODE_MAP = {
    "HOT 이슈": "HOT_ISSUE",
    "수익형 정기": "RECURRING_PROFIT",
    "비수익(제외권장)": "NON_PROFIT",
    "보류": "RECURRING_PROFIT",
    "주의": "RECURRING_PROFIT",
}

DIFFICULTY_LEVEL_MAP = {
    "쉬움": 1,
    "보통": 2,
    "어려움": 3,
}

DIFFICULTY_INDEX_MAP = {1: 1.0, 2: 1.8, 3: 2.8}

HOT_ISSUE_BASE_WEIGHT = 2.0
HOT_ISSUE_MAX_WEIGHT = 3.0
HOT_ISSUE_MENTION_SCALE = 20

RECURRING_PROFIT_WEIGHT = 1.0
NON_PROFIT_WEIGHT = 0.3

SPIKE_RATIO_HOT_THRESHOLD = 2.0


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
    depth = float(naver_data.get("plAvgDepth", 0) or 0)
    multiplier = 1.0 + (depth / AD_DEPTH_BASELINE)
    return min(multiplier, AD_DEPTH_MAX_MULTIPLIER)


def _get_effective_click_rate(naver_data: dict) -> float:
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
        "click_rate_used": round(click_rate * 100, 2),
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


def _build_reason_checklist(candidate: dict, revenue_info: dict, trend_info: dict = None) -> list:
    trend_info = trend_info or {}
    reasons = []
    revenue_text = f"예상 월수익 약 {revenue_info['estimated_revenue_krw']:,.0f}원 (추정, 실제와 다를 수 있음)"
    reasons.append(revenue_text)

    # [FIXED] 이모지 대신 일반 텍스트 태그 사용
    if trend_info.get("trend_available"):
        spike = trend_info.get("spike_ratio", 1.0)
        if spike >= SPIKE_RATIO_HOT_THRESHOLD:
            reasons.insert(0, f"[급증 확인] 실제 검색량이 평소 대비 x{spike}배 증가")
        else:
            reasons.append(f"[평이] 뉴스 언급은 많지만 검색량은 평소와 비슷함(x{spike}) - 상시성 키워드로 판단")

    if revenue_info.get("low_search_high_value"):
        reasons.append(
            f"[숨은고수익] 조회수는 낮지만 광고 경쟁도 높음(배율 x{revenue_info['ad_depth_multiplier']}) "
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
        reasons.append("[주의] 범용 레드오션 키워드 - 검색량은 크지만 실제 유입은 매우 제한적")
    if not revenue_info["is_money_category"]:
        reasons.append("[주의] 수익 카테고리 미매칭 - 애드포스트 수익성 낮을 가능성")
    return reasons


def _categorize(final_score: float, issue_score: float, profit_score: float,
                 is_money_category: bool, is_generic: bool,
                 spike_ratio: float = None, trend_available: bool = False) -> str:
    if not is_money_category:
        return "비수익(제외권장)"

    is_hot_candidate = issue_score >= 25 and profit_score >= 40 and not is_generic

    if is_hot_candidate:
        if trend_available and spike_ratio is not None and spike_ratio < SPIKE_RATIO_HOT_THRESHOLD:
            return "수익형 정기"
        return "HOT 이슈"

    if profit_score >= 45:
        return "수익형 정기"
    if final_score < 8:
        return "보류"
    return "주의"


def calculate_writing_priority(revenue_krw: float, difficulty_level: int, keyword_type_code: str, mentions: int = 0) -> dict:
    difficulty_index = DIFFICULTY_INDEX_MAP.get(difficulty_level, 2.0)

    if keyword_type_code == "HOT_ISSUE":
        urgency_weight = HOT_ISSUE_BASE_WEIGHT + min(mentions / HOT_ISSUE_MENTION_SCALE, 1.0)
        urgency_weight = min(urgency_weight, HOT_ISSUE_MAX_WEIGHT)
        guidance = "오늘 작성 권장 (실시간 이슈, 신선도 감쇠 빠름)"
    elif keyword_type_code == "RECURRING_PROFIT":
        urgency_weight = RECURRING_PROFIT_WEIGHT
        difficulty_text = {1: "하", 2: "중", 3: "상"}.get(difficulty_level, "중")
        guidance = f"여유있게 작성 가능 (수익형 정기, 난이도 {difficulty_text})"
    else:  # NON_PROFIT
        urgency_weight = NON_PROFIT_WEIGHT
        guidance = "작성 비권장 (수익화 어려움)"

    priority_score = (revenue_krw / difficulty_index) * urgency_weight

    return {
        "priority_score": round(priority_score, 1),
        "guidance": guidance,
        "difficulty_index": difficulty_index,
        "urgency_weight": round(urgency_weight, 2),
    }


def score_keyword(candidate: dict, naver_data: dict = None, trend_info: dict = None) -> dict:
    naver_data = naver_data or {}
    trend_info = trend_info or {}

    issue_score = _calculate_issue_score(candidate, naver_data)
    revenue_info = estimate_monthly_revenue(candidate, naver_data)
    profit_score = _calculate_profit_score(revenue_info)
    final_score = _calculate_final_score(issue_score, profit_score, revenue_info["is_money_category"])
    difficulty = get_difficulty(candidate, naver_data)

    keyword_type = _categorize(
        final_score, issue_score, profit_score,
        revenue_info["is_money_category"], candidate.get("is_generic", False),
        spike_ratio=trend_info.get("spike_ratio"),
        trend_available=trend_info.get("trend_available", False),
    )
    reasons = _build_reason_checklist(candidate, revenue_info, trend_info)

    type_code = TYPE_CODE_MAP.get(keyword_type, "RECURRING_PROFIT")
    difficulty_level = DIFFICULTY_LEVEL_MAP.get(difficulty, 2)
    priority_info = calculate_writing_priority(
        revenue_krw=revenue_info["estimated_revenue_krw"],
        difficulty_level=difficulty_level,
        keyword_type_code=type_code,
        mentions=candidate["mentions"],
    )

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
        "type_code": type_code,
        "difficulty": difficulty,
        "difficulty_level": difficulty_level,
        "money_category": revenue_info["category"],
        "is_money_category": revenue_info["is_money_category"],
        "estimated_revenue_krw": revenue_info["estimated_revenue_krw"],
        "estimated_visits": revenue_info["estimated_visits"],
        "ad_depth_multiplier": revenue_info["ad_depth_multiplier"],
        "low_search_high_value": revenue_info["low_search_high_value"],
        "new_issue_special_case": revenue_info["new_issue_special_case"],
        "reasons": reasons,
        "writing_priority_score": priority_info["priority_score"],
        "writing_guidance": priority_info["guidance"],
        "urgency_weight": priority_info["urgency_weight"],
        "trend_checked": trend_info.get("trend_available", False),
        "spike_ratio": trend_info.get("spike_ratio"),
    }


def recheck_with_trend(scored_result: dict, trend_info: dict) -> dict:
    keyword_type = _categorize(
        scored_result["final_score"], scored_result["issue_score"], scored_result["profit_score"],
        scored_result["is_money_category"], False,
        spike_ratio=trend_info.get("spike_ratio"),
        trend_available=trend_info.get("trend_available", False),
    )

    type_code = TYPE_CODE_MAP.get(keyword_type, "RECURRING_PROFIT")
    priority_info = calculate_writing_priority(
        revenue_krw=scored_result["estimated_revenue_krw"],
        difficulty_level=scored_result["difficulty_level"],
        keyword_type_code=type_code,
        mentions=scored_result["mentions"],
    )

    # [FIXED] 이모지 문자열 대신 텍스트 태그로 중복 검사
    reasons = [r for r in scored_result["reasons"] if "급증 확인" not in r and "[평이]" not in r]
    if trend_info.get("trend_available"):
        spike = trend_info.get("spike_ratio", 1.0)
        if spike >= SPIKE_RATIO_HOT_THRESHOLD:
            reasons.insert(0, f"[급증 확인] 실제 검색량이 평소 대비 x{spike}배 증가")
        else:
            reasons.append(f"[평이] 뉴스 언급은 많지만 검색량은 평소와 비슷함(x{spike}) - 상시성 키워드로 판단")

    scored_result["type"] = keyword_type
    scored_result["type_code"] = type_code
    scored_result["writing_priority_score"] = priority_info["priority_score"]
    scored_result["writing_guidance"] = priority_info["guidance"]
    scored_result["urgency_weight"] = priority_info["urgency_weight"]
    scored_result["reasons"] = reasons
    scored_result["trend_checked"] = trend_info.get("trend_available", False)
    scored_result["spike_ratio"] = trend_info.get("spike_ratio")

    return scored_result


def filter_top5(scored_keywords: list) -> list:
    money_only = [k for k in scored_keywords if k["is_money_category"]]
    money_only.sort(key=lambda x: x["writing_priority_score"], reverse=True)
    return money_only[:5]
