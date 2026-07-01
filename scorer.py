def to_int(v):
    s = str(v).replace("< ", "").replace(",", "").strip()
    try:
        return int(s)
    except:
        return 0

def score_keyword(item, source_keyword):
    keyword = item.get("relKeyword", "")
    pc = to_int(item.get("monthlyPcQcCnt", 0))
    mobile = to_int(item.get("monthlyMobileQcCnt", 0))
    total = pc + mobile
    comp = item.get("compIdx", "LOW")

    money_words = ["환급", "지원금", "연금", "보험", "세금", "청약", "카드", "건강보험", "자동차보험"]
    danger_words = ["추천", "가입", "신청", "무조건", "100%", "수익", "승인"]

    search_score = min(total / 3000 * 35, 35)

    comp_score = {
        "LOW": 20,
        "MID": 12,
        "HIGH": 4
    }.get(comp, 8)

    profit_base = 30 if any(w in keyword for w in money_words) else 10
    safety = 0 if any(w in keyword for w in danger_words) else 15

    profit_score = round(search_score + comp_score + profit_base + safety, 1)

    issue_score = 20
    if source_keyword.replace(" ", "") in keyword.replace(" ", ""):
        issue_score += 25
    if any(w in keyword for w in ["오늘", "2026", "변경", "인상", "환급", "지원금"]):
        issue_score += 25
    if len(keyword) >= 5:
        issue_score += 10

    issue_score = min(issue_score, 100)
    final_score = round(issue_score * 0.4 + profit_score * 0.6, 1)

    if final_score >= 80:
        typ = "HOT 이슈"
    elif profit_score >= 70:
        typ = "수익형 상시"
    elif any(w in keyword for w in danger_words):
        typ = "주의"
    else:
        typ = "보류"

    return {
        "keyword": keyword,
        "pc": pc,
        "mobile": mobile,
        "competition": comp,
        "issue_score": issue_score,
        "profit_score": profit_score,
        "final_score": final_score,
        "type": typ
    }
