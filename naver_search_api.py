# -*- coding: utf-8 -*-
"""
naver_search_api.py (v18.9)
----------------------------------------------------
네이버 API 3종 래퍼 + 개별 연결 테스트 + collector.py/scorer.py 호환 메서드

[v18.8에서 유지된 것]
- NaverSearchAPI.search_news(), get_blog_doc_count()
- NaverAdsAPI.get_search_volume(), get_related_keywords()
- NaverDataLabAPI.get_trend_ratio()
- 모든 저수준 진단/연결테스트 로직(_diagnose, test_connection 3종 등)
- 429/timeout 재시도 로직(_http_get_retry, _http_post_json_retry)

[v18.9 변경 사항 - app.py 연동용 재시도 통계 노출]
app.py가 분석 시작/종료 시점에 429·timeout 발생 횟수를 확인할 수 있도록
모듈 최상위에 reset_retry_stats() / get_retry_stats() 두 함수만 추가했다.
기존 함수/클래스는 전혀 수정하지 않았고, 재시도가 실제로 발생하는
_http_get_retry / _http_post_json_retry 내부에서 카운터를 증가시키는
코드만 끼워넣었다(반환값 형식은 동일하게 유지).

표준 라이브러리만 사용 (urllib.request, hmac, hashlib, base64) -> requests 등 외부 패키지 불필요,
PyInstaller onefile 빌드에 안전.
"""

import json
import time
import hmac
import hashlib
import base64
import threading
import urllib.request
import urllib.parse
import urllib.error


# ------------------------------------------------------------------
# [v18.9 신규] 429 / timeout 재시도 통계 (모듈 최상위, 스레드-세이프)
# ------------------------------------------------------------------
_retry_stats_lock = threading.Lock()
_retry_stats = {"count_429": 0, "count_timeout": 0}


def reset_retry_stats():
    """분석 시작 시 호출: 누적된 429 / timeout 재시도 카운터를 0으로 초기화한다."""
    with _retry_stats_lock:
        _retry_stats["count_429"] = 0
        _retry_stats["count_timeout"] = 0


def get_retry_stats():
    """분석 종료 후 호출: 누적된 429 / timeout 재시도 횟수를 dict로 반환한다."""
    with _retry_stats_lock:
        return dict(_retry_stats)


def _record_429():
    with _retry_stats_lock:
        _retry_stats["count_429"] += 1


def _record_timeout():
    with _retry_stats_lock:
        _retry_stats["count_timeout"] += 1


def _http_get(url, headers=None, timeout=6):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            body = res.read().decode("utf-8", errors="replace")
            return res.status, body
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return e.code, body
    except urllib.error.URLError as e:
        return -1, str(e.reason)
    except Exception as e:
        return -2, str(e)


def _http_post_json(url, payload, headers=None, timeout=6):
    data = json.dumps(payload).encode("utf-8")
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            body = res.read().decode("utf-8", errors="replace")
            return res.status, body
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return e.code, body
    except urllib.error.URLError as e:
        return -1, str(e.reason)
    except Exception as e:
        return -2, str(e)


# ------------------------------------------------------------------
# 429 / timeout 재시도 래퍼
# ------------------------------------------------------------------
def _sleep_backoff_429(attempt):
    """429 재시도 대기 시간: 1회차 2초, 2회차 5초, 3회차(이후) 10초."""
    steps = (2, 5, 10)
    idx = min(attempt, len(steps) - 1)
    time.sleep(steps[idx])


def _is_timeout_error(status, body):
    if status not in (-1, -2):
        return False
    b = str(body).lower()
    return "timed out" in b or "timeout" in b


def _http_get_retry(url, headers=None, timeout=6, max_429_retries=3, max_timeout_retries=2):
    """
    _http_get을 감싸 429(호출 한도 초과)와 timeout(응답 지연) 상황에서만 재시도한다.
    200/401/403/404 등 재시도해도 결과가 바뀌지 않는 상태코드는 즉시 그대로 반환한다.
    [v18.9] 재시도가 실제로 발생할 때마다 모듈 최상위 통계(_retry_stats)를 증가시킨다.
    """
    tries_429 = 0
    tries_timeout = 0
    while True:
        status, body = _http_get(url, headers=headers, timeout=timeout)
        if status == 429 and tries_429 < max_429_retries:
            _record_429()
            _sleep_backoff_429(tries_429)
            tries_429 += 1
            continue
        if _is_timeout_error(status, body) and tries_timeout < max_timeout_retries:
            _record_timeout()
            time.sleep(1.0)
            tries_timeout += 1
            continue
        return status, body


