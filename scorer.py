# scorer.py
# v16.2 - 이슈점수 / 수익점수 / 최종점수 계산기 (애드포스트 수익성 검증 반영)

import math

WEIGHT_MULTIWORD = 1.0
WEIGHT_SPECIFIC_SINGLE = 0.7
WEIGHT_GENERIC_SINGLE = 0.15

# ※ 실측 CPC가 아닌 "이 카테고리가 대체로 광고 단가가 높다"는 추정치입니다.
CATEGORY_CPC_WEIGHT = {
    "보험": 1.4,
    "대출": 1.3,
    "세금": 1.2,
    "연금": 1.1,
    "지원금": 1.0,
    "환급": 0.9,
    "상품권": 0.8,
}

MONEY_CATEGORY_KEYWORDS = list(CATEGORY_CPC_WEIGHT.keys())

NON_MONEY_PROFIT_CAP = 8.0
NEW_ISSUE_MENTION_THRESHOLD = 4


def _get_candidate_weight(candidate: dict) -> float:
    if candidate.get("is_generic"):
        return WEIGHT_GENERIC_SINGLE
    if candidate.get("word_count", 1) == 1:
        return WEIGHT_SPECIFIC_SINGLE
    return WEIGHT_MULTIWORD


def _detect_category(keyword: str) -> str:
    for cat in MONEY_CATEGORY_KEYWORDS:
        if cat in keyword:
            return cat
    return "기타"


def _apply_relkeyword_discount(candidate: dict, naver_data: dict) -> float:
    seed = candidate["keyword"]
    rel_keyword = naver_data.get("relKeyword", seed) if naver_data else seed
    if seed not in rel_keyword:
        return 0.3
    return 1.0


def _calculate_issue_score(candidate: dict, naver_data: dict) -> float:
    weight = _get_candidate_weight(candidate)
    discount = _apply_relkeyword_discount(candidate, naver_data)
    adjusted_mentions = candidate["mentions"] * weight * discount
    return round(math.log2(adjusted_mentions + 1) * 10, 2)


def _calculate_profit_info(candidate: dict, naver_data: dict) -> dict:
    pc_count = int(naver_data.get("monthlyPcQcCnt", 0) or 0)
    mobile_count = int(naver_data.get("monthlyMobileQcCnt", 0) or 0)
    total_search = pc_count + mobile_count

    category = _detect_category(candidate["keyword"])
    is_money_category = category != "기타"
    cpc_weight = CATEGORY_CPC_WEIGHT.get(category, 1.0)

    new_issue_special_case = False

    if total_search > 0:
        search_score = math.log2(total_search + 1) * 5
        money_bonus = min(search_score * 0.4, 20) if is_money_category else 0
    else:
        if candidate["mentions"] >= NEW_ISSUE_MENTION_THRESHOLD and is_money_category:
            new_issue_special_case = True
            search_score = math.log2(candidate["mentions"] + 1) * 4
            money_bonus = search_score * 0.3
        else:
            search_score = 1.0
            money_bonus = 0

    raw_profit_score = (search_score * cpc_weight) + money_bonus

    if not is_money_category:
        profit_score = min(raw_profit_score, NON_MONEY_PROFIT_CAP)
    else:
        profit_score = raw_profit_score

    return {
        "profit_score": round(profit_score, 2),
        "category": category,
        "is_money_category": is_money_category,
        "new_issue_special_case": new_issue_special_case,
        "pc": pc_count,
        "mobile": mobile_count,
    }


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


def _build_reason_checklist(candidate: dict, profit_info: dict) -> list:
    reasons = []
    if candidate["mentions"] >= 5 and not candidate.get("is_generic"):
        reasons.append("뉴스 언급 증가")
    if profit_info["profit_score"] >= 20:
        reasons.append("검색량/CPC 우수 (CPC는 추정값)")
    if candidate.get("word_count", 1) >= 2:
        reasons.append("구체적 이슈 (경쟁도 낮음)")
    if profit_info["is_money_category"]:
        reasons.append("장기 검색 가능 카테고리")
    if profit_info.get("new_issue_special_case"):
        reasons.append("신규 이슈 (검색량 데이터 미반영, 뉴스 언급 기반 판단)")
    if not profit_info["is_money_category"]:
        reasons.append("⚠ 수익 카테고리 미매칭 - 애드포스트 수익성 낮을 가능성")
    if not reasons:
        reasons.append("일반 이슈")
    return reasons


def _categorize(final_score: float, issue_score: float, profit_score: float, is_money_category: bool) -> str:
    if not is_money_category:
        return "비수익(제외권장)"
    if issue_score >= 25 and profit_score >= 15:
        return "HOT 이슈"
    if profit_score >= 25:
        return "수익형 정기"
    if final_score < 8:
        return "보류"
    return "주의"


def score_keyword(candidate: dict, naver_data: dict = None) -> dict:
    """
    candidate: collector.py가 만든 후보 dict (keyword, mentions, word_count, is_generic, money_topic)
    naver_data: 네이버 검색광고 API가 돌려준 keywordList의 항목 1개
                (relKeyword, monthlyPcQcCnt, monthlyMobileQcCnt, compIdx 등)
    """
    naver_data = naver_data or {}

    issue_score = _calculate_issue_score(candidate, naver_data)
    profit_info = _calculate_profit_info(candidate, naver_data)
    final_score = _calculate_final_score(issue_score, profit_info["profit_score"], profit_info["is_money_category"])
    difficulty = get_difficulty(candidate, naver_data)
    keyword_type = _categorize(final_score, issue_score, profit_info["profit_score"], profit_info["is_money_category"])
    reasons = _build_reason_checklist(candidate, profit_info)

    return {
        "keyword": candidate["keyword"],
        "mentions": candidate["mentions"],
        "pc": profit_info["pc"],
        "mobile": profit_info["mobile"],
        "competition": naver_data.get("compIdx", "중간"),
        "issue_score": issue_score,
        "profit_score": profit_info["profit_score"],
        "final_score": final_score,
        "type": keyword_type,
        "difficulty": difficulty,
        "money_category": profit_info["category"],
        "is_money_category": profit_info["is_money_category"],
        "new_issue_special_case": profit_info["new_issue_special_case"],
        "reasons": reasons,
    }


def filter_top5(scored_keywords: list) -> list:
    """비수익 키워드를 제외하고 final_score 기준 상위 5개 반환 (STEP1 수익성 1차 필터)"""
    money_only = [k for k in scored_keywords if k["is_money_category"]]
    money_only.sort(key=lambda x: x["final_score"], reverse=True)
    return money_only[:5]
