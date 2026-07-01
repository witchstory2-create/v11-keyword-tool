import random

TEMPLATE_POOL = {
    "refund": {
        "search": [
            "{kw} 대상과 조회 방법 정리",
            "{kw} 신청 전 확인해야 할 조건",
            "{kw} 받을 수 있는 경우와 확인 방법",
            "{kw} 언제까지 신청 가능한지 정리",
            "{kw} 대상 여부 5분 만에 확인하는 법",
        ],
        "home": [
            "모르고 지나가면 못 받을 수 있는 {kw}",
            "{kw}, 생각보다 놓치는 사람이 많습니다",
            "나도 받을 수 있을까? {kw} 확인 포인트",
            "{kw}, 신청 안 하면 그냥 사라집니다",
        ],
    },
    "housing": {
        "search": [
            "{kw} 조건과 자격 기준 정리",
            "{kw} 신청 전 확인해야 할 서류",
            "{kw} 가점 계산법과 유의사항",
        ],
        "home": [
            "{kw}, 조건 하나 놓치면 탈락합니다",
            "{kw} 신청 전 꼭 확인해야 할 것",
        ],
    },
    "tax": {
        "search": [
            "{kw} 공제 대상과 조건 정리",
            "{kw} 환급받는 방법 총정리",
            "{kw} 확인 안 하면 놓치는 부분",
        ],
        "home": [
            "{kw}, 몰라서 못 받는 돈 있습니다",
            "{kw} 이렇게 하면 더 돌려받습니다",
        ],
    },
    "support": {
        "search": [
            "{kw} 대상 조건과 신청 방법",
            "{kw} 지급 일정과 확인 방법",
        ],
        "home": [
            "{kw}, 대상인지 아닌지 헷갈린다면",
            "{kw} 신청 안 하면 손해입니다",
        ],
    },
    "pension": {
        "search": [
            "{kw} 조건과 수령 기준 정리",
            "{kw} 세금과 수령 방식 차이",
        ],
        "home": [
            "{kw}, 그냥 받으면 손해일 수도 있습니다",
            "{kw} 알아보다가 많이 헷갈리는 기준",
        ],
    },
    "insurance": {
        "search": [
            "{kw} 보장 범위와 차이 정리",
            "{kw} 비교할 때 확인해야 할 기준",
        ],
        "home": [
            "{kw}, 이름은 비슷해도 차이가 큽니다",
            "{kw} 확인 전에 놓치기 쉬운 기준",
        ],
    },
    "general": {
        "search": [
            "{kw} 조건과 기준 한 번에 정리",
            "{kw} 확인할 때 꼭 알아야 할 핵심 포인트",
            "{kw}란 무엇이고 어떻게 확인할까?",
            "{kw} A와 B, 무엇이 다를까?",
        ],
        "home": [
            "{kw}, 그냥 넘기면 손해 볼 수 있는 부분",
            "{kw} 알아보다가 가장 많이 헷갈리는 것",
        ],
    },
}


def detect_type(keyword):
    mapping = {
        "환급": "refund", "연금": "pension", "보험": "insurance",
        "청약": "housing", "세금": "tax", "세액": "tax", "지원금": "support",
    }
    for key, typ in mapping.items():
        if key in keyword:
            return typ
    return "general"


def make_titles(keyword, n=3, seed=None):
    typ = detect_type(keyword)
    pool = TEMPLATE_POOL.get(typ, TEMPLATE_POOL["general"])
    rng = random.Random(seed)

    search_pool = list(dict.fromkeys(pool["search"] + TEMPLATE_POOL["general"]["search"]))
    home_pool = list(dict.fromkeys(pool["home"] + TEMPLATE_POOL["general"]["home"]))

    search_titles = [t.format(kw=keyword) for t in rng.sample(search_pool, min(n, len(search_pool)))]
    home_titles = [t.format(kw=keyword) for t in rng.sample(home_pool, min(n, len(home_pool)))]

    return search_titles, home_titles