def _http_post_json_retry(url, payload, headers=None, timeout=10, max_429_retries=3, max_timeout_retries=2):
    """[v18.9] 재시도가 실제로 발생할 때마다 모듈 최상위 통계(_retry_stats)를 증가시킨다."""
    tries_429 = 0
    tries_timeout = 0
    while True:
        status, body = _http_post_json(url, payload, headers=headers, timeout=timeout)
        if status == 429 and tries_429 < max_429_retries:
            _record_429()
            _sleep_backoff_429(tries_429)
            tries_429 += 1
            continue
        if _is_timeout_error(status, body) and tries_timeout < max_timeout_retries:
            _record_timeout()
            time.sleep(1.0)
            tries_timeout += 1
            continue
        return status, body


def _diagnose(status, body, kind):
    """HTTP status/body -> 사람이 읽을 수 있는 실패 원인 문자열"""
    if status == 200:
        return "OK"
    if status in (301, 302, 307, 308):
        return f"{status} 리다이렉트 - 호출 주소(BASE URL)가 잘못되었습니다."
    if status == 401:
        if kind == "search":
            return "401 인증 실패 - 검색 API Client ID/Secret 값을 확인하세요."
        if kind == "datalab":
            return "401 인증 실패 - 데이터랩 Client ID/Secret 값을 확인하세요."
        if kind == "ads":
            return "401 인증 실패 - License Key 또는 Secret Key 값을 확인하세요."
    if status == 403:
        return "403 권한 없음 - 해당 애플리케이션에 이 API 사용 권한이 추가되지 않았습니다."
    if status == 404:
        if kind == "ads":
            return "404 Customer ID 오류 - Customer ID 값을 확인하세요(숫자만)."
        return "404 요청 주소 오류"
    if status == 429:
        return "429 호출 한도 초과 - 잠시 후 다시 시도하세요."
    if status == -1:
        return f"네트워크 오류(URLError): {body}"
    if status == -2:
        return f"알 수 없는 오류: {body}"
    if body:
        try:
            j = json.loads(body)
            msg = j.get("errorMessage") or j.get("message") or j.get("errorCode")
            if msg:
                return f"HTTP {status} - {msg}"
        except Exception:
            pass
    return f"HTTP {status} - 알 수 없는 오류"


def _to_int(v):
    """네이버 API가 종종 '< 10' 같은 문자열로 값을 주는 경우를 안전하게 정수로 변환."""
    if isinstance(v, str):
        v = v.replace("< ", "").replace(",", "").strip()
        if v == "" or not v.isdigit():
            return 0
        return int(v)
    try:
        return int(v)
    except Exception:
        return 0


# ------------------------------------------------------------------
# 1) 네이버 검색 API (Open API) - 뉴스 수집 / 문서수 조회
# ------------------------------------------------------------------
class NaverSearchAPI:
    def __init__(self, client_id, client_secret):
        self.client_id = (client_id or "").strip()
        self.client_secret = (client_secret or "").strip()

    def _headers(self):
        return {
            "X-Naver-Client-Id": self.client_id,
            "X-Naver-Client-Secret": self.client_secret,
        }

    def _sanity_check(self):
        if self.client_id.isdigit() and len(self.client_id) <= 10:
            return ("검색 API Client ID 값이 숫자로만 되어 있어 의심스럽습니다. "
                    "검색광고 Customer ID 값과 바뀐 것은 아닌지 확인하세요.")
        if len(self.client_secret) < 6:
            return "검색 API Client Secret 값이 너무 짧습니다. 값을 다시 확인하세요."
        return None

    def test_connection(self):
        warn = self._sanity_check()
        if not self.client_id or not self.client_secret:
            return False, "Client ID/Secret이 입력되지 않았습니다.", warn or ""
        url = "https://openapi.naver.com/v1/search/blog.json?query=%s&display=1" % urllib.parse.quote("테스트")
        status, body = _http_get(url, headers=self._headers())
        if status == 200:
            return True, "✅ 연결 성공", warn or ""
        return False, "❌ " + _diagnose(status, body, "search"), warn or ""

    def _search_total(self, kind, query):
        """
        429/timeout 재시도 래퍼(_http_get_retry) 적용.
        기존 반환 형식((총 문서수, 에러메시지) 튜플)은 그대로 유지했다.
        """
        url = "https://openapi.naver.com/v1/search/%s.json?query=%s&display=1" % (
            kind, urllib.parse.quote(query))
        status, body = _http_get_retry(url, headers=self._headers(), timeout=8)
        if status != 200:
            return None, _diagnose(status, body, "search")
        try:
            j = json.loads(body)
            return int(j.get("total", 0)), None
        except Exception as e:
            return None, f"응답 파싱 오류: {e}"

    def get_news_doc_count(self, query):
        """기존 메서드 - 반환 형식 변경 없음(내부적으로 재시도 적용됨)."""
        return self._search_total("news", query)

    def search_news(self, query, display=100, start=1, sort="date"):
        """
        collector.py의 _fetch_seed_news()가 호출하는 뉴스 검색 메서드.
        v18.7과 동일하게 유지 (재시도 로직 미적용 - 수집 단계는 429 이슈가 없었음).
        """
        display = max(1, min(int(display), 100))
        start = max(1, min(int(start), 1000))

        params = urllib.parse.urlencode({
            "query": query,
            "display": display,
            "start": start,
            "sort": sort,
        })
        url = "https://openapi.naver.com/v1/search/news.json?" + params
        status, body = _http_get(url, headers=self._headers())
        if status != 200:
            raise Exception(_diagnose(status, body, "search"))
        try:
            j = json.loads(body)
        except Exception as e:
            raise Exception(f"응답 파싱 오류: {e}")

        items = j.get("items", [])
        result = []
        for item in items:
            result.append({
                "title": item.get("title", ""),
                "description": item.get("description", ""),
                "link": item.get("link", ""),
                "originallink": item.get("originallink", ""),
                "pubDate": item.get("pubDate", ""),
            })
        return result

    def get_blog_doc_count(self, query):
        """
        scorer.py 호환 인터페이스.
        _search_total 내부에 재시도가 적용되어 429/timeout에 더 강해졌다.
        """
        count, err = self._search_total("blog", query)
        if count is None:
            raise Exception(err or "블로그 문서수 조회 실패")
        return count


