# -*- coding: utf-8 -*-
"""
naver_search_api.py (v17 - 수익형 키워드 발굴기)

1) NaverOpenAPI : developers.naver.com 발급 (Client ID / Client Secret)
   - 블로그 검색 문서수(totalCount)  -> 포화도 계산용
   - 뉴스 검색(search_news)          -> Discovery 소스 확장용
   - 데이터랩 검색어트렌드(상대 지수) -> 스파이크율 계산용

2) NaverAdAPI   : ads.naver.com 검색광고 > 도구 > API 사용 관리에서 발급
                  (API License Key / Secret Key / Customer ID)
   - 연관키워드 조회 -> 월간 검색량(PC+모바일), 경쟁정도(compIdx)
"""

import os
import sys
import json
import time
import hmac
import base64
import hashlib
import urllib.request
import urllib.parse
from datetime import datetime, timedelta

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) KeywordTool/17.0"


def _app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def write_debug_log(message: str):
    path = os.path.join(_app_dir(), "trend_debug_log.txt")
    try:
        with open(path, "a", encoding="utf-8") as f:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{ts}] {message}\n")
    except Exception:
        pass


class NaverOpenAPI:
    BLOG_SEARCH_URL = "https://openapi.naver.com/v1/search/blog.json"
    NEWS_SEARCH_URL = "https://openapi.naver.com/v1/search/news.json"
    DATALAB_URL = "https://openapi.naver.com/v1/datalab/search"

    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret

    def _headers(self, content_type_json=False):
        h = {
            "X-Naver-Client-Id": self.client_id,
            "X-Naver-Client-Secret": self.client_secret,
            "User-Agent": USER_AGENT,
        }
        if content_type_json:
            h["Content-Type"] = "application/json"
        return h

    def test_connection(self):
        try:
            self.get_blog_doc_count("테스트")
            return True, "네이버 검색 API 연결 성공"
        except Exception as e:
            return False, f"네이버 검색 API 연결 실패: {e}"

    def get_blog_doc_count(self, keyword: str):
        try:
            params = urllib.parse.urlencode({"query": keyword, "display": 1})
            url = f"{self.BLOG_SEARCH_URL}?{params}"
            req = urllib.request.Request(url, headers=self._headers())
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return int(data.get("total", 0))
        except Exception as e:
            write_debug_log(f"[blog_doc_count] '{keyword}' 실패: {e}")
            return None

    def search_news(self, query: str, display=20, sort="date"):
        try:
            display = min(max(display, 1), 100)
            params = urllib.parse.urlencode({"query": query, "display": display, "sort": sort})
            url = f"{self.NEWS_SEARCH_URL}?{params}"
            req = urllib.request.Request(url, headers=self._headers())
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data.get("items", [])
        except Exception as e:
            write_debug_log(f"[search_news] '{query}' 실패: {e}")
            return []

    def get_datalab_trend(self, keyword: str, recent_days=3, base_days=14):
        try:
            end = datetime.now()
            start = end - timedelta(days=recent_days + base_days)
            body = {
                "startDate": start.strftime("%Y-%m-%d"),
                "endDate": end.strftime("%Y-%m-%d"),
                "timeUnit": "date",
                "keywordGroups": [{"groupName": keyword, "keywords": [keyword]}],
            }
            data_bytes = json.dumps(body).encode("utf-8")
            req = urllib.request.Request(
                self.DATALAB_URL, data=data_bytes,
                headers=self._headers(content_type_json=True), method="POST",
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            points = result["results"][0]["data"]
            if not points:
                return {"spike_ratio": 1.0, "status": "미검증"}

            values = [p["ratio"] for p in points]
            recent_vals = values[-recent_days:]
            base_vals = values[:-recent_days] if len(values) > recent_days else values

            recent_avg = sum(recent_vals) / max(len(recent_vals), 1)
            base_avg = sum(base_vals) / max(len(base_vals), 1)

            if base_avg <= 0.01:
                spike_ratio = 3.0 if recent_avg > 0 else 1.0
            else:
                spike_ratio = recent_avg / base_avg

            if spike_ratio >= 2.0:
                status = "급증"
            elif spike_ratio >= 1.3:
                status = "상승"
            elif spike_ratio >= 0.7:
                status = "평이"
            else:
                status = "하락"

            return {"spike_ratio": round(spike_ratio, 2), "status": status}
        except Exception as e:
            write_debug_log(f"[datalab_trend] '{keyword}' 실패: {e}")
            return {"spike_ratio": 1.0, "status": "미검증"}


class NaverAdAPI:
    BASE_URL = "https://api.searchad.naver.com"

    def __init__(self, api_key: str, secret_key: str, customer_id: str):
        self.api_key = api_key
        self.secret_key = secret_key
        self.customer_id = customer_id

    def _signature(self, timestamp: str, method: str, uri: str) -> str:
        message = f"{timestamp}.{method}.{uri}"
        digest = hmac.new(bytes(self.secret_key, "utf-8"), bytes(message, "utf-8"), hashlib.sha256).digest()
        return base64.b64encode(digest).decode()

    def _headers(self, method: str, uri: str):
        timestamp = str(round(time.time() * 1000))
        signature = self._signature(timestamp, method, uri)
        return {
            "Content-Type": "application/json; charset=UTF-8",
            "X-Timestamp": timestamp,
            "X-API-KEY": self.api_key,
            "X-Customer": str(self.customer_id),
            "X-Signature": signature,
        }

    def test_connection(self):
        try:
            self.get_related_keywords("보험")
            return True, "네이버 검색광고 API 연결 성공"
        except Exception as e:
            return False, f"네이버 검색광고 API 연결 실패: {e}"

    def get_related_keywords(self, keyword: str):
        uri = "/keywordstool"
        method = "GET"
        params = {"hintKeywords": keyword, "showDetail": "1"}
        query = urllib.parse.urlencode(params)
        url = f"{self.BASE_URL}{uri}?{query}"
        try:
            req = urllib.request.Request(url, headers=self._headers(method, uri), method=method)
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data.get("keywordList", [])
        except Exception as e:
            write_debug_log(f"[related_keywords] '{keyword}' 실패: {e}")
            return []

    def get_search_volume_and_competition(self, keyword: str):
        results = self.get_related_keywords(keyword)
        if not results:
            return None, None

        target = None
        norm_kw = keyword.replace(" ", "")
        for row in results:
            if str(row.get("relKeyword", "")).replace(" ", "") == norm_kw:
                target = row
                break
        if target is None:
            target = results[0]

        def _to_int(v):
            if isinstance(v, str) and v.strip().startswith("<"):
                return 5
            try:
                return int(v)
            except Exception:
                return 0

        pc = _to_int(target.get("monthlyPcQcCnt", 0))
        mobile = _to_int(target.get("monthlyMobileQcCnt", 0))
        comp = target.get("compIdx", None)
        return pc + mobile, comp


COMP_IDX_MAP = {"낮음": 1, "중간": 2, "높음": 3}


def verify_keyword(keyword: str, open_api: "NaverOpenAPI", ad_api: "NaverAdAPI"):
    doc_count = open_api.get_blog_doc_count(keyword)
    trend = open_api.get_datalab_trend(keyword)
    search_volume, comp_label = ad_api.get_search_volume_and_competition(keyword)

    return {
        "doc_count": doc_count,
        "spike_ratio": trend.get("spike_ratio", 1.0),
        "trend_status": trend.get("status", "미검증"),
        "search_volume": search_volume,
        "comp_label": comp_label,
        "comp_idx": COMP_IDX_MAP.get(comp_label, 0),
    }
