# -*- coding: utf-8 -*-
"""
scorer.py (v19.7.1)
네이버 블로그 수익형 키워드 발굴 시스템 - 검증/확장/점수화/등급분류 통합 엔진

[파이프라인 내 위치]
  collector.collect_candidates() -> profit_filter.filter_candidates() -> scorer.score_candidates()

[v19.7.1 변경 사항 - keyword_history.json 경로 정리 (보안/경로 정리 작업, 최소 수정)]
  기존 KEYWORD_HISTORY_FILE = "keyword_history.json"은 실행 위치(현재 작업
  디렉토리, os.getcwd()) 기준 상대경로였다. 이 경우 사용자가 EXE를 바탕화면이
  아닌 다른 위치(단축아이콘의 "시작 위치" 설정, cmd에서 다른 폴더로 이동 후
  실행 등)에서 실행하면 keyword_history.json이 config.json과 다른 폴더에
  생성될 수 있었다.

  v19.7.1부터는 app.py의 BASE_DIR 계산 방식과 동일한 패턴(sys.frozen /
  sys.executable 명시적 분기)을 scorer.py 자체적으로 적용해, 항상 실행
  파일(EXE) 또는 스크립트가 위치한 폴더를 기준으로 한 절대경로를 사용한다.
  KEYWORD_HISTORY_FILE을 사용하는 두 함수(_load_keyword_history,
  _save_keyword_history)는 이미 path를 인자로 받는 구조라 내부 로직은
  전혀 수정하지 않았고, 상수 값만 상대경로에서 절대경로로 바뀌었다.

  이번 변경은 점수 계산, API 호출 순서, 429 대응 로직, 등급 분류에는
  어떠한 영향도 주지 않는다. 아직 keyword_tree.json 관련 기능(KEYWORD_TREE_FILE
  등)은 이 버전의 코드에 존재하지 않으므로 이번 변경 대상에 포함하지 않았다
  (해당 기능이 실제로 추가되는 시점에 동일한 패턴으로 함께 처리할 예정).

[이하 v19.7 변경 사항 - 2차 연관검색어 확장(Ads L2) 추가, 승인된 12개 조건만 반영]

  이번 버전은 1차 연관검색어 확장(기존 _expand_related_keywords, 변경 없음) 다음에
  "2차 연관검색어 확장" 한 단계만 추가했다. 429 대응 로직, 점수 계산, 등급 분류,
  _sanitize_keyword_for_ads(), get_search_volume(), get_related_keywords()의
  시그니처, app.py, collector.py는 전혀 변경하지 않았다.

  확정된 구현 조건은 다음과 같다.

    1) 2차 확장 API 호출(ads_api.get_related_keywords)은 최대 15회로 고정된다.
       1차 확장 결과(expanded) 중 검색량 기준 상위 15개를 대상으로 선정하며,
       혹시라도 대상 수가 15개를 넘는 경우를 대비해 RELATED_L2_MAX_CALLS로
       한 번 더 하드캡을 적용한다.
    2) 각 2차 확장 결과(연관검색어 응답 1건)에서는 total_volume 기준 상위
       5개(RELATED_L2_MAX_NEW_PER_CALL)만 신규 후보로 추가한다. 따라서
       2차 확장으로 추가되는 신규 후보는 최대 15 x 5 = 75개다.
    3) 이미 pool(1차 확장까지 끝난 결과)에 존재하는 키워드는 생성 시점에
       바로 제외한다(중복 방지, seen_keywords 집합으로 실시간 추적).
    4) 검색량이 MIN_SEARCH_VOLUME 미만인 신규 후보는 제외한다.
    5) [핵심] 2차 확장 결과에 포함된 total_volume(또는 monthlyPcQcCnt +
       monthlyMobileQcCnt) 값을 그대로 search_volume으로 사용한다.
       get_search_volume()을 이 신규 후보들에 대해 별도로 재호출하지
       않는다. 이렇게 해야 신규 후보 수(최대 75개)만큼 검색량 확인 API가
       추가로 호출되는 것을 막을 수 있고, 결과적으로 이번 기능으로 인한
       순수 추가 Ads API 호출은 2차 확장 15회로 완전히 고정된다.
    6) 문서수 확인(3단계)은 기존 DOC_COUNT_CHECK_LIMIT(150) 안에서만,
       DataLab 확인(4단계)은 기존 DATALAB_CHECK_LIMIT(25) 안에서만
       처리된다. 즉 신규 후보도 기존 후보와 동일한 기준(검색량×의도점수
       상위 정렬)으로 경쟁해 선별되며, 이 두 단계의 로직/제한 값은
       전혀 변경하지 않았다.
    7) 신규 후보에는 source=["ads_l2"], expansion_depth=2 필드가
       부여된다. 기존 후보(1차 확장 이하)에는 expansion_depth 필드가
       없으므로(암묵적으로 1차 이하로 간주), 화면/로그에서 2차 확장으로
       유입된 후보만 구분해서 볼 수 있다.
    8) 429 다발 대응: RELATED_L2_BATCH_SIZE(5) 단위로 나눠 호출하며,
       누적 실패율이 RELATED_L2_ABORT_FAIL_RATE(30%) 이상이면 남은 대상은
       호출을 시도하지 않고 조기 중단한다. max_workers는 1~2
       (RELATED_L2_MAX_WORKERS=2)로 제한하고, 호출 사이 sleep도 기존
       1차 확장과 동일하게 0.5~0.8초를 적용한다.
    9) keyword_history.json 관련 저장/로드 기능(v19.6)은 이번 버전에서도
       "저장"만 하며, 감점/보너스 반영은 여전히 적용하지 않는다(변경 없음).

  이번 버전에서 새로 추가된 함수는 _select_l2_targets(), _expand_related_l2_one(),
  _expand_related_keywords_level2() 세 개뿐이며, score_candidates() 안에서는
  1차 확장(_expand_related_keywords) 직후, pool을 만든 다음에 2차 확장 결과를
  한 번 더 병합하는 코드 한 블록만 추가했다.

[이하 v19.6 이전 버전들의 설계 배경/로직 설명은 변경 없음 - 참고용으로 유지]

  v19.6: ads API HTTP 400 원인 진단용 로그(_fetch_volume_one) 추가.
  keyword_history.json 저장/로드 기능 추가(ENABLE_KEYWORD_HISTORY 플래그로
  ON/OFF 가능, 감점/보너스는 미적용, 데이터 축적 뼈대만 마련).

  v19.5: ads API HTTP 400(Bad Request) 대응. _sanitize_keyword_for_ads()
  신설. 앞뒤 공백 제거, 연속 공백은 하나로 정리(제거하지는 않음),
  한글/영문/숫자/공백만 허용, 길이 제한(2~40자) 검사. 1단계/2단계 모두
  실제 API 호출 전에 유효/무효 키워드를 나눠 무효 키워드는 호출 자체를
  하지 않고 volume_invalid_keyword=True로 보류 처리. ApiHealthTracker에
  record_error_type()/error_summary() 추가해 400/429/기타 구분 집계.

  v19.4: 문서수를 확인하지 못한 보류 키워드의 효율(efficiency) 표시 버그
  수정. _compute_efficiency()가 doc_count=None을 0으로 치환해 나누던 것을
  0.0을 그대로 반환하도록 고치고, entry["efficiency_unknown"] 플래그를
  추가해 app.py가 화면에 "-"/"확인 불가"로 표시할 수 있게 했다.

  v19.3: 1단계(검색량)/2단계(연관검색어) ads API 429 대응. MAX_WORKERS
  4->2, sleep 증가, 1단계 대상 상위 250건 제한, 2단계 대표 후보 5->3개
  축소, 배치 조기중단 로직, ads API 실패 시 검색량 0 탈락이 아니라
  volume_api_failed=True로 보류 처리.

  v19.2: 문서수 조회(3단계)를 배치(20건) 단위로 처리하며, 누적 실패율이
  30% 이상이면 조기 중단한다. 중단된 항목은 doc_call_aborted=True.

  v19.1: DOC_COUNT_CHECK_LIMIT 50 -> 150.

  v19: EfficiencyScore 신설, 문서수 절대값 페널티 5단계 강화, 검색량 게이트
  3단, 브랜드/기관명 페널티(x0.5), FinalScore 가중치 재분배(Opportunity
  0.45/Efficiency 0.25/Category 0.15/Issue 0.10/DataLab 0.05).

[출력 계약] score_candidates()는 (results, api_health) 튜플을 반환한다. (v19.x와 동일 필드 유지)
  - 신규 추가 필드(비파괴적 추가, v19.2): doc_call_aborted (bool)
  - 신규 추가 필드(비파괴적 추가, v19.3): volume_api_failed (bool),
    volume_check_skipped (bool), volume_call_aborted (bool)
  - 신규 추가 필드(비파괴적 추가, v19.4): efficiency_unknown (bool)
  - 신규 추가 필드(비파괴적 추가, v19.5): volume_invalid_keyword (bool)
  - v19.6: 신규 필드 없음(부작용으로 keyword_history.json 갱신)
  - 신규 추가 필드(비파괴적 추가, v19.7): expansion_depth (int, 2차 확장
    유입 후보에만 존재. 그 외 후보는 이 키 자체가 없음 - .get()으로 안전하게
    조회할 것)
  - v19.7.1: 신규 필드 없음(keyword_history.json 저장 경로만 변경)

표준 라이브러리만 사용 (math, time, random, re, os, sys, json, threading,
datetime, concurrent.futures) -> PyInstaller / GitHub Actions 빌드 100%
호환. 외부 pip 패키지 없음.
"""

import re
import os
import sys
import json
import math
import time
import random
import threading
from datetime import datetime, date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed


# =========================================================================
# 0. 하이퍼파라미터
# =========================================================================
MIN_SEARCH_VOLUME = 10          # 이보다 검색량이 낮으면 1단계에서 탈락
MAX_RELATED_PER_CATEGORY = 3    # 카테고리별 연관검색어 확장 대표 후보 수 (v19.3: 5 -> 3, 429 방지)
RELATED_LIMIT = 30              # 대표 후보 1개당 받아올 연관검색어 최대 개수
DATALAB_HARD_CUT = 1.3          # 이 비율 이상이어야 "상승"으로 인정
DATALAB_NEUTRAL_RATIO = 1.0     # DataLab 실패/미조회 시 적용하는 중립값 (탈락시키지 않음)

RISK_DOC_ABS = 50000            # 이 문서수를 넘으면서 범용 앵커이면 "위험"
HOLD_DOC_ABS = 15000            # 이 문서수를 넘으면 "보류" 사유 추가(경쟁 심함, 등급 배제는 아님)
LOW_EFFICIENCY_CUT = 0.5        # search_volume/doc_count 이 값보다 낮으면 비효율 보류 사유 추가

