# naver_trend_api.py
# v2 - 데이터랩 검색어트렌드 API 연동 + 파일 로그 기능 추가
# 변경 사항(2026-07):
#   실행파일(exe)로만 쓰는 경우 콘솔 창이 없어서 오류가 나도 확인할 방법이 없었음.
#   이제부터는 API 호출이 실패하거나 예상과 다른 응답이 오면, 앱이 실행되는 같은 폴더에
#   trend_debug_log.txt 라는 텍스트 파일을 만들어서 원인을 그대로 기록한다.
#   (파일은 자동으로 계속 누적되며, 문제 없을 때는 아무것도 기록하지 않는다.)

import json
import urllib.request
import datetime
import os

DATALAB_URL = "https://openapi.naver.com/v1/datalab/search"

LOG_FILE_PATH = os.path.join(os.getcwd(), "trend_debug_log.txt")


def _write_log(message: str):
    """콘솔이 없는 실행파일 환경에서도 원인을 확인할 수 있도록 파일에 기록한다."""
    try:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")
    except Exception:
        pass  # 로그 기록 자체가 실패해도 앱은 계속 동작해야 함


def get_search_trend(keyword: str, client_id: str, client_secret: str, days: int = 14) -> dict:
    """
    최근 `days`일간의 검색어트렌드 지수를 가져와서
    - recent_avg: 최근 2일 평균 지수
    - baseline_avg: 그 이전 기간 평균 지수
    - spike_ratio: recent_avg / baseline_avg (평소 대비 몇 배 급증했는지)
    를 계산해서 반환한다. 실패하면 이유를 trend_debug_log.txt에 기록한다.
    """
    if not client_id or not client_secret:
        return {"spike_ratio": 1.0, "recent_avg": 0, "baseline_avg": 0, "trend_available": False}

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
        raw_body = response.read().decode("utf-8")
        result = json.loads(raw_body)
    except urllib.error.HTTPError as e:
        # 401(키 오류), 429(호출 한도 초과) 등 HTTP 오류를 구체적으로 기록
        try:
            error_body = e.read().decode("utf-8")
        except Exception:
            error_body = "(응답 내용 없음)"
        _write_log(f"[HTTP 오류] 키워드='{keyword}' 상태코드={e.code} 응답내용={error_body}")
        return {"spike_ratio": 1.0, "recent_avg": 0, "baseline_avg": 0, "trend_available": False}
    except Exception as e:
        _write_log(f"[연결 오류] 키워드='{keyword}' 오류내용={repr(e)}")
        return {"spike_ratio": 1.0, "recent_avg": 0, "baseline_avg": 0, "trend_available": False}

    try:
        data_points = result["results"][0]["data"]  # [{period: "2026-06-20", ratio: 23.4}, ...]
        ratios = [point["ratio"] for point in data_points]
    except (KeyError, IndexError) as e:
        _write_log(f"[응답 구조 오류] 키워드='{keyword}' 응답내용={result} 오류={repr(e)}")
        return {"spike_ratio": 1.0, "recent_avg": 0, "baseline_avg": 0, "trend_available": False}

    if len(ratios) < 4:
        _write_log(f"[데이터 부족] 키워드='{keyword}' 받은 날짜 수={len(ratios)}개 (최소 4개 필요) - 검색량 자체가 거의 없는 키워드일 가능성")
        return {"spike_ratio": 1.0, "recent_avg": 0, "baseline_avg": 0, "trend_available": False}

    recent = ratios[-2:]          # 최근 2일
    baseline = ratios[:-2]        # 그 이전 기간

    recent_avg = sum(recent) / len(recent) if recent else 0
    baseline_avg = sum(baseline) / len(baseline) if baseline else 0

    if baseline_avg <= 0.01:
        spike_ratio = 3.0 if recent_avg > 1 else 1.0
    else:
        spike_ratio = recent_avg / baseline_avg

    return {
        "spike_ratio": round(spike_ratio, 2),
        "recent_avg": round(recent_avg, 1),
        "baseline_avg": round(baseline_avg, 1),
        "trend_available": True,
    }
