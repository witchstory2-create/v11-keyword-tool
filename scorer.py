CATEGORY_CPC_WEIGHT = {
    "대출": 1.3, "보험": 1.3, "자동차보험": 1.2, "건강보험": 1.1,
    "연금": 1.1, "청약": 1.15, "세금": 1.0, "세액공제": 1.0, "카드": 1.1,
    "환급": 0.9, "지원금": 0.85,
}

DANGER_WORDS = ["추천", "가입", "신청", "무조건", "100%", "수익", "승인"]

DIFFICULTY_HARD = ["보험", "세금", "법률", "청약", "대출"]
DIFFICULTY_EASY = ["환급", "지원금", "연금", "조회"]


def to_int(v):
    s = str(v).replace("< ", "").replace(",", "").strip()
    try:
        return int(s)
    except Exception:
        return 0


def get_category_weight(keyword):
    for cat, w in CATEGORY_CPC_WEIGHT.items():
        if cat in keyword:
            return w
    return 1.0


def get_difficulty(keyword):
    if any(w in keyword for w in DIFFICULTY_HARD):
        return "어려움"
    if any(w in keyword for w in DIFFICULTY_EASY):
        return "쉬움"
    return "보통"


def score_keyword(item, source_meta):
    """
    item: 네이버 검색광고 API에서 온 개별 키워드 데이터
    source_meta: collector.py에서 넘어온 {"mentions": int, "is_money_topic": bool}
    """
    keyword = item.get("relKeyword", "")
    pc = to_int(item.get("monthlyPcQcCnt", 0))
    mobile = to_int(item.get("monthlyMobileQcCnt", 0))
    total = pc + mobile
    comp = item.get("compIdx", "LOW")

    mentions = source_meta.get("mentions", 0)
    is_money_topic = source_meta.get("is_money_topic", False)

    # --- 이슈 점수: 실제 뉴스 언급 빈도가 핵심 축 ---
    mention_score = min(mentions * 8, 50)
    volume_score = min(total / 3000 * 20, 20)
    keyword_match_bonus = 10 if is_money_topic else 0
    issue_score = round(min(mention_score + volume_score + keyword_match_bonus, 100), 1)

    # --- 수익 점수: 검색량 + 경쟁도 + 카테고리 CPC 가중치 ---
    search_score = min(total / 3000 * 35, 35)
    comp_score = {"LOW": 20, "MID": 12, "HIGH": 4}.get(comp, 8)
    category_weight = get_category_weight(keyword)
    safety = 0 if any(w in keyword for w in DANGER_WORDS) else 15

    profit_score = round((search_score + comp_score) * category_weight + safety, 1)
    profit_score = min(profit_score, 100)

    # --- 유형별로 다른 가중치를 적용한 최종 점수 ---
    if any(w in keyword for w in DANGER_WORDS):
        typ = "주의"
        final_score = round(profit_score * 0.5, 1)
    elif issue_score >= 60:
        typ = "HOT 이슈"
        final_score = round(issue_score * 0.7 + profit_score * 0.3, 1)
    elif profit_score >= 65:
        typ = "수익형 상시"
        final_score = round(issue_score * 0.3 + profit_score * 0.7, 1)
    else:
        typ = "보류"
        final_score = round(issue_score * 0.4 + profit_score * 0.6, 1)

    return {
        "keyword": keyword,
        "pc": pc,
        "mobile": mobile,
        "competition": comp,
        "issue_score": issue_score,
        "profit_score": profit_score,
        "final_score": final_score,
        "type": typ,
        "mentions": mentions,
        "difficulty": get_difficulty(keyword),
    }


def build_reason_checklist(item):
    """오늘의 추천 이유를 사람이 읽을 수 있는 체크리스트 문장으로 변환"""
    checks = []
    if item.get("mentions", 0) >= 3:
        checks.append("오늘 뉴스 언급 증가")
    if item["pc"] + item["mobile"] >= 3000:
        checks.append("검색량 높음")
    if get_category_weight(item["keyword"]) >= 1.1:
        checks.append("CPC/수익성 높은 카테고리")
    if item["competition"] in ("LOW", "MID"):
        checks.append("경쟁도 낮음~보통, 상위노출 가능성")
    if item["type"] == "수익형 상시":
        checks.append("장기 검색 가능 키워드")
    if not checks:
        checks.append("기본 후보 키워드 (신뢰도 낮음, 직접 확인 권장)")
    return checks
