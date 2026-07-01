# scorer.py
# v16.2 - 이슈점수 / 수익점수 / 최종점수 계산기
# 변경 사항(2026-07, 애드포스트 수익성 검증 반영):
#   1) 수익 카테고리("기타")로 분류된 키워드는 profit_score 상한을 걸어
#      애드포스트와 무관한 화제성 키워드가 TOP5에 끼어드는 것을 방지
#   2) CATEGORY_CPC_WEIGHT는 실측이 아닌 추정값임을 결과에 명시(cpc_is_estimated)
#   3) 검색량 0 + mentions 높음 = "신규 이슈 특례"로 분리해 mentions 기반 보정 점수 부여
#   4) money_bonus를 flat 값이 아닌 검색량 규모에 비례하는 방식으로 변경

import math

# ---------------------------------------------------------------
# 1. 후보 성격에 따른 mentions 가중치 (collector.py 연동)
# ---------------------------------------------------------------
WEIGHT_MULTIWORD = 1.0        # "고유가 피해지원금" 같은 2어절 이상 구체 이슈
WEIGHT_SPECIFIC_SINGLE = 0.7  # "상생대환대출2", "국민연금공단" 같은 압축 명사
WEIGHT_GENERIC_SINGLE = 0.15  # "환급", "대출", "보험" 같은 범용 시드 단어

# ---------------------------------------------------------------
# 2. 카테고리별 CPC 가중치
#    ※ 주의: 이 값은 실측 CPC/애드포스트 단가가 아니라
#      "이 카테고리가 대체로 광고 단가가 높다"는 상식적 추정치입니다.
#      네이버 검색광고 API 기본 응답에는 CPC 필드가 없고,
#      애드포스트 실제 단가는 검색광고 CPC와 별개 체계이므로
#      정확한 수치가 아님을 UI에도 반드시 표기해야 합니다.
# ---------------------------------------------------------------
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

# "기타"(수익 카테고리 미매칭) 키워드에 걸리는 profit_score 상한
# 화제성 이슈(연예/스포츠/사건사고 등)가 검색량만으로 TOP5를 차지하지 못하게 막는 안전장치
NON_MONEY_PROFIT_CAP = 8.0

# 신규 이슈 특례: 검색량 0인데 mentions가 이 값 이상이면
# "데이터가 아직 안 잡힌 신선한 이슈"로 간주해 최소 점수를 mentions 기반으로 보정
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
    """
    네이버 API가 돌려준 관련 키워드(relKeyword)가 원래 시드 키워드 문자열을
    포함하지 않는 경우, 관련성이 낮다고 보고 mentions를 30% 수준으로 할인한다.
    """
    seed = candidate["keyword"]
    rel_keyword = naver_data.get("relKeyword", seed) if naver_data else seed
    if seed not in rel_keyword:
        return 0.3
    return 1.0


def calculate_issue_score(candidate: dict, naver_data: dict = None) -> float:
    weight = _get_candidate_weight(candidate)
    discount = _apply_relkeyword_discount(candidate, naver_data or {})
    adjusted_mentions = candidate["mentions"] * weight * discount

    # 로그 스케일로 압축해 극단값이 점수를 지배하지 않도록 함
    issue_score = math.log2(adjusted_mentions + 1) * 10
    return round(issue_score, 2)