TOP5_SIZE = 5
TOP10_SIZE = 10                 # TOP5 이후 순위 6~15
MAX_WORKERS = 2                 # 1단계(검색량)/2단계(연관검색어) 병렬 수 (v19.3: 4 -> 2, 429 심하면 1로 낮출 것)

# [v19.3] 1단계 검색량 확인 대상 제한 및 429 조기중단 파라미터
SEARCH_VOLUME_CHECK_LIMIT = 250     # mentions/intent_score/category_weight 상위 250건만 실제 ads API 호출
VOLUME_CALL_BATCH_SIZE = 20         # 이 단위로 나눠서 조회하며 배치마다 실패율을 점검한다
VOLUME_CALL_ABORT_MIN_ATTEMPTS = 10 # 최소 이만큼 시도한 뒤부터 중단 여부를 판단한다
VOLUME_CALL_ABORT_FAIL_RATE = 0.3   # 누적 실패율이 이 값 이상이면 1단계 조회를 중단한다

# [v19.3] 2단계 연관검색어 확장(1차) 429 조기중단 파라미터
RELATED_CALL_BATCH_SIZE = 10        # 대표 후보 수가 적으므로 문서수/검색량보다 작은 배치 단위 사용
RELATED_CALL_ABORT_MIN_ATTEMPTS = 5
RELATED_CALL_ABORT_FAIL_RATE = 0.3

# [v19.7 신규] 2단계 연관검색어 "2차" 확장(Ads L2) 파라미터
# 이번 단계의 핵심 제약: 순수 추가 Ads API 호출은 정확히 최대 15회로 고정한다.
RELATED_L2_TOP_N = 15               # 1차 확장 결과(expanded) 중 검색량 상위 15개를 2차 확장 대상으로 선정
RELATED_L2_MAX_CALLS = 15           # 2차 확장 API 호출 최대 15회 (대상 수와 별개로 한 번 더 거는 하드캡)
RELATED_L2_MAX_NEW_PER_CALL = 5     # 2차 확장 결과 1건당 신규 후보로 추가할 키워드는 최대 5개 (최대 15x5=75개)
RELATED_L2_BATCH_SIZE = 5           # 배치 단위 (429 조기중단 점검 주기)
RELATED_L2_ABORT_MIN_ATTEMPTS = 5
RELATED_L2_ABORT_FAIL_RATE = 0.3
RELATED_L2_MAX_WORKERS = 2          # max_workers 1~2 유지

# [v19.5 신규] ads API(hintKeywords) 400 방지를 위한 키워드 유효성 검사 파라미터
MIN_KEYWORD_LENGTH_FOR_ADS = 2      # 정리 후 길이가 이보다 짧으면 호출하지 않음(운영 중 튜닝 가능)
MAX_KEYWORD_LENGTH_FOR_ADS = 40     # 정리 후 길이가 이보다 길면 호출하지 않음(운영 중 튜닝 가능)

DOC_COUNT_CHECK_LIMIT = 150      # 문서수 조회는 검색량×의도점수 상위 150건까지만 실제 호출 (v19.1)
DATALAB_CHECK_LIMIT = 25         # DataLab 조회는 문서수 확인된 후보 중 효율 상위 25건까지만 실제 호출
DOC_MAX_WORKERS = 2              # 문서수 조회 병렬 수 (429 방지)
DATALAB_MAX_WORKERS = 1          # DataLab 조회는 순차 처리 (timeout 방지)

# [v19.2] 문서수 조회 429 조기중단 로직 파라미터
DOC_COUNT_BATCH_SIZE = 20           # 이 단위로 나눠서 조회하며 배치마다 실패율을 점검한다
DOC_COUNT_ABORT_MIN_ATTEMPTS = 10   # 최소 이만큼 시도한 뒤부터 중단 여부를 판단한다
DOC_COUNT_ABORT_FAIL_RATE = 0.3     # 누적 실패율이 이 값 이상이면 조회를 중단한다

# 범용/상시성 앵커 - "위험" 등급 및 OpportunityScore 감점에 함께 사용
GENERIC_RISK_ANCHORS = {"보험", "대출", "연금", "세금", "카드", "부동산", "청약"}

# ---- FinalScore 가중치 (Efficiency 신설로 재분배, v19와 동일) ----
WEIGHT_OPPORTUNITY = 0.45
WEIGHT_EFFICIENCY = 0.25
WEIGHT_CATEGORY = 0.15
WEIGHT_ISSUE = 0.10
WEIGHT_DATALAB = 0.05

# ---- 검색량 게이트 3단 (v19와 동일) ----
LOW_VOLUME_PENALTY_CUT = 500        # 이 미만이면 OpportunityScore 50% 추가 감점
LOW_VOLUME_PENALTY_MULTIPLIER = 0.5
TOP5_MIN_SEARCH_VOLUME = 300        # 이 미만이면 TOP5 후보에서 제외
TOP10_MIN_SEARCH_VOLUME = 100       # 이 미만이면 TOP10 후보에서도 제외 (무조건 보류)

# ---- 문서수 절대값 페널티 (v19와 동일) ----
DOC_COUNT_PENALTY_TIERS = [
    (1_000_000, 0.05),
    (500_000, 0.15),
    (300_000, 0.3),
    (100_000, 0.6),
    (50_000, 0.8),
]

# ---- 범용 상시 키워드(앵커) 감점 (v19와 동일) ----
GENERIC_ANCHOR_DOC_THRESHOLD = RISK_DOC_ABS   # 50,000
GENERIC_ANCHOR_PENALTY_MULTIPLIER = 0.15

# ---- 브랜드/기관명 페널티 (v19와 동일) ----
BRAND_INSTITUTION_KEYWORDS = {
    "공단", "심사평가원", "정부24", "네이버", "카카오", "삼성", "현대",
    "국민건강보험", "근로복지공단", "국세청", "국민연금공단",
}
BRAND_INSTITUTION_PENALTY_MULTIPLIER = 0.5

# ---- doc_count 미확인 시 FinalScore 계산용 중립값 (v19와 동일) ----
OPPORTUNITY_NEUTRAL_FOR_UNKNOWN_DOC = 3.0
EFFICIENCY_NEUTRAL_FOR_UNKNOWN_DOC = 3.0

# ---- 브랜드/롱테일 가산 (v19와 동일) ----
LONGTAIL_MIN_LENGTH = 6
BRAND_LONGTAIL_MAX_BONUS = 1.5

# ---- EfficiencyScore 스케일 계수 (v19와 동일) ----
EFFICIENCY_LOG_SCALE = 5.0

# [v19.7.1 신규] PyInstaller 빌드 환경에서 실행 파일 위치를 견고하게 판별하기
# 위해 sys.frozen 여부를 명시적으로 분기한다. app.py의 BASE_DIR 계산 방식과
# 동일한 패턴이다. scorer.py는 app.py 없이 단독 실행(__main__)도 가능한
# 모듈이므로, app.py의 BASE_DIR 값을 가져다 쓰지 않고 이 파일 자체적으로
# 계산해 모듈 간 결합도를 낮게 유지한다.
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---- [v19.6 신규, v19.7.1에서 경로만 변경] keyword_history.json 저장/로드 기능 ----
# 이번 단계에서는 이 히스토리를 점수 계산이나 등급 분류에 전혀 사용하지 않는다.
# 순수하게 "다음 실행 때 참고할 데이터를 쌓아두는" 뼈대만 추가한 것이다.
ENABLE_KEYWORD_HISTORY = True        # False로 바꾸면 히스토리 기능 전체를 끌 수 있음 (v19.5와 100% 동일 동작)
# [v19.7.1 변경] 상대경로("keyword_history.json") -> BASE_DIR 기준 절대경로.
# 실행 위치(현재 작업 디렉토리)에 따라 config.json과 다른 폴더에 생성되던
# 문제를 방지한다. _load_keyword_history()/_save_keyword_history()는 이미
# path를 인자로 받으므로 내부 로직은 변경하지 않았다.
KEYWORD_HISTORY_FILE = os.path.join(BASE_DIR, "keyword_history.json")
KEYWORD_HISTORY_RETENTION_DAYS = 30  # 이 기간을 넘는 기록은 자동 정리(prune)
KEYWORD_HISTORY_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


# =========================================================================
# 1. API 안전 호출 래퍼 + api_health 집계
# =========================================================================
class ApiHealthTracker:
    """스레드에서 동시에 호출되므로 락으로 카운터를 보호한다."""

    def __init__(self):
        self._lock = threading.Lock()
        self._counts = {
            "search": {"ok": 0, "fail": 0},
            "ads": {"ok": 0, "fail": 0},
            "datalab": {"ok": 0, "fail": 0},
        }
        # [v19.5 신규] ads API 실패를 400(파라미터 오류)/429(과다호출)/기타로 구분해 집계
        self._error_counts = {"http_400": 0, "http_429": 0, "other": 0}

    def record(self, api_name, success):
        with self._lock:
            key = "ok" if success else "fail"
            self._counts[api_name][key] += 1

    def record_error_type(self, error_message):
        """
        [v19.5 신규] API 호출 실패 시 예외 메시지 문자열에서 HTTP 상태코드를
        추정해 집계한다. naver_search_api.py의 예외 메시지가 "HTTP 400 - ...",
        "HTTP 429 - ..." 형태라는 것을 로그에서 확인했으므로, 메시지 안에
        "400"/"429" 문자열이 포함되어 있는지로 단순 분류한다.
        """
        with self._lock:
            msg = error_message or ""
            if "400" in msg:
                self._error_counts["http_400"] += 1
            elif "429" in msg:
                self._error_counts["http_429"] += 1
            else:
                self._error_counts["other"] += 1

    def summarize(self):
        summary = {}
        for api_name, counts in self._counts.items():
            total = counts["ok"] + counts["fail"]
            if total == 0:
                summary[api_name] = "ok"
            elif counts["fail"] == 0:
                summary[api_name] = "ok"
            elif counts["ok"] == 0:
                summary[api_name] = "fail"
            else:
                summary[api_name] = "partial"
        return summary

    def error_summary(self):
        """[v19.5 신규] 실패 유형별 집계(400/429/기타)를 반환한다. 로그 출력용."""
        with self._lock:
            return dict(self._error_counts)


def _safe_call(fn, tracker, api_name, log=None, context=""):
    """API 호출을 감싸서 예외를 흡수하고 성공/실패를 tracker에 기록. 실패 시 None 반환."""
    try:
        result = fn()
        tracker.record(api_name, True)
        return result
    except Exception as e:
        tracker.record(api_name, False)
        tracker.record_error_type(str(e))  # [v19.5 신규] 400/429/기타 구분 집계
        if log:
            log(f"[scorer] API 호출 실패 ({api_name}, {context}): {e}")
        return None


