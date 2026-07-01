def detect_type(keyword):
    if "환급" in keyword:
        return "refund"
    if "연금" in keyword:
        return "pension"
    if "보험" in keyword:
        return "insurance"
    if "청약" in keyword:
        return "housing"
    if "세금" in keyword or "세액" in keyword:
        return "tax"
    if "지원금" in keyword:
        return "support"
    return "general"

def make_titles(keyword):
    t = detect_type(keyword)

    search_titles = [
        f"{keyword} 조건과 기준 한 번에 정리",
        f"{keyword} 확인할 때 꼭 알아야 할 핵심 포인트",
        f"{keyword} 처음 알아본다면 헷갈리는 부분 정리"
    ]

    home_titles = [
        f"{keyword}, 그냥 넘기면 손해 볼 수 있는 부분",
        f"{keyword} 알아보다가 가장 많이 헷갈리는 것",
        f"{keyword} 생각보다 차이가 큰 핵심 기준"
    ]

    if t == "refund":
        search_titles = [
            f"{keyword} 대상과 조회 방법 정리",
            f"{keyword} 신청 전 확인해야 할 조건",
            f"{keyword} 받을 수 있는 경우와 확인 방법"
        ]
        home_titles = [
            f"모르고 지나가면 못 받을 수 있는 {keyword}",
            f"{keyword}, 생각보다 놓치는 사람이 많습니다",
            f"나도 받을 수 있을까? {keyword} 확인 포인트"
        ]

    elif t == "pension":
        search_titles = [
            f"{keyword} 조건과 수령 기준 정리",
            f"{keyword} 세금과 수령 방식 차이",
            f"{keyword} 처음 알아볼 때 헷갈리는 부분"
        ]
        home_titles = [
            f"{keyword}, 그냥 받으면 손해일 수도 있습니다",
            f"{keyword} 알아보다가 많이 헷갈리는 기준",
            f"{keyword} 생각보다 차이가 큰 부분"
        ]

    elif t == "insurance":
        search_titles = [
            f"{keyword} 보장 범위와 차이 정리",
            f"{keyword} 비교할 때 확인해야 할 기준",
            f"{keyword} 처음 볼 때 헷갈리는 내용"
        ]
        home_titles = [
            f"{keyword}, 이름은 비슷해도 차이가 큽니다",
            f"{keyword} 확인 전에 놓치기 쉬운 기준",
            f"{keyword} 비교할 때 헷갈리는 부분"
        ]

    return search_titles, home_titles
