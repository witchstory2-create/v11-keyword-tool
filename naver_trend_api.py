# naver_trend_api.py
# [NEW] 네이버 데이터랩 통합검색어트렌드 API 연동
# 목적: 뉴스 언급량(mentions)만으로 "HOT 이슈"를 판단하던 기존 방식의 한계를 보완.
#       실제 검색 지수가 "평소 대비 얼마나 급증했는지(spike_ratio)"를 계산해서
#       상시성 키워드(신용대출, 보험료 등)가 HOT 이슈로 오분류되는 것을 방지한다.

import json
import urllib.request
import datetime

DATALAB_URL = "https://openapi.naver.com/v1/datalab/search"


def get_search_trend(keyword: str, client_id: str, client_secret: str, days: int = 14) -> dict:
    """
    최근 `days`일간의 검색어트렌드 지수를 가져와서
    - recent_avg: 최근 2일 평균 지수
    - baseline_avg: 그 이전 기간 평균 지수
    - spike_ratio: recent_avg / baseline_avg (평소 대비 몇 배 급증했는지)
    를 계산해서 반환한다.

    API 자체는 하루 1000회 호출 제한이 있으므로, TOP 후보 전체가 아니라
    이미 1차로 걸러진 TOP 10~20개 정도에만 호출하는 것을 권장.
    """
    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=days)

    body = {
        "startDate": start_date.strftime("%Y-%m-%d"),
        "endDate": end_date.strftime("%Y-%m-%d"),
        "timeUnit": "date",
        "keywordGroups": [
            {"groupName": keyword, "keywords": [keyword]}
        ],
    }

    request = urllib.request.Request(DATALAB_URL)
    request.add_header("X-Naver-Client-Id", client_id)
    request.add_header("X-Naver-Client-Secret", client_secret)
    request.add_header("Content-Type", "application/json")

    try:
        response = urllib.request.urlopen(request, data=json.dumps(body).encode("utf-8"), timeout=5)
        result = json.loads(response.read().decode("utf-8"))
    except Exception as e:
        print("데이터랩 API 오류:", keyword, e)
        return {"spike_ratio": 1.0, "recent_avg": 0, "baseline_avg": 0, "trend_available": False}

    try:
        data_points = result["results"][0]["data"]  # [{period: "2026-06-20", ratio: 23.4}, ...]
        ratios = [point["ratio"] for point in data_points]
    except (KeyError, IndexError):
        return {"spike_ratio": 1.0, "recent_avg": 0, "baseline_avg": 0, "trend_available": False}

    if len(ratios) < 4:
        return {"spike_ratio": 1.0, "recent_avg": 0, "baseline_avg": 0, "trend_available": False}

    recent = ratios[-2:]          # 최근 2일
    baseline = ratios[:-2]        # 그 이전 기간

    recent_avg = sum(recent) / len(recent) if recent else 0
    baseline_avg = sum(baseline) / len(baseline) if baseline else 0

    if baseline_avg <= 0.01:
        # 이전 기간에 검색 지수가 거의 없었는데 최근에 생겼다면 -> 완전히 신규 이슈
        spike_ratio = 3.0 if recent_avg > 1 else 1.0
    else:
        spike_ratio = recent_avg / baseline_avg

    return {
        "spike_ratio": round(spike_ratio, 2),
        "recent_avg": round(recent_avg, 1),
        "baseline_avg": round(baseline_avg, 1),
        "trend_available": True,
    }