class _KeywordCache:
    """
    실행 중(score_candidates 1회 호출 동안) 동일 키워드에 대한
    중복 API 호출을 막기 위한 캐시. 스레드 안전.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.search_volume = {}
        self.doc_count = {}
        self.trend_ratio = {}

    def get_search_volume(self, keyword):
        with self._lock:
            return self.search_volume.get(keyword, "MISS")

    def set_search_volume(self, keyword, value):
        with self._lock:
            self.search_volume[keyword] = value

    def get_doc_count(self, keyword):
        with self._lock:
            return self.doc_count.get(keyword, "MISS")

    def set_doc_count(self, keyword, value):
        with self._lock:
            self.doc_count[keyword] = value

    def get_trend_ratio(self, keyword):
        with self._lock:
            return self.trend_ratio.get(keyword, "MISS")

    def set_trend_ratio(self, keyword, value):
        with self._lock:
            self.trend_ratio[keyword] = value


# =========================================================================
# 1B. [v19.5] ads API(hintKeywords) 400 방지를 위한 키워드 유효성 검사
# =========================================================================
_KEYWORD_ALLOWED_PATTERN = re.compile(r"^[가-힣a-zA-Z0-9 ]+$")


def _sanitize_keyword_for_ads(raw_keyword):
    """
    ads API(hintKeywords)에 전달하기 전 키워드를 정리하고 유효성을 검사한다.

    - 앞뒤 공백 제거, 연속 공백은 하나로 정리
    - 한글/영문/숫자/공백만 허용(그 외 특수문자가 하나라도 포함되면 무효)
    - 정리 후 길이가 MIN_KEYWORD_LENGTH_FOR_ADS ~ MAX_KEYWORD_LENGTH_FOR_ADS
      범위를 벗어나면 무효
    - 빈 문자열은 당연히 무효

    이 함수를 통과하지 못한 키워드는 ads API(get_search_volume,
    get_related_keywords) 호출 자체를 시도하지 않는다. 호출 실패(429 등)와는
    원인이 다르므로 volume_invalid_keyword로 별도 표시한다.

    [v19.6, v19.7, v19.7.1] 이번 버전에서도 이 함수의 동작은 변경하지
    않았다(승인된 범위 밖).

    Returns
    -------
    (cleaned_keyword, is_valid) : tuple[str, bool]
    """
    if not raw_keyword:
        return "", False
    cleaned = re.sub(r"\s+", " ", raw_keyword.strip())
    if not cleaned:
        return cleaned, False
    if not _KEYWORD_ALLOWED_PATTERN.match(cleaned):
        return cleaned, False
    if not (MIN_KEYWORD_LENGTH_FOR_ADS <= len(cleaned) <= MAX_KEYWORD_LENGTH_FOR_ADS):
        return cleaned, False
    return cleaned, True


# =========================================================================
# 2. 1단계: 검색량 확인
# =========================================================================
def _fetch_volume_one(ads_api, keyword, tracker, log, cache):
    """
    이 함수는 이미 _sanitize_keyword_for_ads()를 통과한 keyword에 대해서만
    호출된다(호출부인 _check_search_volume에서 사전 필터링). 실제 API에는
    정리된(cleaned) 키워드를 전달한다.

    반환값의 두 번째 요소는 int(검색량) 또는 None(API 호출 실패)이다.
    """
    cached = cache.get_search_volume(keyword)
    if cached != "MISS":
        return keyword, cached
    cleaned, _ = _sanitize_keyword_for_ads(keyword)  # 이미 유효성 검사를 통과했으므로 cleaned만 사용
    if log:
        # [v19.6 진단용] HTTP 400 원인 확정을 위해 ads API에 실제로 전달되는
        # 문자열을 원본과 함께 남긴다. 확인 목적의 임시 로그이며
        # _sanitize_keyword_for_ads(), 429 대응 로직, 점수 계산, 등급
        # 분류에는 아무런 영향을 주지 않는다.
        log(f"[scorer] ADS 요청(volume): original='{keyword}' -> 전달값='{cleaned}'")
    vol = _safe_call(lambda: ads_api.get_search_volume(cleaned), tracker, "ads", log, f"volume:{keyword}")
    time.sleep(0.3 + random.random() * 0.2)  # 0.08~0.16초 -> 0.3~0.5초
    result = vol if isinstance(vol, int) else None  # None = API 호출 실패(429 등), 검색량 0과 구분
    cache.set_search_volume(keyword, result)
    return keyword, result


def _select_volume_check_targets(candidates, limit=SEARCH_VOLUME_CHECK_LIMIT):
    """
    1단계 검색량 확인 대상을 전체가 아니라 mentions -> intent_score
    -> category_weight 순으로 우선순위를 매겨 상위 limit개 키워드만 선정한다.
    동일 키워드가 여러 카테고리에 걸쳐 있을 수 있으므로 키워드 단위로 유일화한 뒤
    가장 높은 우선순위 값을 대표값으로 사용해 정렬한다.
    """
    best_by_keyword = {}
    for c in candidates:
        kw = c["keyword"]
        rank_key = (
            c.get("mentions", 0),
            c.get("intent_score", 0.3),
            c.get("category_weight", 1.0),
        )
        if kw not in best_by_keyword or rank_key > best_by_keyword[kw]:
            best_by_keyword[kw] = rank_key

    ranked_keywords = sorted(best_by_keyword.items(), key=lambda kv: kv[1], reverse=True)
    selected_keywords = {kw for kw, _ in ranked_keywords[:limit]}
    skipped_keywords = {kw for kw, _ in ranked_keywords[limit:]}
    return selected_keywords, skipped_keywords


def _check_search_volume(candidates, ads_api, tracker, cache, log=None, max_workers=MAX_WORKERS):
    """
    1단계 검색량 확인.

    - 전체 후보가 아니라 mentions/intent_score/category_weight 기준 상위
      SEARCH_VOLUME_CHECK_LIMIT(250)건만 실제 ads API를 호출한다. 250건 밖
      후보는 volume_check_skipped=True로 표시해 "보류"로 넘긴다.
    - [v19.5] 선정된 250건 중에서도 _sanitize_keyword_for_ads() 검사를
      통과하지 못한 키워드(특수문자/길이 등 hintKeywords 형식 오류 가능성이
      높은 키워드)는 애초에 API 호출 배치에 포함시키지 않고
      volume_invalid_keyword=True로 표시해 "보류"로 넘긴다. 이 키워드들은
      429 조기중단 실패율 계산에도 포함되지 않는다(호출 속도 문제와
      파라미터 형식 문제를 서로 오염시키지 않기 위함).
    - 나머지 유효 키워드는 VOLUME_CALL_BATCH_SIZE 단위로 나눠서 순차 호출하며,
      누적 실패율이 VOLUME_CALL_ABORT_FAIL_RATE 이상이면(429 다발로 추정)
      남은 배치는 호출을 시도하지 않고 조기 중단한다. 중단된 키워드는
      volume_call_aborted=True로 표시한다.
    - ads API 호출이 실패(429 등)한 키워드는 검색량 0으로 간주해 탈락시키지
      않고 volume_api_failed=True로 표시해 "보류"로 넘긴다.
    - 실제로 호출에 성공했는데 진짜 검색량이 MIN_SEARCH_VOLUME 미만인
      경우만 기존처럼 탈락(dropped) 처리한다.

    Returns
    -------
    (survived, dropped, held) : tuple[list[dict], list[dict], list[dict]]
      - survived: 검색량 확인 성공 + MIN_SEARCH_VOLUME 이상
      - dropped : 검색량 확인 성공 + MIN_SEARCH_VOLUME 미만(진짜 저검색량, 탈락)
      - held    : 호출 제한/무효 키워드/API 실패/조기중단으로 검색량을 확인하지 못함(보류)
    """
    selected_keywords, skipped_keywords = _select_volume_check_targets(candidates)

    # [v19.5] 실제 호출 전에 유효/무효 키워드를 먼저 나눈다.
    valid_keywords = []
    invalid_keywords = set()
    for kw in selected_keywords:
        _, is_valid = _sanitize_keyword_for_ads(kw)
        if is_valid:
            valid_keywords.append(kw)
        else:
            invalid_keywords.add(kw)

    volume_map = {}
    attempted = 0
    fail_total = 0
    aborted = False
    processed_keywords = set()

    for batch_start in range(0, len(valid_keywords), VOLUME_CALL_BATCH_SIZE):
        if aborted:
            break
        batch = valid_keywords[batch_start:batch_start + VOLUME_CALL_BATCH_SIZE]
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_fetch_volume_one, ads_api, kw, tracker, log, cache): kw
                for kw in batch
            }
            for future in as_completed(futures):
                kw, vol = future.result()
                volume_map[kw] = vol
                processed_keywords.add(kw)
                attempted += 1
                if vol is None:
                    fail_total += 1

        if attempted >= VOLUME_CALL_ABORT_MIN_ATTEMPTS and (fail_total / attempted) >= VOLUME_CALL_ABORT_FAIL_RATE:
            aborted = True

    aborted_keywords = set(valid_keywords) - processed_keywords

    survived, dropped, held = [], [], []
    for c in candidates:
        kw = c["keyword"]
        entry = dict(c)
        entry["volume_check_skipped"] = kw in skipped_keywords
        entry["volume_call_aborted"] = kw in aborted_keywords
        entry["volume_invalid_keyword"] = kw in invalid_keywords
        entry["volume_api_failed"] = False

        if kw in invalid_keywords:
            entry["search_volume"] = 0
            entry["verify_volume"] = False
            held.append(entry)
            continue

        if kw in skipped_keywords or kw in aborted_keywords:
            entry["search_volume"] = 0
            entry["verify_volume"] = False
            held.append(entry)
            continue

        vol = volume_map.get(kw)
        if vol is None:
            entry["search_volume"] = 0
            entry["verify_volume"] = False
            entry["volume_api_failed"] = True
            held.append(entry)
            continue

        entry["search_volume"] = vol
        entry["verify_volume"] = vol >= MIN_SEARCH_VOLUME
        if entry["verify_volume"]:
            survived.append(entry)
        else:
            dropped.append(entry)

    if log:
        log(f"[scorer] 1단계 검색량 확인 대상 {len(selected_keywords)}건 "
            f"(mentions/intent_score/category_weight 상위 {SEARCH_VOLUME_CHECK_LIMIT}건, "
            f"전체 {len(candidates)}건 중 {len(skipped_keywords)}건은 호출 제한으로 미확인)")
        if invalid_keywords:
            log(f"[scorer] 1단계 ads API 호출 제외(무효 키워드) {len(invalid_keywords)}건 "
                f"- 특수문자/길이 제한 등으로 hintKeywords 형식에 부적합해 호출 자체를 시도하지 않음 "
                f"(HTTP 400 방지)")
        log(f"[scorer] 1단계 검색량 확인 결과 - 통과 {len(survived)}건 / "
            f"저검색량 탈락 {len(dropped)}건 / "
            f"API실패·호출제한·조기중단·무효키워드로 보류 {len(held)}건")
        if aborted:
            log(f"[scorer] 1단계 검색량 API 429 다발로 조기중단 - 실패율 {fail_total}/{attempted} "
                f"({(fail_total / attempted * 100):.0f}%)로 임계값({VOLUME_CALL_ABORT_FAIL_RATE * 100:.0f}%) 초과, "
                f"남은 {len(aborted_keywords)}건은 호출을 중단하고 보류 처리합니다.")

    return survived, dropped, held


# =========================================================================
# 3. 2단계: 연관검색어 확장 (1차, 검색량 확인된 후보 중 대표만)
# =========================================================================
def _select_representatives(survived, per_category=MAX_RELATED_PER_CATEGORY):
    by_category = {}
    for c in survived:
        by_category.setdefault(c["category"], []).append(c)

    reps = []
    for cat, items in by_category.items():
        items_sorted = sorted(
            items, key=lambda x: x["search_volume"] * x.get("intent_score", 0.3), reverse=True
        )
        reps.extend(items_sorted[:per_category])
    return reps


def _expand_related_one(ads_api, rep, tracker, log):
    """
    이 함수는 이미 _sanitize_keyword_for_ads()를 통과한 rep에 대해서만
    호출된다(호출부인 _expand_related_keywords에서 사전 필터링). 실제 API에는
    정리된(cleaned) 키워드를 전달한다.
    """
    kw = rep["keyword"]
    cleaned, _ = _sanitize_keyword_for_ads(kw)  # 이미 유효성 검사를 통과했으므로 cleaned만 사용
    related = _safe_call(
        lambda: ads_api.get_related_keywords(cleaned, limit=RELATED_LIMIT),
        tracker, "ads", log, f"related:{kw}"
    )
    time.sleep(0.5 + random.random() * 0.3)  # 0.1~0.2초 -> 0.5~0.8초
    failed = related is None  # 호출 자체가 실패한 경우(429 등)와 "연관검색어 없음"(빈 리스트)을 구분
    return rep, (related or []), failed


def _expand_related_keywords(survived, ads_api, tracker, log=None, max_workers=MAX_WORKERS):
    """
    대표 후보를 RELATED_CALL_BATCH_SIZE 단위로 나눠서 순차 확장하며,
    누적 실패율이 RELATED_CALL_ABORT_FAIL_RATE 이상이면(429 다발로 추정)
    남은 배치는 호출을 시도하지 않고 조기 중단한다.

    [v19.5] 대표 후보 중 _sanitize_keyword_for_ads() 검사를 통과하지 못하는
    키워드는 애초에 API 호출을 시도하지 않고 건너뛴다(HTTP 400 방지). 이
    후보들은 429 조기중단 실패율 계산에도 포함되지 않는다.
    """
    if not hasattr(ads_api, "get_related_keywords"):
        if log:
            log("[scorer] ads_api.get_related_keywords 미제공 - 2단계 확장을 건너뜁니다.")
        return []

    reps = _select_representatives(survived)

    # [v19.5] 실제 호출 전에 유효/무효 대표 후보를 먼저 나눈다.
    valid_reps = []
    invalid_rep_keywords = []
    for rep in reps:
        _, is_valid = _sanitize_keyword_for_ads(rep["keyword"])
        if is_valid:
            valid_reps.append(rep)
        else:
            invalid_rep_keywords.append(rep["keyword"])

    if log:
        log(f"[scorer] 2단계 연관검색어 확장(1차) 대상: {len(reps)}건 (카테고리별 상위 {MAX_RELATED_PER_CATEGORY}개)")
        if invalid_rep_keywords:
            log(f"[scorer] 2단계(1차) ads API 호출 제외(무효 키워드) {len(invalid_rep_keywords)}건 "
                f"- 특수문자/길이 제한 등으로 hintKeywords 형식에 부적합해 호출 자체를 시도하지 않음 "
                f"(HTTP 400 방지)")

    expanded = []
    attempted = 0
    fail_total = 0
    aborted = False
    processed_reps = 0

    for batch_start in range(0, len(valid_reps), RELATED_CALL_BATCH_SIZE):
        if aborted:
            break
        batch = valid_reps[batch_start:batch_start + RELATED_CALL_BATCH_SIZE]
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_expand_related_one, ads_api, rep, tracker, log): rep for rep in batch}
            for future in as_completed(futures):
                rep, related_items, failed = future.result()
                processed_reps += 1
                attempted += 1
                if failed:
                    fail_total += 1
                    continue

                for item in related_items:
                    rel_kw = (item.get("keyword") or item.get("relKeyword") or "").strip()
                    rel_vol = item.get("total_volume")
                    if rel_vol is None:
                        pc = item.get("monthlyPcQcCnt", 0) or 0
                        mo = item.get("monthlyMobileQcCnt", 0) or 0
                        rel_vol = pc + mo
                    if not rel_kw or rel_vol < MIN_SEARCH_VOLUME:
                        continue
                    if rep["anchor"] not in rel_kw and rep["keyword"] not in rel_kw:
                        continue

                    expanded.append({
                        "keyword": rel_kw,
                        "category": rep["category"],
                        "anchor": rep["anchor"],
                        "intent_word": rep.get("intent_word"),
                        "mentions": 0,
                        "sample_titles": [],
                        "seed_query": rep["keyword"],
                        "first_pub_date": "",
                        "latest_pub_date": "",
                        "intent_score": rep.get("intent_score", 0.3),
                        "category_weight": rep.get("category_weight", 1.0),
                        "category_meta": rep.get("category_meta", {}),
                        "source": ["ads"],
                        "search_volume": int(rel_vol),
                        "verify_volume": True,
                    })

        if attempted >= RELATED_CALL_ABORT_MIN_ATTEMPTS and (fail_total / attempted) >= RELATED_CALL_ABORT_FAIL_RATE:
            aborted = True

    dedup = {}
    for e in expanded:
        key = (e["category"], e["keyword"])
        if key not in dedup:
            dedup[key] = e
    result = list(dedup.values())

    if log:
        log(f"[scorer] 2단계 연관검색어 확장(1차) 결과: 시도 {attempted}건 / 실패 {fail_total}건 / "
            f"신규 후보 {len(result)}건")
        if aborted:
            skipped_reps = len(valid_reps) - processed_reps
            log(f"[scorer] 2단계(1차) 연관검색어 확장 API 429 다발로 조기중단 - 실패율 {fail_total}/{attempted} "
                f"({(fail_total / attempted * 100):.0f}%)로 임계값({RELATED_CALL_ABORT_FAIL_RATE * 100:.0f}%) 초과, "
                f"남은 {skipped_reps}건은 확장을 건너뜁니다.")

    return result


def _merge_pools(survived, expanded):
    merged = {}
    for c in survived:
        key = (c["category"], c["keyword"])
        entry = dict(c)
        entry.setdefault("source", ["news"])
        merged[key] = entry
    for c in expanded:
        key = (c["category"], c["keyword"])
        if key in merged:
            existing_sources = set(merged[key].get("source", []))
            existing_sources.update(c.get("source", ["ads"]))
            merged[key]["source"] = sorted(existing_sources)
        else:
            merged[key] = c
    return list(merged.values())


# =========================================================================
# 3B. [v19.7 신규] 2단계 연관검색어 "2차" 확장 (Ads L2)
# =========================================================================
def _select_l2_targets(expanded, limit=RELATED_L2_TOP_N):
    """
    1차 확장 결과(expanded) 중 검색량(search_volume) 기준 상위 limit개를
    2차 확장 대상으로 선정한다. 이 선정 하나만으로 2차 확장 API 호출 수가
    limit(기본 15)를 넘지 않도록 통제된다.
    """
    ranked = sorted(expanded, key=lambda c: -(c.get("search_volume", 0)))
    return ranked[:limit]


def _expand_related_l2_one(ads_api, rep, tracker, log):
    """
    2차 확장 대상 1건에 대해 ads_api.get_related_keywords()를 1회 호출한다.
    이 함수는 이미 _sanitize_keyword_for_ads()를 통과한 rep에 대해서만
    호출된다(호출부인 _expand_related_keywords_level2에서 사전 필터링).
    """
    kw = rep["keyword"]
    cleaned, _ = _sanitize_keyword_for_ads(kw)  # 이미 유효성 검사를 통과했으므로 cleaned만 사용
    related = _safe_call(
        lambda: ads_api.get_related_keywords(cleaned, limit=RELATED_LIMIT),
        tracker, "ads", log, f"related_l2:{kw}"
    )
    time.sleep(0.5 + random.random() * 0.3)  # 1차 확장과 동일한 sleep 정책
    failed = related is None
    return rep, (related or []), failed


def _expand_related_keywords_level2(expanded, ads_api, tracker, existing_keywords, log=None,
                                     max_workers=RELATED_L2_MAX_WORKERS):
    """
    1차 확장 결과(expanded) 중 검색량 상위 RELATED_L2_TOP_N(15)개를 대상으로
    ads_api.get_related_keywords()를 추가로 호출하는 "2차 확장" 함수.

    [핵심 제약 - 이번 승인 범위]
      - 2차 확장 API 호출은 최대 RELATED_L2_MAX_CALLS(15)회로 고정된다.
        대상 선정 자체가 15개이므로 자연히 호출도 15회를 넘지 않지만,
        만약을 대비해 valid_targets를 한 번 더 슬라이싱해 하드캡을 건다.
      - 각 2차 확장 결과에서는 total_volume 기준 상위
        RELATED_L2_MAX_NEW_PER_CALL(5)개만 신규 후보로 추가한다.
        (최대 15 x 5 = 75개)
      - 이미 pool에 존재하는 키워드(existing_keywords)는 제외하며, 이번
        함수 실행 중 새로 추가되는 키워드도 즉시 seen_keywords에 반영해
        같은 실행 내에서 중복이 생기지 않도록 한다.
      - 검색량이 MIN_SEARCH_VOLUME 미만인 신규 후보는 제외한다.
      - [중요] 2차 확장 결과에 포함된 total_volume(또는 monthlyPcQcCnt +
        monthlyMobileQcCnt) 값을 그대로 search_volume으로 사용하며,
        get_search_volume()을 이 신규 후보들에 대해 별도로 재호출하지
        않는다. 이 원칙 덕분에 이번 기능으로 인한 순수 추가 Ads API
        호출은 2차 확장 15회로 완전히 고정된다.
      - 신규 후보에는 source=["ads_l2"], expansion_depth=2를 부여한다.
      - 429 다발 대응: RELATED_L2_BATCH_SIZE(5) 단위로 나눠 호출하며,
        누적 실패율이 RELATED_L2_ABORT_FAIL_RATE(30%) 이상이면 남은 대상은
        호출을 시도하지 않고 조기 중단한다.
    """
    if not hasattr(ads_api, "get_related_keywords"):
        if log:
            log("[scorer] ads_api.get_related_keywords 미제공 - 2차 확장을 건너뜁니다.")
        return []

    targets_all = _select_l2_targets(expanded, RELATED_L2_TOP_N)

    # 실제 호출 전에 유효/무효 대상을 먼저 나눈다 (HTTP 400 방지).
    valid_targets = []
    invalid_target_keywords = []
    for rep in targets_all:
        _, is_valid = _sanitize_keyword_for_ads(rep["keyword"])
        if is_valid:
            valid_targets.append(rep)
        else:
            invalid_target_keywords.append(rep["keyword"])

    # 안전장치: 어떤 경우에도 2차 확장 호출 대상은 RELATED_L2_MAX_CALLS를 넘지 않는다.
    valid_targets = valid_targets[:RELATED_L2_MAX_CALLS]

    if log:
        log(f"[scorer] 2단계 연관검색어 확장(2차) 대상: {len(valid_targets)}건 "
            f"(1차 확장 결과 중 검색량 상위 {RELATED_L2_TOP_N}건 기준, 최대 {RELATED_L2_MAX_CALLS}회 호출로 고정)")
        if invalid_target_keywords:
            log(f"[scorer] 2단계(2차) 확장 호출 제외(무효 키워드) {len(invalid_target_keywords)}건 "
                f"- hintKeywords 형식에 부적합해 호출 자체를 시도하지 않음(HTTP 400 방지)")

    new_candidates = []
    attempted = 0
    fail_total = 0
    aborted = False
    processed_reps = 0
    seen_keywords = set(existing_keywords)  # 이미 pool에 있는 키워드는 제외

    for batch_start in range(0, len(valid_targets), RELATED_L2_BATCH_SIZE):
        if aborted:
            break
        batch = valid_targets[batch_start:batch_start + RELATED_L2_BATCH_SIZE]
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_expand_related_l2_one, ads_api, rep, tracker, log): rep
                for rep in batch
            }
            for future in as_completed(futures):
                rep, related_items, failed = future.result()
                processed_reps += 1
                attempted += 1
                if failed:
                    fail_total += 1
                    continue

                # total_volume 기준 상위 RELATED_L2_MAX_NEW_PER_CALL(5)개만 추린다.
                ranked_items = []
                for item in related_items:
                    rel_kw = (item.get("keyword") or item.get("relKeyword") or "").strip()
                    rel_vol = item.get("total_volume")
                    if rel_vol is None:
                        pc = item.get("monthlyPcQcCnt", 0) or 0
                        mo = item.get("monthlyMobileQcCnt", 0) or 0
                        rel_vol = pc + mo
                    if not rel_kw:
                        continue
                    try:
                        rel_vol = int(rel_vol)
                    except (TypeError, ValueError):
                        continue
                    ranked_items.append((rel_kw, rel_vol))
                ranked_items.sort(key=lambda x: -x[1])

                added_for_this_rep = 0
                for rel_kw, rel_vol in ranked_items:
                    if added_for_this_rep >= RELATED_L2_MAX_NEW_PER_CALL:
                        break
                    if rel_kw in seen_keywords:
                        continue
                    if rel_vol < MIN_SEARCH_VOLUME:
                        continue

                    new_candidates.append({
                        "keyword": rel_kw,
                        "category": rep["category"],
                        "anchor": rep["anchor"],
                        "intent_word": rep.get("intent_word"),
                        "mentions": 0,
                        "sample_titles": [],
                        "seed_query": rep["keyword"],
                        "first_pub_date": "",
                        "latest_pub_date": "",
                        "intent_score": rep.get("intent_score", 0.3),
                        "category_weight": rep.get("category_weight", 1.0),
                        "category_meta": rep.get("category_meta", {}),
                        "source": ["ads_l2"],
                        # [핵심] 재조회 없이 2차 확장 응답의 total_volume을 그대로 사용
                        "search_volume": rel_vol,
                        "verify_volume": True,
                        "expansion_depth": 2,
                    })
                    seen_keywords.add(rel_kw)
                    added_for_this_rep += 1

        if attempted >= RELATED_L2_ABORT_MIN_ATTEMPTS and (fail_total / attempted) >= RELATED_L2_ABORT_FAIL_RATE:
            aborted = True

    if log:
        log(f"[scorer] 2단계 연관검색어 확장(2차) 결과: 시도 {attempted}건 / 실패 {fail_total}건 / "
            f"신규 후보 {len(new_candidates)}건 (검색량 재조회 없이 total_volume 값 그대로 사용, "
            f"추가 Ads API 호출 없음)")
        if aborted:
            skipped_reps = len(valid_targets) - processed_reps
            log(f"[scorer] 2단계(2차) 연관검색어 확장 API 429 다발로 조기중단 - 실패율 {fail_total}/{attempted} "
                f"({(fail_total / attempted * 100):.0f}%)로 임계값({RELATED_L2_ABORT_FAIL_RATE * 100:.0f}%) 초과, "
                f"남은 {skipped_reps}건은 확장을 건너뜁니다.")

    return new_candidates


# =========================================================================
# 4. 3단계: 문서수 확인 (상위 150건 제한 / 배치 처리 + 429 조기중단)
# =========================================================================
def _select_doc_check_targets(pool, limit=DOC_COUNT_CHECK_LIMIT):
    """검색량×의도점수 기준 상위 limit건만 실제 문서수 조회 대상으로 선정."""
    ranked = sorted(
        pool, key=lambda c: -(c.get("search_volume", 0) * c.get("intent_score", 0.3))
    )
    return ranked[:limit], ranked[limit:]


def _fetch_doc_count_one(search_api, keyword, tracker, log, cache):
    cached = cache.get_doc_count(keyword)
    if cached != "MISS":
        return keyword, cached
    count = _safe_call(
        lambda: search_api.get_blog_doc_count(keyword), tracker, "search", log, f"doc:{keyword}"
    )
    time.sleep(0.15 + random.random() * 0.15)
    cache.set_doc_count(keyword, count)
    return keyword, count  # None이면 API 실패, 0이면 정상 응답(문서 없음)


def _check_doc_counts(targets, search_api, tracker, cache, log=None, max_workers=DOC_MAX_WORKERS):
    """
    대상은 이미 상위 N건으로 제한된 상태로 들어온다.
    문서수 API 실패는 candidates에서 제거(drop)하지 않고, doc_count=None,
    doc_api_failed=True로 표시만 한다. (등급 판정 단계에서 "보류"(검증보류)로 처리)

    DOC_COUNT_BATCH_SIZE 단위로 나눠서 순차 처리하며, 배치가 끝날 때마다
    누적 실패율을 점검한다. 시도 건수가 DOC_COUNT_ABORT_MIN_ATTEMPTS 이상이면서
    누적 실패율이 DOC_COUNT_ABORT_FAIL_RATE 이상이면(429 다발 상황으로 추정),
    남은 대상은 호출을 시도하지 않고 doc_call_aborted=True로 표시한 뒤
    doc_count=None("-" 표시)으로 남긴다.
    """
    unique_keywords = list({c["keyword"] for c in targets})

    doc_map = {}
    attempted = 0
    fail_total = 0
    aborted = False
    processed_keywords = set()

    for batch_start in range(0, len(unique_keywords), DOC_COUNT_BATCH_SIZE):
        if aborted:
            break
        batch = unique_keywords[batch_start:batch_start + DOC_COUNT_BATCH_SIZE]
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_fetch_doc_count_one, search_api, kw, tracker, log, cache): kw
                for kw in batch
            }
            for future in as_completed(futures):
                kw, count = future.result()
                doc_map[kw] = count
                processed_keywords.add(kw)
                attempted += 1
                if count is None:
                    fail_total += 1

        if attempted >= DOC_COUNT_ABORT_MIN_ATTEMPTS and (fail_total / attempted) >= DOC_COUNT_ABORT_FAIL_RATE:
            aborted = True

    ok_count = 0
    fail_count = 0
    aborted_count = 0
    for c in targets:
        kw = c["keyword"]
        if kw in processed_keywords:
            count = doc_map.get(kw)
            if count is None:
                c["doc_count"] = None
                c["verify_docs"] = False
                c["doc_api_failed"] = True
                c["doc_check_skipped"] = False
                c["doc_call_aborted"] = False
                fail_count += 1
            else:
                c["doc_count"] = count
                c["verify_docs"] = True
                c["doc_api_failed"] = False
                c["doc_check_skipped"] = False
                c["doc_call_aborted"] = False
                ok_count += 1
        else:
            # 429 다발로 조기 중단되어 호출을 시도조차 하지 못한 경우
            c["doc_count"] = None
            c["verify_docs"] = False
            c["doc_api_failed"] = False
            c["doc_check_skipped"] = False
            c["doc_call_aborted"] = True
            aborted_count += 1

    if log:
        summary = (f"[scorer] 3단계 문서수 확인 대상 {len(targets)}건 - 통과 {ok_count}건 / "
                   f"API 실패(검증보류) {fail_count}건")
        if aborted_count:
            summary += f" / 조회 중단(429 다발) {aborted_count}건"
        log(summary)
        if aborted:
            log(f"[scorer] 문서수 조회 제한으로 일부 미확인 - 실패율 {fail_total}/{attempted} "
                f"({(fail_total / attempted * 100):.0f}%)로 임계값({DOC_COUNT_ABORT_FAIL_RATE * 100:.0f}%) 초과, "
                f"남은 {aborted_count}건은 조회를 중단하고 '-'로 표시합니다.")

    return targets


# =========================================================================
# 5. 4단계: DataLab 확인 (문서수 확인된 후보 중 상위 25건 제한)
# =========================================================================
def _select_datalab_targets(doc_known_candidates, limit=DATALAB_CHECK_LIMIT):
    """검색량/문서수 효율 기준 상위 limit건만 실제 DataLab 조회 대상으로 선정."""
    ranked = sorted(
        doc_known_candidates,
        key=lambda c: -(c.get("search_volume", 0) / max((c.get("doc_count") or 0) + 1, 1))
    )
    return ranked[:limit], ranked[limit:]


def _fetch_trend_one(datalab_api, keyword, tracker, log, cache):
    cached = cache.get_trend_ratio(keyword)
    if cached != "MISS":
        return keyword, cached
    ratio = _safe_call(
        lambda: datalab_api.get_trend_ratio(keyword), tracker, "datalab", log, f"trend:{keyword}"
    )
    time.sleep(0.15 + random.random() * 0.15)
    cache.set_trend_ratio(keyword, ratio)
    return keyword, ratio  # None이면 실패


def _check_datalab(targets, datalab_api, tracker, cache, log=None, max_workers=DATALAB_MAX_WORKERS):
    """
    실패/timeout은 탈락이 아니라 DATALAB_NEUTRAL_RATIO(1.0) 중립값으로 대체한다.
    """
    unique_keywords = list({c["keyword"] for c in targets})
    trend_map = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_fetch_trend_one, datalab_api, kw, tracker, log, cache): kw
            for kw in unique_keywords
        }
        for future in as_completed(futures):
            kw, ratio = future.result()
            trend_map[kw] = ratio

    confirmed = 0
    failed = 0
    for c in targets:
        raw_ratio = trend_map.get(c["keyword"])
        if raw_ratio is None:
            c["datalab_ratio"] = DATALAB_NEUTRAL_RATIO
            c["datalab_failed"] = True
            failed += 1
        else:
            c["datalab_ratio"] = raw_ratio
            c["datalab_failed"] = False
        c["datalab_skipped"] = False
        c["verify_datalab"] = (not c["datalab_failed"]) and c["datalab_ratio"] >= DATALAB_HARD_CUT
        if c["verify_datalab"]:
            confirmed += 1

    if log:
        log(f"[scorer] 4단계 DataLab 확인 대상 {len(targets)}건 - 상승 확인 {confirmed}건 / "
            f"API 실패(중립값 {DATALAB_NEUTRAL_RATIO} 적용) {failed}건")

    return targets


# =========================================================================
# 6. 신선도 계산
# =========================================================================
def _compute_freshness(latest_pub_date, source):
    if "news" not in source or not latest_pub_date:
        return 0.5
    try:
        pub = datetime.strptime(latest_pub_date, "%Y-%m-%d").date()
    except Exception:
        return 0.1
    diff = (date.today() - pub).days
    if diff <= 1:
        return 1.0
    if diff <= 3:
        return 0.7
    if diff <= 7:
        return 0.4
    return 0.1


def _classify_timing(freshness, datalab_ratio):
    if freshness >= 1.0 and (datalab_ratio or 0) >= DATALAB_HARD_CUT:
        return "오늘"
    if freshness >= 1.0:
        return "오후"
    if freshness >= 0.4:
        return "주간"
    return "상시"


# =========================================================================
# 7. 점수 계산
# =========================================================================
def _compute_issue_score(mentions, freshness, datalab_ratio):
    mention_component = min(mentions, 20) / 20.0
    freshness_component = freshness
    ratio = datalab_ratio if datalab_ratio else 0.5
    datalab_component = min(ratio / 2.0, 1.0)
    score = (mention_component * 0.3 + freshness_component * 0.3 + datalab_component * 0.4) * 10
    return round(score, 2)


def _doc_count_penalty_multiplier(doc_count):
    """문서수 절대값 구간별 페널티 배수(5단계). 구간에 해당 없으면 1.0."""
    if doc_count is None:
        return 1.0
    for threshold, multiplier in DOC_COUNT_PENALTY_TIERS:
        if doc_count >= threshold:
            return multiplier
    return 1.0


def _generic_anchor_penalty_multiplier(anchor, intent_word, doc_count):
    """
    범용 상시 키워드(보험/대출 등) + 검색의도 없음 + 문서수 과다인 경우
    강한 페널티(x0.15)를 적용. 다른 감점과는 곱하지 않고 min()으로만 비교한다.
    """
    if doc_count is None:
        return 1.0
    if anchor in GENERIC_RISK_ANCHORS and not intent_word and doc_count > GENERIC_ANCHOR_DOC_THRESHOLD:
        return GENERIC_ANCHOR_PENALTY_MULTIPLIER
    return 1.0


def _brand_institution_penalty_multiplier(keyword):
    """
    키워드에 기관/대기업 명칭이 포함되면 x0.5 감점.
    다른 감점(문서수/범용어 앵커)과는 곱하지 않고 min()으로만 비교한다.
    """
    if not keyword:
        return 1.0
    for brand in BRAND_INSTITUTION_KEYWORDS:
        if brand in keyword:
            return BRAND_INSTITUTION_PENALTY_MULTIPLIER
    return 1.0


def _brand_longtail_bonus_multiplier(keyword, intent_word, anchor):
    """
    [휴리스틱] 검색 의도가 명확하거나(intent_word 존재) 앵커보다 구체적인
    롱테일 키워드일 경우 소폭 가산한다. 최대 1.5배로 제한.
    """
    bonus = 1.0
    if intent_word:
        bonus += 0.25
    stripped = (keyword or "").replace(" ", "")
    stripped_anchor = (anchor or "").replace(" ", "")
    if len(stripped) >= LONGTAIL_MIN_LENGTH and stripped != stripped_anchor:
        bonus += 0.15
    return min(bonus, BRAND_LONGTAIL_MAX_BONUS)


def _compute_opportunity_score(search_volume, doc_count, anchor, intent_word, keyword):
    """
    OpportunityScore = "검색량은 있는데 경쟁(문서수)은 적은" 키워드에 높은 점수.

    - doc_count가 None이면 계산하지 않고 None을 반환한다.
    - 문서수 절대값 페널티 / 범용 상시 키워드(앵커) 페널티 / 브랜드·기관명
      페널티는 서로 곱하지 않고, 셋 중 가장 강한(작은) 배수 하나만 적용한다.
    - 검색량 500 미만이면 위 페널티 이후 추가로 50% 감점한다.
    - 브랜드/롱테일 가산은 모든 감점이 끝난 다음 마지막에 곱한다.
    """
    if doc_count is None:
        return None

    volume_component = math.log10(search_volume + 1)
    competition_component = math.log10(doc_count + 10)
    base_score = (volume_component / competition_component) * 3.0
    base_score = min(base_score, 10.0)

    doc_penalty = _doc_count_penalty_multiplier(doc_count)
    anchor_penalty = _generic_anchor_penalty_multiplier(anchor, intent_word, doc_count)
    brand_penalty = _brand_institution_penalty_multiplier(keyword)
    combined_penalty = min(doc_penalty, anchor_penalty, brand_penalty)
    score = base_score * combined_penalty

    if search_volume < LOW_VOLUME_PENALTY_CUT:
        score *= LOW_VOLUME_PENALTY_MULTIPLIER

    bonus = _brand_longtail_bonus_multiplier(keyword, intent_word, anchor)
    score *= bonus

    return round(max(0.0, min(score, 10.0)), 2)


def _compute_efficiency_score(search_volume, doc_count):
    """
    EfficiencyScore = log10(효율+1) 기반 0~10 스케일 점수.
    효율 = 검색량 / 문서수. doc_count가 None이면 계산하지 않고 None 반환.
    """
    if doc_count is None:
        return None
    efficiency = search_volume / max(doc_count, 1)
    score = math.log10(efficiency + 1) * EFFICIENCY_LOG_SCALE
    return round(max(0.0, min(score, 10.0)), 2)


def _compute_category_score(category_weight):
    """category_weight(배수)를 0~10 스케일로 환산."""
    weight = category_weight if category_weight is not None else 1.0
    score = weight * 5.0
    return round(max(0.0, min(score, 10.0)), 2)


def _compute_datalab_score(datalab_ratio):
    """datalab_ratio(비율)를 0~10 스케일로 환산."""
    ratio = datalab_ratio if datalab_ratio is not None else DATALAB_NEUTRAL_RATIO
    score = ratio * 5.0
    return round(max(0.0, min(score, 10.0)), 2)


def _compute_efficiency(search_volume, doc_count):
    """
    [표시용 원시 효율값] search_volume/doc_count 그대로. UI 표시나 로그용으로 유지.

    doc_count가 None(문서수 미확인)인 경우 0.0을 반환한다. 화면 표시는
    app.py가 efficiency_unknown 플래그와 함께 판단해 "-"/"확인 불가"로
    대체한다. FinalScore 계산에는 이 원시값이 쓰이지 않는다.
    """
    if doc_count is None:
        return 0.0
    return round(search_volume / max(doc_count, 1), 2)


# =========================================================================
# 8. 등급 분류 (위험 / 보류 / TOP5 / TOP10)
# =========================================================================
def _build_risk_reasons(entry):
    """이 함수는 doc_count가 확인된(not None) 후보에만 호출된다."""
    reasons = []
    anchor = entry.get("anchor", "")
    doc_count = entry.get("doc_count") or 0
    has_intent = bool(entry.get("intent_word"))

    if anchor in GENERIC_RISK_ANCHORS and not has_intent and doc_count > RISK_DOC_ABS:
        reasons.append(f"범용 상시 키워드('{anchor}') + 문서수 {doc_count:,}건으로 경쟁 과다")
    return reasons


def _build_hold_reasons(entry):
    """
    이 함수가 반환하는 사유는 등급을 강제로 낮추지 않는다.
    TOP5/TOP10으로 선정된 후에도 상세 패널에 참고 정보로 표시되는 정보성 사유다.
    """
    reasons = []
    doc_count = entry.get("doc_count") or 0
    efficiency = entry.get("efficiency", 0)

    if RISK_DOC_ABS >= doc_count > HOLD_DOC_ABS:
        reasons.append(f"문서수 과다({doc_count:,}건) - 경쟁 심함")

    if entry.get("datalab_skipped"):
        reasons.append(f"DataLab 미조회(호출 제한, 중립값 {DATALAB_NEUTRAL_RATIO} 적용)")
    elif entry.get("datalab_failed"):
        reasons.append(f"DataLab 조회 실패(중립값 {DATALAB_NEUTRAL_RATIO} 적용)")
    elif not entry.get("verify_datalab"):
        reasons.append(f"DataLab 상승 미확인(비율 {entry.get('datalab_ratio', DATALAB_NEUTRAL_RATIO):.2f})")

    if efficiency < LOW_EFFICIENCY_CUT and doc_count > 0:
        reasons.append(f"검색량 대비 문서수 비효율(효율 {efficiency:.2f})")

    if entry.get("search_volume", 0) < LOW_VOLUME_PENALTY_CUT:
        reasons.append(f"검색량 {entry.get('search_volume', 0)}건으로 저검색량 감점(50%) 적용됨")

    if entry.get("_brand_penalty_applied"):
        reasons.append("브랜드/기관명 포함 키워드로 OpportunityScore 감점 적용됨")

    return reasons


def _build_reason_tags(entry):
    tags = []
    if entry.get("search_volume", 0) >= 3000:
        tags.append("검색량↑")
    if entry.get("verify_datalab"):
        tags.append("DataLab↑")
    doc_count = entry.get("doc_count")
    if doc_count is not None and 0 <= doc_count <= 1500:
        tags.append("경쟁낮음")
    meta = entry.get("category_meta", {})
    if meta.get("cpc") == "high":
        tags.append("CPC높음")
    if "news" in entry.get("source", []) and "ads" in entry.get("source", []):
        tags.append("뉴스+검색 교차확인")
    if entry.get("mentions", 0) >= 5:
        tags.append("다수 매체 언급")
    # 1단계 검색량 확인 관련 태그 (우선순위: 무효키워드 > 조기중단 > API실패 > 호출제한)
    if entry.get("volume_invalid_keyword"):
        tags.append("검색량 키워드 형식 오류")
    elif entry.get("volume_call_aborted"):
        tags.append("검색량 조회 중단(429)")
    elif entry.get("volume_api_failed"):
        tags.append("검색량 확인 실패")
    elif entry.get("volume_check_skipped"):
        tags.append("검색량 미확인(제한)")
    if entry.get("doc_check_skipped"):
        tags.append("문서수 미확인(제한)")
    if entry.get("doc_api_failed"):
        tags.append("문서수 확인 실패")
    if entry.get("doc_call_aborted"):
        tags.append("문서수 조회 중단(429)")
    if entry.get("search_volume", 0) < LOW_VOLUME_PENALTY_CUT:
        tags.append("저검색량 감점")
    if entry.get("_brand_penalty_applied"):
        tags.append("브랜드/기관명 감점")
    # [v19.7 신규] 2차 확장으로 유입된 후보임을 표시(추적/UI 구분용, 등급/점수에는 영향 없음)
    if entry.get("expansion_depth") == 2:
        tags.append("연관검색어 2차 확장")
    return tags


# =========================================================================
# 9. keyword_history.json 저장/로드 (감점/보너스 미적용, 뼈대만, v19.6과 동일)
# =========================================================================
def _load_keyword_history(path, log=None):
    """
    keyword_history.json을 읽어 list[dict]를 반환한다.
    파일이 없거나 손상된 경우 빈 리스트를 반환하고, 파이프라인은 계속 진행된다
    (히스토리 기능은 절대 메인 파이프라인을 막지 않는다).
    """
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        if log:
            log(f"[scorer] keyword_history.json 형식이 예상과 달라(list 아님) 무시하고 새로 시작합니다.")
        return []
    except Exception as e:
        if log:
            log(f"[scorer] keyword_history.json 로드 실패 - 새로 시작합니다: {e}")
        return []


def _prune_keyword_history(records, retention_days=KEYWORD_HISTORY_RETENTION_DAYS):
    """
    run_at 기준으로 retention_days를 초과한 기록은 제거한다.
    run_at 형식이 잘못된(파싱 불가) 레코드도 함께 제거한다.
    """
    cutoff = datetime.now() - timedelta(days=retention_days)
    pruned = []
    for r in records:
        run_at = r.get("run_at", "")
        try:
            dt = datetime.strptime(run_at, KEYWORD_HISTORY_TIMESTAMP_FORMAT)
        except Exception:
            continue
        if dt >= cutoff:
            pruned.append(r)
    return pruned


def _build_history_records(finalized, run_at_str):
    """
    이번 실행의 최종 결과(finalized)에서 히스토리 저장에 필요한 필드만
    추출한다. finalized 원본 entry는 전혀 수정하지 않는다(읽기만 함).
    """
    records = []
    for e in finalized:
        records.append({
            "keyword": e.get("keyword", ""),
            "category": e.get("category", ""),
            "anchor": e.get("anchor", ""),
            "grade": e.get("grade", ""),
            "final_score": e.get("final_score", 0),
            "run_at": run_at_str,
        })
    return records


def _save_keyword_history(path, records, log=None):
    """
    임시파일(.tmp)에 먼저 쓰고 os.replace()로 교체해, 저장 중 오류가 나도
    기존 keyword_history.json이 손상되지 않도록 한다.
    """
    tmp_path = f"{path}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception as e:
        if log:
            log(f"[scorer] keyword_history.json 저장 실패: {e}")
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


def _update_keyword_history(finalized, log=None):
    """
    score_candidates()의 최종 결과(finalized)를 keyword_history.json에
    누적 기록한다. 이 함수는 finalized 자체를 변경하지 않으며, 실패해도
    예외를 밖으로 던지지 않는다(메인 파이프라인에 영향 없음).

    이번 단계에서는 저장된 히스토리를 점수 계산이나 등급 분류에 "사용"하지
    않는다. 다음 개선 단계(반복 감점/앵커·카테고리 제한/신규성 보너스)를
    위한 데이터 축적 뼈대만 제공한다.
    """
    try:
        run_at_str = datetime.now().strftime(KEYWORD_HISTORY_TIMESTAMP_FORMAT)
        existing_history = _load_keyword_history(KEYWORD_HISTORY_FILE, log)
        new_records = _build_history_records(finalized, run_at_str)
        merged_history = existing_history + new_records
        pruned_history = _prune_keyword_history(merged_history, KEYWORD_HISTORY_RETENTION_DAYS)
        _save_keyword_history(KEYWORD_HISTORY_FILE, pruned_history, log)
        if log:
            log(f"[scorer] keyword_history.json 갱신 완료 - 이번 실행 {len(new_records)}건 추가 / "
                f"보관 중 {len(pruned_history)}건 (최근 {KEYWORD_HISTORY_RETENTION_DAYS}일, "
                f"이번 실행에서 사용되지 않음 - 데이터 축적만 수행)")
    except Exception as e:
        if log:
            log(f"[scorer] keyword_history 처리 중 예외 발생(무시하고 계속 진행): {e}")


# =========================================================================
# 10. 메인 인터페이스
# =========================================================================
def score_candidates(candidates, apis, log=None, max_workers=MAX_WORKERS):
    """
    Parameters
    ----------
    candidates : list[dict]
        profit_filter.filter_candidates()의 출력.
    apis : dict
        {"search": NaverSearchAPI, "ads": NaverAdsAPI, "datalab": NaverDataLabAPI}
    log : callable | None

    Returns
    -------
    (results, api_health) : tuple[list[dict], dict]
    """
    tracker = ApiHealthTracker()
    cache = _KeywordCache()
    search_api = apis.get("search")
    ads_api = apis.get("ads")
    datalab_api = apis.get("datalab")

    if log:
        log(f"[scorer] 입력 후보 {len(candidates)}건, 5단계 검증 파이프라인 시작")

    # ---- 1단계: 검색량 확인 (상위 250건 제한 + 429/400 대응) ----
    survived_v, dropped_v, held_v = _check_search_volume(candidates, ads_api, tracker, cache, log, max_workers)

    # ---- 2단계: 연관검색어 확장(1차) (검색량 확인된 대표 후보에만, 429/400 대응) ----
    expanded = _expand_related_keywords(survived_v, ads_api, tracker, log, max_workers)
    pool = _merge_pools(survived_v, expanded)

    # ---- 2단계: 연관검색어 확장(2차, v19.7 신규) ----
    # 1차 확장 결과(expanded) 중 검색량 상위 15개만 대상으로 삼아 추가 API 호출은
    # 최대 15회로 고정한다. total_volume을 그대로 search_volume으로 사용하며
    # get_search_volume() 재호출은 하지 않는다.
    existing_keywords_for_l2 = {c["keyword"] for c in pool}
    expanded_l2 = _expand_related_keywords_level2(
        expanded, ads_api, tracker, existing_keywords_for_l2, log, RELATED_L2_MAX_WORKERS
    )
    pool = _merge_pools(pool, expanded_l2)

    # ---- 3단계: 문서수 확인 (상위 150건만 실제 호출, 배치 처리 + 429 조기중단) ----
    doc_targets, doc_skip = _select_doc_check_targets(pool, DOC_COUNT_CHECK_LIMIT)
    _check_doc_counts(doc_targets, search_api, tracker, cache, log, DOC_MAX_WORKERS)
    for c in doc_skip:
        c["doc_count"] = None
        c["verify_docs"] = False
        c["doc_api_failed"] = False
        c["doc_check_skipped"] = True
        c["doc_call_aborted"] = False
    if log and doc_skip:
        log(f"[scorer] 3단계 호출 제한: 상위 {DOC_COUNT_CHECK_LIMIT}건 외 {len(doc_skip)}건은 "
            f"문서수 미확인(검증보류) 처리")

    all_after_doc = doc_targets + doc_skip
    doc_known = [c for c in all_after_doc if c["doc_count"] is not None]
    doc_unknown = [c for c in all_after_doc if c["doc_count"] is None]

    # ---- 1단계에서 보류된 후보(held_v)는 2/3단계를 건너뛰고
    #      곧바로 문서수 미확인(doc_count=None) 그룹에 준하는 기본값을 채워
    #      아래 5단계 점수 계산 및 등급 분류(보류)로 합류시킨다. ----
    for c in held_v:
        c["doc_count"] = None
        c["verify_docs"] = False
        c["doc_api_failed"] = False
        c["doc_check_skipped"] = False
        c["doc_call_aborted"] = False
        c["datalab_ratio"] = DATALAB_NEUTRAL_RATIO
        c["datalab_failed"] = False
        c["datalab_skipped"] = True
        c["verify_datalab"] = False

    # ---- 4단계: DataLab 확인 (문서수 확인된 후보 중 상위 25건만 실제 호출) ----
    datalab_targets, datalab_skip = _select_datalab_targets(doc_known, DATALAB_CHECK_LIMIT)
    _check_datalab(datalab_targets, datalab_api, tracker, cache, log, DATALAB_MAX_WORKERS)
    for c in datalab_skip:
        c["datalab_ratio"] = DATALAB_NEUTRAL_RATIO
        c["datalab_failed"] = False
        c["datalab_skipped"] = True
        c["verify_datalab"] = False
    for c in doc_unknown:
        c["datalab_ratio"] = DATALAB_NEUTRAL_RATIO
        c["datalab_failed"] = False
        c["datalab_skipped"] = True
        c["verify_datalab"] = False
    if log and datalab_skip:
        log(f"[scorer] 4단계 호출 제한: 상위 {DATALAB_CHECK_LIMIT}건 외 {len(datalab_skip)}건은 "
            f"DataLab 미조회(중립값 {DATALAB_NEUTRAL_RATIO} 적용)")

    all_candidates = datalab_targets + datalab_skip + doc_unknown + held_v

    # ---- 5단계: 신선도 + 점수 계산 (전체 후보 대상) ----
    scored = []
    for entry in all_candidates:
        freshness = _compute_freshness(entry.get("latest_pub_date"), entry.get("source", []))
        entry["freshness_score"] = round(freshness, 2)
        entry["timing"] = _classify_timing(freshness, entry.get("datalab_ratio"))

        entry["issue_score"] = _compute_issue_score(
            entry.get("mentions", 0), freshness, entry.get("datalab_ratio")
        )
        entry["opportunity_score"] = _compute_opportunity_score(
            entry["search_volume"],
            entry.get("doc_count"),
            entry.get("anchor", ""),
            entry.get("intent_word"),
            entry.get("keyword", ""),
        )
        entry["efficiency_score"] = _compute_efficiency_score(
            entry["search_volume"], entry.get("doc_count")
        )
        entry["category_score"] = _compute_category_score(entry.get("category_weight", 1.0))
        entry["datalab_score"] = _compute_datalab_score(entry.get("datalab_ratio"))
        entry["efficiency"] = _compute_efficiency(entry["search_volume"], entry.get("doc_count"))
        entry["efficiency_unknown"] = entry.get("doc_count") is None

        entry["_brand_penalty_applied"] = (
            _brand_institution_penalty_multiplier(entry.get("keyword", "")) < 1.0
            and entry.get("doc_count") is not None
        )

        opp_for_final = (
            entry["opportunity_score"]
            if entry["opportunity_score"] is not None
            else OPPORTUNITY_NEUTRAL_FOR_UNKNOWN_DOC
        )
        eff_for_final = (
            entry["efficiency_score"]
            if entry["efficiency_score"] is not None
            else EFFICIENCY_NEUTRAL_FOR_UNKNOWN_DOC
        )
        entry["final_score"] = round(
            opp_for_final * WEIGHT_OPPORTUNITY
            + eff_for_final * WEIGHT_EFFICIENCY
            + entry["category_score"] * WEIGHT_CATEGORY
            + entry["issue_score"] * WEIGHT_ISSUE
            + entry["datalab_score"] * WEIGHT_DATALAB,
            2
        )
        entry["verify_news"] = entry.get("mentions", 0) > 0 or "news" in entry.get("source", [])
        scored.append(entry)

    # ---- 등급 분류 1차: 문서수 미확인 -> 즉시 검증보류 / 위험 판정 ----
    remaining = []
    finalized = []
    for entry in scored:
        if entry["doc_count"] is None:
            entry["grade"] = "보류"
            entry["risk_reasons"] = []
            # 1단계(검색량) 관련 보류 사유를 3단계(문서수) 사유보다 먼저 확인한다.
            # [v19.5] 무효 키워드(HTTP 400)를 429/호출제한보다 먼저 확인해 원인을 명확히 구분한다.
            if entry.get("volume_invalid_keyword"):
                entry["hold_reasons"] = [
                    "검색광고 API 키워드 형식 오류(특수문자/길이 제한 등)로 검색량 확인 불가"
                    "(HTTP 400) - 검증보류로 처리"
                ]
            elif entry.get("volume_call_aborted"):
                entry["hold_reasons"] = [
                    "검색량 조회 제한으로 일부 미확인 (429 다발로 1단계 조회 중단) - 검증보류로 처리"
                ]
            elif entry.get("volume_api_failed"):
                entry["hold_reasons"] = ["검색량 확인 실패(API 오류/429) - 검증보류로 처리"]
            elif entry.get("volume_check_skipped"):
                entry["hold_reasons"] = [
                    f"검색량 확인 호출 제한(상위 {SEARCH_VOLUME_CHECK_LIMIT}건 외)으로 미확인 - 검증보류로 처리"
                ]
            elif entry.get("doc_api_failed"):
                entry["hold_reasons"] = ["문서수 확인 실패(API 오류) - 검증보류로 처리"]
            elif entry.get("doc_call_aborted"):
                entry["hold_reasons"] = [
                    "문서수 조회 제한으로 일부 미확인 (429 다발로 조회 중단) - 검증보류로 처리"
                ]
            else:
                entry["hold_reasons"] = [
                    f"호출 제한(상위 {DOC_COUNT_CHECK_LIMIT}건 외)으로 문서수 미확인 - 검증보류로 처리"
                ]
            finalized.append(entry)
            continue

        risk_reasons = _build_risk_reasons(entry)
        if risk_reasons:
            entry["grade"] = "위험"
            entry["risk_reasons"] = risk_reasons
            entry["hold_reasons"] = []
            finalized.append(entry)
            continue

        entry["risk_reasons"] = []
        entry["hold_reasons"] = _build_hold_reasons(entry)  # 정보성 - 등급 배제에는 사용하지 않음
        remaining.append(entry)

    # ---- 등급 분류 2차: 위험 제외 후보 중 final_score 순으로 TOP5/TOP10 채우기 ----
    remaining.sort(key=lambda e: -e["final_score"])

    top5_list = []
    rest_pool = []
    for entry in remaining:
        if len(top5_list) < TOP5_SIZE and entry.get("search_volume", 0) >= TOP5_MIN_SEARCH_VOLUME:
            top5_list.append(entry)
        else:
            if entry.get("search_volume", 0) < TOP5_MIN_SEARCH_VOLUME:
                entry["hold_reasons"].append(
                    f"검색량 {entry.get('search_volume', 0)}건 ({TOP5_MIN_SEARCH_VOLUME} 미만)으로 TOP5 기준 미달"
                )
            rest_pool.append(entry)

    for entry in top5_list:
        entry["grade"] = "TOP5"
        finalized.append(entry)

    top10_eligible = []
    top10_ineligible = []
    for entry in rest_pool:
        if entry.get("search_volume", 0) >= TOP10_MIN_SEARCH_VOLUME:
            top10_eligible.append(entry)
        else:
            entry["hold_reasons"].append(
                f"검색량 {entry.get('search_volume', 0)}건 ({TOP10_MIN_SEARCH_VOLUME} 미만)으로 TOP10 대상에서 제외"
            )
            top10_ineligible.append(entry)

    for idx, entry in enumerate(top10_eligible, start=1):
        if idx <= TOP10_SIZE:
            entry["grade"] = "TOP10"
        else:
            entry["grade"] = "보류"
            if not entry["hold_reasons"]:
                entry["hold_reasons"] = ["TOP15 순위 밖"]
        finalized.append(entry)

    for entry in top10_ineligible:
        entry["grade"] = "보류"
        finalized.append(entry)

    # ---- reason_tags 및 전체 순위 부여 ----
    finalized.sort(key=lambda e: -e["final_score"])
    for rank, entry in enumerate(finalized, start=1):
        entry["rank"] = rank
        entry["reason_tags"] = _build_reason_tags(entry)
        entry.pop("sample_titles", None)
        entry.pop("_brand_penalty_applied", None)

    api_health = tracker.summarize()

    if log:
        grade_counts = {}
        for e in finalized:
            grade_counts[e["grade"]] = grade_counts.get(e["grade"], 0) + 1
        log(f"[scorer] 최종 결과 {len(finalized)}건 - "
            f"TOP5 {grade_counts.get('TOP5', 0)}, TOP10 {grade_counts.get('TOP10', 0)}, "
            f"보류 {grade_counts.get('보류', 0)}, 위험 {grade_counts.get('위험', 0)}")
        log(f"[scorer] API 상태: 검색={api_health['search']}, "
            f"검색광고={api_health['ads']}, DataLab={api_health['datalab']}")

        l2_count = sum(1 for e in finalized if e.get("expansion_depth") == 2)
        log(f"[scorer] 2차 확장(ads_l2) 유입 후보: 최종 결과 포함 {l2_count}건 "
            f"(생성 {len(expanded_l2)}건 중, 문서수/DataLab 상위 선별 통과분)")

        invalid_keyword_count = sum(1 for c in held_v if c.get("volume_invalid_keyword"))
        log(f"[scorer] 1단계 검색량: 저검색량 탈락 {len(dropped_v)}건 / "
            f"API실패·호출제한·조기중단·무효키워드로 보류 {len(held_v)}건 "
            f"(그중 무효키워드/HTTP 400 추정 {invalid_keyword_count}건)")
        log(f"[scorer] 문서수 미확인(API실패+호출제한+조회중단) {len(doc_unknown)}건, "
            f"DataLab 미조회(중립값 적용) {len(datalab_skip) + len(doc_unknown)}건")

        # [v19.5] ads API 실패 유형(400/429/기타) 구분 집계 출력
        error_summary = tracker.error_summary()
        if error_summary["http_400"] or error_summary["http_429"] or error_summary["other"]:
            log(f"[scorer] ads API 실패 유형 집계 - HTTP 400(파라미터 오류) {error_summary['http_400']}회, "
                f"HTTP 429(과다호출) {error_summary['http_429']}회, 기타 {error_summary['other']}회")

    # ---- keyword_history.json 갱신 (finalized/api_health를 변경하지 않음) ----
    if ENABLE_KEYWORD_HISTORY:
        _update_keyword_history(finalized, log)
    elif log:
        log("[scorer] keyword_history 기능 비활성화(ENABLE_KEYWORD_HISTORY=False) - 기록하지 않습니다.")

    return finalized, api_health


if __name__ == "__main__":
    def _print_log(msg):
        print(msg)

    class _DummyAds:
        def get_search_volume(self, keyword):
            return 5000
        def get_related_keywords(self, keyword, limit=30):
            return [{"keyword": f"{keyword} 신청", "total_volume": 3000}]

    class _DummySearch:
        def get_blog_doc_count(self, keyword):
            return 1200

    class _DummyDataLab:
        def get_trend_ratio(self, keyword):
            return 1.6

    dummy_candidates = [
        {"keyword": "민생지원금", "category": "지원금", "anchor": "지원금",
         "intent_word": None, "mentions": 12, "sample_titles": [],
         "seed_query": "민생지원금", "first_pub_date": "2026-06-28", "latest_pub_date": "2026-06-30",
         "intent_score": 0.5, "category_weight": 1.5, "category_meta": {"cpc": "high"}, "source": ["news"]},
    ]
    apis = {"search": _DummySearch(), "ads": _DummyAds(), "datalab": _DummyDataLab()}
    results, health = score_candidates(dummy_candidates, apis, log=_print_log)
    for r in results:
        print(r)
    print("api_health:", health)
