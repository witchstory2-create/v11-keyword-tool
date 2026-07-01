# -*- coding: utf-8 -*-
"""
naver_search_api.py (v17.1)
----------------------------------------------------
네이버 API 3종 래퍼 + 개별 연결 테스트
1) NaverSearchAPI   : 검색 Open API (블로그/뉴스 문서수)
2) NaverDataLabAPI  : 데이터랩 검색어트렌드 (상승률)
3) NaverAdsAPI      : 검색광고 API (검색량/경쟁도)

표준 라이브러리만 사용 (urllib.request) -> requests 불필요, PyInstaller onefile 빌드에 안전.
"""

import json
import time
import hmac
import hashlib
import base64
import urllib.request
import urllib.parse
import urllib.error


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


def _diagnose(status, body, kind):
    """HTTP status/body -> 사람이 읽을 수 있는 실패 원인 문자열"""
    if status == 200:
        return "OK"
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


# ------------------------------------------------------------------
# 1) 네이버 검색 API (Open API) - 문서수 조회
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
        url = "https://openapi.naver.com/v1/search/%s.json?query=%s&display=1" % (
            kind, urllib.parse.quote(query))
        status, body = _http_get(url, headers=self._headers())
        if status != 200:
            return None, _diagnose(status, body, "search")
        try:
            j = json.loads(body)
            return int(j.get("total", 0)), None
        except Exception as e:
            return None, f"응답 파싱 오류: {e}"

    def get_blog_doc_count(self, query):
        return self._search_total("blog", query)

    def get_news_doc_count(self, query):
        return self._search_total("news", query)


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
        import datetime
        end = datetime.date.today()
        start = end - datetime.timedelta(days=days)
        payload = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "timeUnit": "date",
            "keywordGroups": [{"groupName": keyword, "keywords": [keyword]}],
        }
        status, body = _http_post_json(
            "https://openapi.naver.com/v1/datalab/search", payload, headers=self._headers())
        if status != 200:
            return None, _diagnose(status, body, "datalab")
        try:
            j = json.loads(body)
            data = j["results"][0]["data"]
            return data, None
        except Exception as e:
            return None, f"응답 파싱 오류: {e}"

    def get_spike_ratio(self, keyword, days=30, recent_days=3):
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


# ------------------------------------------------------------------
# 3) 검색광고 API - 검색량 / 경쟁도
# ------------------------------------------------------------------
class NaverAdsAPI:
    BASE = "https://api.naver.com"

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

    def get_keyword_stats(self, keyword):
        uri = "/keywordstool"
        query = "hintKeywords=%s&showDetail=1" % urllib.parse.quote(keyword)
        url = self.BASE + uri + "?" + query
        headers = self._headers("GET", uri)
        status, body = _http_get(url, headers=headers)
        if status != 200:
            return None, _diagnose(status, body, "ads")
        try:
            j = json.loads(body)
            lst = j.get("keywordList", [])
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
        except Exception as e:
            return None, f"응답 파싱 오류: {e}"
