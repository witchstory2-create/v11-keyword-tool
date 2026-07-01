# -*- coding: utf-8 -*-
"""
naver_search_api.py (v18.6)
----------------------------------------------------
네이버 API 3종 래퍼 + 개별 연결 테스트 + scorer.py 호환 메서드

[이 파일의 역할]
1) NaverSearchAPI   : 검색 Open API (블로그/뉴스 문서수 조회) -> scorer.py가 사용
2) NaverDataLabAPI  : 데이터랩 검색어트렌드 (DataLab 상승률 조회) -> scorer.py가 사용
3) NaverAdsAPI      : 검색광고 API (검색량/연관검색어/경쟁도 조회) -> scorer.py가 사용

[이 파일이 하지 않는 일]
- 뉴스 수집, 후보 추출 -> collector.py
- 수익형 필터링, 카테고리 가중치 -> profit_filter.py
- 점수 계산, 등급 분류 -> scorer.py
- 화면 표시 -> app.py

[v18.6 변경 사항 - scorer.py 호환성 추가]
기존 저수준 메서드(_search_total, get_keyword_stats, get_spike_ratio 등)는
전혀 수정하지 않고 그대로 유지했다. 다만 scorer.py의 _safe_call()은
"함수가 값을 직접 반환하거나, 실패 시 예외를 던진다"는 방식을 기대하는데
기존 메서드들은 모두 (값, 에러메시지) 튜플을 반환하므로 서로 맞지 않는다.
그래서 아래 4개의 "얇은 래퍼 메서드"를 새로 추가해 이 간극을 메웠다.

    - NaverSearchAPI.get_blog_doc_count(keyword)      -> int 직접 반환 / 실패 시 예외
    - NaverAdsAPI.get_search_volume(keyword)          -> int 직접 반환 / 실패 시 예외
    - NaverAdsAPI.get_related_keywords(keyword, limit)-> list[dict] 직접 반환 / 실패 시 예외
    - NaverDataLabAPI.get_trend_ratio(keyword)         -> float 직접 반환 / 실패 시 예외

이 래퍼들이 던지는 예외는 scorer.py의 _safe_call이 잡아서 ApiHealthTracker에
실패로 기록하므로, app.py 상단의 API 상태 표시등(검색/광고/DataLab)이 분석 실행
중 실제 호출 성공/실패를 정확히 반영하게 된다.

주의: get_blog_doc_count(keyword)는 원래도 이 이름으로 존재하던 메서드였으나,
파일 상단 주석에 이미 "scorer.py가 사용"이라고 명시되어 있었으므로 이번에
반환 방식만 scorer.py 호환 형태(int 직접 반환/실패 시 예외)로 바꾸었다.
기존에 튜플 반환 방식에 의존하는 다른 코드가 있다면 알려주시면 별도 메서드명으로
분리해 드리겠습니다.

표준 라이브러리만 사용 (urllib.request, hmac, hashlib, base64) -> requests 등 외부 패키지 불필요,
PyInstaller onefile 빌드에 안전.
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
        """기존 저수준 메서드 - 수정하지 않음. (총 문서수, 에러메시지) 튜플 반환."""
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

    def get_news_doc_count(self, query):
        """기존 메서드 - 수정하지 않음. (총 문서수, 에러메시지) 튜플 반환."""
        return self._search_total("news", query)

    # ----------------------------------------------------------------
    # [scorer.py 호환 메서드] get_blog_doc_count(keyword) -> int
    # ----------------------------------------------------------------
    def get_blog_doc_count(self, query):
        """
        scorer.py 호환 인터페이스.
        블로그 총 문서수(int)를 직접 반환하며, 조회 실패 시 예외를 발생시킨다.
        (scorer.py의 _safe_call이 이 예외를 잡아 api_health["search"]를 "fail"로 기록한다)
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
        """기존 메서드 - 수정하지 않음. (일별 데이터 리스트, 에러메시지) 반환."""
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

    # ----------------------------------------------------------------
    # [scorer.py 호환 메서드] get_trend_ratio(keyword) -> float
    # ----------------------------------------------------------------
    def get_trend_ratio(self, keyword, days=30, recent_days=3):
        """
        scorer.py 호환 인터페이스.
        최근기간 대비 이전기간 검색관심도 비율(float)을 직접 반환한다.
        (예: 1.6이면 60% 상승, scorer.py의 DATALAB_HARD_CUT=1.3과 비교됨)
        실제 API/네트워크 오류일 때만 예외를 발생시키고, 데이터 자체가 없어 상승/하락을
        판단할 수 없는 경우는 get_spike_ratio가 이미 중립값 1.0을 반환하므로 그대로 넘긴다.
        """
        ok, ratio, err = self.get_spike_ratio(keyword, days=days, recent_days=recent_days)
        if not ok:
            raise Exception(err or "DataLab 조회 실패")
        return ratio


# ------------------------------------------------------------------
# 3) 검색광고 API - 검색량 / 연관검색어 / 경쟁도
#    BASE URL은 반드시 api.searchad.naver.com 이어야 한다.
#    (api.naver.com으로 호출하면 308 Permanent Redirect가 발생함)
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
        """
        기존 get_keyword_stats가 호출하던 것과 동일한 원본 API 응답(keywordList)을
        반환하는 저수준 헬퍼. get_search_volume/get_related_keywords가 함께 재사용한다.
        (raw_list, error) 튜플 반환.
        """
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
        """기존 메서드 - 수정하지 않음. (통계 dict 또는 None, 에러메시지) 튜플 반환."""
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

    # ----------------------------------------------------------------
    # [scorer.py 호환 메서드] get_search_volume(keyword) -> int
    # ----------------------------------------------------------------
    def get_search_volume(self, keyword):
        """
        scorer.py 호환 인터페이스.
        해당 키워드의 월간 PC+모바일 검색량 합계(int)를 직접 반환하며,
        조회 실패 시 예외를 발생시킨다.
        """
        stats, err = self.get_keyword_stats(keyword)
        if stats is None:
            raise Exception(err or "검색량 조회 실패")
        return stats.get("total", 0)

    # ----------------------------------------------------------------
    # [scorer.py 호환 메서드] get_related_keywords(keyword, limit) -> list[dict]
    # ----------------------------------------------------------------
    def get_related_keywords(self, keyword, limit=30):
        """
        scorer.py 호환 인터페이스.
        연관검색어 목록을 [{"keyword": str, "total_volume": int}, ...] 형태로 반환한다.
        원본 키워드 자기 자신 행과 검색량 0인 항목은 제외하고, 검색량 내림차순 상위 limit개만 반환한다.
        조회 자체가 실패하면(네트워크/인증 오류) 예외를 발생시킨다.
        """
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