# ------------------------------------------------------------------
# 2) 데이터랩 API - 검색어 트렌드(상승률)
# ------------------------------------------------------------------
class NaverDataLabAPI:
    def __init__(self, client_id, client_secret):
        self.client_id = (client_id or "").strip()
        self.client_secret = (client_secret or "").strip()

    def _headers(self):
        return {
            "X-Naver-Client-Id": self.client_id,
            "X-Naver-Client-Secret": self.client_secret,
        }

    def test_connection(self):
        if not self.client_id or not self.client_secret:
            return False, "Client ID/Secret이 입력되지 않았습니다.", ""
        ok, ratio, err = self.get_spike_ratio("테스트")
        if ok:
            return True, "✅ 연결 성공", ""
        hint = ""
        if err and ("024" in err or "권한" in err):
            hint = "네이버 개발자센터 애플리케이션 설정에서 '데이터랩(검색어트렌드)' API 사용을 추가했는지 확인하세요."
        return False, "❌ " + (err or "알 수 없는 오류"), hint

    def get_trend(self, keyword, days=30):
        """
        timeout 6초 -> 12초 상향, 429/timeout 재시도 래퍼 적용.
        반환 형식((일별 데이터 리스트, 에러메시지) 튜플)은 그대로 유지했다.
        """
        import datetime
        end = datetime.date.today()
        start = end - datetime.timedelta(days=days)
        payload = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "timeUnit": "date",
            "keywordGroups": [{"groupName": keyword, "keywords": [keyword]}],
        }
        status, body = _http_post_json_retry(
            "https://openapi.naver.com/v1/datalab/search", payload,
            headers=self._headers(), timeout=12
        )
        if status != 200:
            return None, _diagnose(status, body, "datalab")
        try:
            j = json.loads(body)
            data = j["results"][0]["data"]
            return data, None
        except Exception as e:
            return None, f"응답 파싱 오류: {e}"

    def get_spike_ratio(self, keyword, days=30, recent_days=3):
        """기존 메서드 - 수정하지 않음. (성공여부, 상승비율, 에러메시지) 튜플 반환."""
        data, err = self.get_trend(keyword, days=days)
        if data is None:
            return False, None, err
        if not data:
            return True, 1.0, None
        ratios = [d.get("ratio", 0) for d in data]
        if len(ratios) <= recent_days:
            recent = ratios[-1:]
            base = ratios[:-1] or [1]
        else:
            recent = ratios[-recent_days:]
            base = ratios[:-recent_days]
        recent_avg = sum(recent) / max(len(recent), 1)
        base_avg = sum(base) / max(len(base), 1)
        if base_avg <= 0:
            base_avg = 0.1
        spike = recent_avg / base_avg
        return True, round(spike, 3), None

    def get_trend_ratio(self, keyword, days=30, recent_days=3):
        """
        scorer.py 호환 인터페이스.
        get_trend 내부 재시도/timeout 상향이 적용되어 이전보다 timeout 실패가 줄어든다.
        여전히 실패하면 예외를 던지며, scorer.py는 이 경우 중립값(1.0)으로 대체 처리한다.
        """
        ok, ratio, err = self.get_spike_ratio(keyword, days=days, recent_days=recent_days)
        if not ok:
            raise Exception(err or "DataLab 조회 실패")
        return ratio