def calculate_profit_score(candidate: dict, naver_data: dict = None) -> dict:
    """
    수익점수를 계산하고, 계산 과정의 세부 정보(카테고리, 추정 여부, 특례 적용 여부)를
    함께 반환한다. 반환값이 float가 아니라 dict인 이유는 UI/디버깅에서
    '왜 이 점수가 나왔는지' 투명하게 보여주기 위함이다.
    """
    naver_data = naver_data or {}
    pc_count = naver_data.get("monthlyPcQcCnt", 0) or 0
    mobile_count = naver_data.get("monthlyMobileQcCnt", 0) or 0
    total_search = pc_count + mobile_count

    category = _detect_category(candidate["keyword"])
    is_money_category = category != "기타"
    cpc_weight = CATEGORY_CPC_WEIGHT.get(category, 1.0)

    new_issue_special_case = False

    if total_search > 0:
        # 검색량이 실제로 잡히는 정상 케이스
        search_score = math.log2(total_search + 1) * 5
        # money_bonus: flat 값이 아니라 검색 규모에 비례 (최대 20점 캡)
        money_bonus = min(search_score * 0.4, 20) if is_money_category else 0
    else:
        # 검색량 0 케이스: "신규 이슈라 데이터 미반영"과 "그냥 죽은 키워드"를 구분
        if candidate["mentions"] >= NEW_ISSUE_MENTION_THRESHOLD and is_money_category:
            # 신규 이슈 특례: mentions를 근거로 최소 점수 보정
            new_issue_special_case = True
            search_score = math.log2(candidate["mentions"] + 1) * 4
            money_bonus = search_score * 0.3
        else:
            search_score = 1.0  # 죽은 키워드에 가까운 최소값
            money_bonus = 0

    raw_profit_score = (search_score * cpc_weight) + money_bonus

    # 수익 카테고리와 무관한("기타") 키워드는 검색량이 커도 상한을 걸어
    # TOP5에 화제성 키워드가 끼어드는 것을 막음
    if not is_money_category:
        profit_score = min(raw_profit_score, NON_MONEY_PROFIT_CAP)
    else:
        profit_score = raw_profit_score

    return {
        "profit_score": round(profit_score, 2),
        "category": category,
        "is_money_category": is_money_category,
        "cpc_is_estimated": True,  # CPC 가중치가 실측이 아닌 추정값임을 항상 명시
        "new_issue_special_case": new_issue_special_case,
        "total_search": total_search,
    }


def calculate_final_score(issue_score: float, profit_score: float, is_money_category: bool) -> float:
    """
    이슈성과 수익성을 6:4 비중으로 결합.
    다만 수익 카테고리가 아닌 경우("기타") 최종점수 자체에도 페널티를 줘서
    TOP5 후보군에서 자연스럽게 밀려나도록 한다.
    """
    base = issue_score * 0.6 + profit_score * 0.4
    if not is_money_category:
        base *= 0.5  # 화제성만 있고 수익성 무관한 키워드는 최종점수 절반으로 페널티
    return round(base, 2)


def get_difficulty(candidate: dict, naver_data: dict = None) -> str:
    naver_data = naver_data or {}
    competition = naver_data.get("compIdx", "중간")
    category = _detect_category(candidate["keyword"])

    high_effort_categories = {"보험", "대출", "세금"}
    if category in high_effort_categories or competition == "높음":
        return "어려움"
    if competition == "낮음" and candidate.get("word_count", 1) >= 2:
        return "쉬움"
    return "보통"


def build_reason_checklist(candidate: dict, issue_score: float, profit_info: dict) -> list:
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


def categorize(final_score: float, issue_score: float, profit_score: float, is_money_category: bool) -> str:
    if not is_money_category:
        return "비수익 (제외 권장)"
    if issue_score >= 25 and profit_score >= 15:
        return "HOT 이슈"
    if profit_score >= 25:
        return "수익형 정기"
    if final_score < 8:
        return "보류"
    return "주의"


def score_keyword(candidate: dict, naver_data: dict = None) -> dict:
    """collector.py 후보 1개에 대해 전체 점수/분류/이유를 계산해 반환"""
    issue_score = calculate_issue_score(candidate, naver_data)
    profit_info = calculate_profit_score(candidate, naver_data)
    final_score = calculate_final_score(
        issue_score, profit_info["profit_score"], profit_info["is_money_category"]
    )

    return {
        "keyword": candidate["keyword"],
        "mentions": candidate["mentions"],
        "issue_score": issue_score,
        "profit_score": profit_info["profit_score"],
        "final_score": final_score,
        "difficulty": get_difficulty(candidate, naver_data),
        "category": categorize(
            final_score, issue_score, profit_info["profit_score"], profit_info["is_money_category"]
        ),
        "money_category": profit_info["category"],
        "cpc_is_estimated": profit_info["cpc_is_estimated"],
        "new_issue_special_case": profit_info["new_issue_special_case"],
        "reasons": build_reason_checklist(candidate, issue_score, profit_info),
    }


def filter_top5(scored_keywords: list) -> list:
    """
    비수익 키워드를 제외하고 final_score 기준 상위 5개만 반환.
    STEP 1(수익성 1차 필터)의 실질적인 구현부.
    """
    money_only = [k for k in scored_keywords if k["category"] != "비수익 (제외 권장)"]
    money_only.sort(key=lambda x: x["final_score"], reverse=True)
    return money_only[:5]
