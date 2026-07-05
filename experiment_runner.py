"""
experiment_runner.py
---------------------
scorer.py(v6)의 analyze_news()를 10개 뉴스에 반복 실행하는 배치 러너 +
사람이 채울 실험 로그 템플릿(v1.0) 생성기.

- scorer.py 로직은 수정하지 않고 그대로 호출만 함
- 콘솔 출력에 v6 신규 필드(core_summary, event, questions, outline) 포함
"""

from scorer import analyze_news

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False


# -----------------------------
# 1. 테스트 데이터 (10개 뉴스)
# -----------------------------

sample_news = [
    {
        "id": 1,
        "title": "정부, 청년 월세 지원금 신청 접수 시작",
        "summary": "국토교통부는 오늘부터 청년 월세 지원금 신청 접수를 시작한다고 밝혔다. 지원 대상은 만 19세~34세 무주택 청년으로, 신청은 정부24에서 가능하며 지원 기간은 8월 말까지다.",
        "source": "연합뉴스",
    },
    {
        "id": 2,
        "title": "인기 배우 A, 열애설에 결국 소속사 입장 발표",
        "summary": "배우 A의 열애설이 확산되자 소속사가 공식 입장을 냈다. 팬들 반응이 엇갈리는 가운데 추가 보도가 이어지고 있다.",
        "source": "스포츠투데이",
    },
    {
        "id": 3,
        "title": "서울 강남구서 화재, 인명피해 없어",
        "summary": "오늘 오전 서울 강남구 한 건물에서 화재가 발생했다. 소방당국은 신속히 진압했으며 인명피해는 없는 것으로 확인됐다.",
        "source": "뉴시스",
    },
    {
        "id": 4,
        "title": "여름철 전기요금 폭탄 피하는 법, 절약 노하우 총정리",
        "summary": "무더위로 냉방기기 사용이 늘면서 전기요금 부담이 커지고 있다. 전문가들은 사용 시간 조절과 절약 요금제 가입을 조언한다.",
        "source": "머니투데이",
    },
    {
        "id": 5,
        "title": "복리와 단리, 무슨 차이일까? 기초 재테크 개념 정리",
        "summary": "금융 초보자들이 헷갈리는 복리와 단리의 개념 차이를 정리했다. 장기 투자에서 복리 효과가 어떻게 작동하는지 설명한다.",
        "source": "이코노미조선",
    },
    {
        "id": 6,
        "title": "추석 연휴 고속도로 통행료 면제, 일정과 대상 확인",
        "summary": "올해도 추석 연휴 기간 고속도로 통행료가 면제된다. 정확한 면제 일정과 하이패스 이용 방법을 안내한다.",
        "source": "국토일보",
    },
    {
        "id": 7,
        "title": "유명 유튜버 B, 논란 영상 삭제 후 사과문 게재",
        "summary": "유튜버 B가 최근 논란이 된 영상을 삭제하고 사과문을 올렸다. 구독자들의 반응이 갈리며 재조명되고 있다.",
        "source": "디스패치",
    },
    {
        "id": 8,
        "title": "실업급여 신청 자격과 조건, 2026년 기준 총정리",
        "summary": "고용노동부가 발표한 실업급여 신청 자격 기준을 정리했다. 고용보험 가입 기간과 이직 사유에 따른 수급 조건을 안내한다.",
        "source": "고용노동부 보도자료",
    },
    {
        "id": 9,
        "title": "교차로 신호 위반 단속 카메라, 새로 20곳 설치",
        "summary": "경찰청은 교차로 신호 위반이 잦은 지역 20곳에 단속 카메라를 새로 설치한다고 밝혔다. 설치 지역과 단속 방식을 안내한다.",
        "source": "경찰청 보도자료",
    },
    {
        "id": 10,
        "title": "연봉 실수령액 계산법, 4대보험 공제 기준 정리",
        "summary": "연봉 협상 시즌을 맞아 실수령액 계산 방법에 대한 관심이 높다. 4대보험과 소득세 공제 기준을 반영한 계산법을 정리했다.",
        "source": "잡플래닛",
    },
]


# -----------------------------
# 2. 배치 실행 + 콘솔 출력
# -----------------------------

def run_batch(news_list: list) -> list:
    results = []

    for news in news_list:
        result = analyze_news(news["title"], news["summary"])

        print(f"\n===== Run #{news['id']} =====")
        print(f"제목 : {news['title']}")
        print(f"출처 : {news['source']}")
        print(f"핵심요약 : {result['analysis']['core_summary']}")
        print(f"토픽 : {result['analysis']['topic']}")
        print(f"키워드 : {result['analysis']['keywords']}")
        print(f"메인키워드 : {result['analysis']['main_keyword']}")
        print(f"연관키워드 : {result['analysis']['related_keywords']}")
        print(f"행동의도 : {result['analysis']['action_intents']}")
        print(f"이벤트(event) : {result['analysis']['event']}")
        print(f"질문(questions) : {result['analysis']['questions']}")
        print(f"목차(outline) : {result['analysis']['outline']}")
        print(f"전략 : {result['strategy']['type']} - {result['strategy']['reason']}")
        print(f"SEO 제목 : {result['titles']['seo_titles']}")
        print(f"클릭베이트 제목 : {result['titles']['clickbait_titles']}")
        print(f"혼합형 제목 : {result['titles']['hybrid_titles']}")

        results.append({
            "run_id": news["id"],
            "title": news["title"],
            "source": news["source"],
            "news": news,
            "analysis_result": result,
        })

    return results


# -----------------------------
# 3. 실험 로그 템플릿 생성 (v1.0)
# -----------------------------

LOG_COLUMNS = [
    "Run #",
    "뉴스 제목",
    "출처",
    "경쟁등급(0-2/3-7/8+)",
    "애매함(Y/N)",
    "수명(즉시/단기/시즌/에버그린)",
    "수익(Low/Medium/High)",
    "판단(Write/Skip/Test)",
    "망설임 포인트",
]


def build_log_rows(batch_results: list) -> list:
    rows = []
    for item in batch_results:
        rows.append({
            "Run #": item["run_id"],
            "뉴스 제목": item["title"],
            "출처": item["source"],
            "경쟁등급(0-2/3-7/8+)": "",
            "애매함(Y/N)": "",
            "수명(즉시/단기/시즌/에버그린)": "",
            "수익(Low/Medium/High)": "",
            "판단(Write/Skip/Test)": "",
            "망설임 포인트": "",
        })
    return rows


def print_log_markdown(rows: list):
    header = "| " + " | ".join(LOG_COLUMNS) + " |"
    divider = "|" + "|".join(["---"] * len(LOG_COLUMNS)) + "|"
    print("\n" + header)
    print(divider)
    for row in rows:
        print("| " + " | ".join(str(row[col]) for col in LOG_COLUMNS) + " |")


def save_log_csv(rows: list, filepath: str = "experiment_log_v1.csv"):
    if HAS_PANDAS:
        df = pd.DataFrame(rows, columns=LOG_COLUMNS)
        df.to_csv(filepath, index=False, encoding="utf-8-sig")
    else:
        import csv
        with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=LOG_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

    print(f"\n[저장 완료] 실험 로그 템플릿 -> {filepath}")


# -----------------------------
# 4. 메인 실행
# -----------------------------

if __name__ == "__main__":
    batch_results = run_batch(sample_news)

    log_rows = build_log_rows(batch_results)

    print("\n\n========== 실험 로그 템플릿 (v1.0) ==========")
    print_log_markdown(log_rows)

    save_log_csv(log_rows)