# ------------------------------------------------------------------
# 3) 검색광고 API - 검색량 / 연관검색어 / 경쟁도 (v18.7과 완전히 동일 - 미수정)
# ------------------------------------------------------------------
class NaverAdsAPI:
    BASE = "https://api.searchad.naver.com"

    def __init__(self, customer_id, license_key, secret_key):
        self.customer_id = (customer_id or "").strip()
        self.license_key = (license_key or "").strip()
        self.secret_key = (secret_key or "").strip()

    def _signature(self, timestamp, method, uri):
        message = f"{timestamp}.{method}.{uri}"
        h = hmac.new(self.secret_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256)
        return base64.b64encode(h.digest()).decode("utf-8")

    def _headers(self, method, uri):
        timestamp = str(int(time.time() * 1000))
        return {
            "X-Timestamp": timestamp,
            "X-API-KEY": self.license_key,
            "X-Customer": self.customer_id,
            "X-Signature": self._signature(timestamp, method, uri),
        }

    def _sanity_check(self):
        if self.customer_id and not self.customer_id.isdigit():
            return ("Customer ID 값에 숫자가 아닌 문자가 포함되어 있습니다. "
                    "검색 API Client ID 값과 바뀐 것은 아닌지 확인하세요.")
        return None

    def test_connection(self):
        warn = self._sanity_check()
        if not self.customer_id or not self.license_key or not self.secret_key:
            return False, "Customer ID / License Key / Secret Key 중 누락된 값이 있습니다.", warn or ""
        uri = "/keywordstool"
        query = "hintKeywords=%s&showDetail=1" % urllib.parse.quote("테스트")
        url = self.BASE + uri + "?" + query
        headers = self._headers("GET", uri)
        status, body = _http_get(url, headers=headers)
        if status == 200:
            return True, "✅ 연결 성공", warn or ""
        return False, "❌ " + _diagnose(status, body, "ads"), warn or ""

    def _fetch_keywordstool(self, keyword):
        uri = "/keywordstool"
        query = "hintKeywords=%s&showDetail=1" % urllib.parse.quote(keyword)
        url = self.BASE + uri + "?" + query
        headers = self._headers("GET", uri)
        status, body = _http_get(url, headers=headers)
        if status != 200:
            return None, _diagnose(status, body, "ads")
        try:
            j = json.loads(body)
            return j.get("keywordList", []), None
        except Exception as e:
            return None, f"응답 파싱 오류: {e}"

    def get_keyword_stats(self, keyword):
        lst, err = self._fetch_keywordstool(keyword)
        if lst is None:
            return None, err
        if not lst:
            return {"pc": 0, "mobile": 0, "total": 0, "comp_idx": "낮음"}, None
        top = lst[0]

        def _num(v):
            if isinstance(v, str):
                v = v.replace("< ", "").replace(",", "").strip()
                if v == "" or not v.isdigit():
                    return 0
                return int(v)
            try:
                return int(v)
            except Exception:
                return 0

        pc = _num(top.get("monthlyPcQcCnt", 0))
        mobile = _num(top.get("monthlyMobileQcCnt", 0))
        comp = top.get("compIdx", "낮음")
        return {"pc": pc, "mobile": mobile, "total": pc + mobile, "comp_idx": comp}, None

    def get_search_volume(self, keyword):
        stats, err = self.get_keyword_stats(keyword)
        if stats is None:
            raise Exception(err or "검색량 조회 실패")
        return stats.get("total", 0)

    def get_related_keywords(self, keyword, limit=30):
        lst, err = self._fetch_keywordstool(keyword)
        if lst is None:
            raise Exception(err or "연관검색어 조회 실패")

        norm_keyword = keyword.replace(" ", "")
        results = []
        for item in lst:
            rel = (item.get("relKeyword") or "").strip()
            if not rel or rel.replace(" ", "") == norm_keyword:
                continue
            pc = _to_int(item.get("monthlyPcQcCnt", 0))
            mobile = _to_int(item.get("monthlyMobileQcCnt", 0))
            total = pc + mobile
            if total <= 0:
                continue
            results.append({"keyword": rel, "total_volume": total})

        results.sort(key=lambda r: -r["total_volume"])
        return results[:limit]
