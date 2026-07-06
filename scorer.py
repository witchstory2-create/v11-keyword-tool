# -*- coding: utf-8 -*-
"""
scorer.py (v21)
네이버 블로그 수익형 키워드 발굴 시스템 - 검증/확장/점수화/등급분류 통합 엔진

[파이프라인 내 위치]
  collector.collect_candidates() -> profit_filter.filter_candidates() -> scorer.score_candidates()

[v21 변경 사항 - TOP5/TOP10 카테고리 편중 해소]
  기존 v19.8까지는 adjusted_final_score(검색량/문서수/효율 중심) 내림차순으로
  상위 N개를 그대로 잘라 TOP5/TOP10을 확정했기 때문에, 검색량이 큰 소수의
  범용 앵커(대출/보험/자동차보험 등)가 항상 상위를 독점하는 구조적 문제가
  있었다. v21에서는 다음 5가지를 추가한다.

  1) source 필드 정규화(버그 수정) - collector.py(v18.8)가 문자열로 부여한
     source("seed_based"/"news_derived")를 scorer 내부 규약(리스트 타입)과
     충돌하지 않도록 진입 시점에 origin_mark로 옮기고 source를 리스트로
     정규화한다. 이 처리를 하지 않으면 "news" not in "seed_based"가 부분
     문자열 검사로 오동작해 freshness_score가 왜곡된다.
  2) Specificity Score - 단일어/범용어(대출, 보험, 환급, 지원금 단독)는
     가산점 0, 복합어/다어절 구체 키워드일수록 가산점을 부여해
     adjusted_final_score에 반영한다.
  3) News-derived Bonus - collector.py가 부여한 derived_from=="title_phrase"
     후보에 고정 가산점을 부여한다.
  4) History Penalty 강화 - 등장 1회당 감점률 3%->6%, 누적 감점 상한
     15%->30%로 강화한다(계산 로직 자체는 v19.8과 동일, 상수만 변경).
  5) Category/Anchor Cap + Diversity-aware Ranking - TOP5/TOP10을 확정하는
     블록을 "정렬된 순서대로 순회하되 anchor당 상한 개수까지만 채택"하는
     그리디 선택 로직으로 교체한다. 점수 자체의 우선순위는 그대로 존중하되,
     동일 anchor가 상한을 넘기면 다음 순위(TOP10 또는 보류)로 넘겨 다른
     anchor에 자리를 내주도록 한다.

  collector.py / profit_filter.py / app.py는 전혀 수정하지 않았으며, 기존
  API 호출/검색량/문서수/DataLab 검증 로직, keyword_history.json 저장
  구조, 429 조기중단 로직은 v19.8과 완전히 동일하다.

[v19.8 변경 사항 - History Penalty 1단계(최근 TOP5/TOP10 반복 노출 완화)]
  keyword_history.json에 저장되어 온 데이터를 읽어 최근 7일 내 TOP5/TOP10
  등장 횟수를 adjusted_final_score에 반영한다. final_score 원본은 변경하지
  않으며, 위험/보류 판정에는 영향을 주지 않는다. ENABLE_HISTORY_PENALTY
  플래그로 on/off 가능. _update_keyword_history() 등 기존 히스토리 저장
  구조는 전혀 수정하지 않았다.

[v19.7.1 변경 사항 - keyword_history.json 경로 정리]
  KEYWORD_HISTORY_FILE을 실행 위치 기준 상대경로 대신 BASE_DIR 기준
  절대경로로 변경(app.py의 sys.frozen 분기 패턴과 동일).

[v19.7 변경 사항 - 2차 연관검색어 확장(Ads L2)]
  1차 연관검색어 확장 다음에 2차 확장 단계를 추가. 2차 확장 API 호출은
  최대 15회로 고정, 각 응답당 상위 5개만 신규 후보로 채택(최대 75개),
  total_volume을 그대로 search_volume으로 사용해 재조회 없음. 신규 후보에는
  source=["ads_l2"], expansion_depth=2 부여.

[출력 계약] score_candidates()는 (results, api_health) 튜플을 반환한다.
  - v21 신규 필드: specificity_score(float), news_derived_bonus(float),
    origin_mark(str)
  - v19.8 신규 필드: adjusted_final_score(float), history_penalty_hits(int),
    history_penalty_rate(float)
  - v19.7 신규 필드: expansion_depth(int, 2차 확장 유입 후보에만 존재)
  - 그 외 필드는 기존 버전과 동일

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
MAX_RELATED_PER_CATEGORY = 3    # 카테고리별 연관검색어 확장 대표 후보 수
RELATED_LIMIT = 30              # 대표 후보 1개당 받아올 연관검색어 최대 개수
DATALAB_HARD_CUT = 1.3          # 이 비율 이상이어야 "상승"으로 인정
DATALAB_NEUTRAL_RATIO = 1.0     # DataLab 실패/미조회 시 적용하는 중립값 (탈락시키지 않음)

RISK_DOC_ABS = 50000            # 이 문서수를 넘으면서 범용 앵커이면 "위험"
HOLD_DOC_ABS = 15000            # 이 문서수를 넘으면 "보류" 사유 추가(경쟁 심함, 등급 배제는 아님)
LOW_EFFICIENCY_CUT = 0.5        # search_volume/doc_count 이 값보다 낮으면 비효율 보류 사유 추가

TOP5_SIZE = 5
TOP10_SIZE = 10                 # TOP5 이후 순위 6~15
MAX_WORKERS = 2                 # 1단계(검색량)/2단계(연관검색어) 병렬 수

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

DOC_COUNT_CHECK_LIMIT = 350      # 문서수 조회는 2버킷(A:250+B:100) 방식으로 최대 350건까지만 실제 호출
DOC_COUNT_BUCKET_A_SIZE = 250    # 버킷 A: 검색량×의도점수 상위 250건
DOC_COUNT_BUCKET_B_SIZE = 100    # 버킷 B: 버킷 A를 제외한 나머지 중 카테고리별로 고르게 분배한 100건
DATALAB_CHECK_LIMIT = 25         # DataLab 조회는 문서수 확인된 후보 중 효율 상위 25건까지만 실제 호출
DOC_MAX_WORKERS = 2              # 문서수 조회 병렬 수 (429 방지)
DATALAB_MAX_WORKERS = 1          # DataLab 조회는 순차 처리 (timeout 방지)

# [v19.2] 문서수 조회 429 조기중단 로직 파라미터
DOC_COUNT_BATCH_SIZE = 20           # 이 단위로 나눠서 조회하며 배치마다 실패율을 점검한다
DOC_COUNT_ABORT_MIN_ATTEMPTS = 10   # 최소 이만큼 시도한 뒤부터 중단 여부를 판단한다
DOC_COUNT_ABORT_FAIL_RATE = 0.3     # 누적 실패율이 이 값 이상이면 조회를 중단한다

# 범용/상시성 앵커 - "위험" 등급 및 OpportunityScore 감점에 함께 사용
GENERIC_RISK_ANCHORS = {"보험", "대출", "연금", "세금", "카드", "부동산", "청약"}

# ---- FinalScore 가중치 (Efficiency 신설로 재분배) ----
WEIGHT_OPPORTUNITY = 0.45
WEIGHT_EFFICIENCY = 0.25
WEIGHT_CATEGORY = 0.15
WEIGHT_ISSUE = 0.10
WEIGHT_DATALAB = 0.05

# ---- 검색량 게이트 3단 ----
LOW_VOLUME_PENALTY_CUT = 500        # 이 미만이면 OpportunityScore 50% 추가 감점
LOW_VOLUME_PENALTY_MULTIPLIER = 0.5
TOP5_MIN_SEARCH_VOLUME = 300        # 이 미만이면 TOP5 후보에서 제외
TOP10_MIN_SEARCH_VOLUME = 100       # 이 미만이면 TOP10 후보에서도 제외 (무조건 보류)

# ---- 문서수 절대값 페널티 ----
DOC_COUNT_PENALTY_TIERS = [
    (1_000_000, 0.05),
    (500_000, 0.15),
    (300_000, 0.3),
    (100_000, 0.6),
    (50_000, 0.8),
]

# ---- 범용 상시 키워드(앵커) 감점 ----
GENERIC_ANCHOR_DOC_THRESHOLD = RISK_DOC_ABS   # 50,000
GENERIC_ANCHOR_PENALTY_MULTIPLIER = 0.15

# ---- 브랜드/기관명 페널티 ----
BRAND_INSTITUTION_KEYWORDS = {
    "공단", "심사평가원", "정부24", "네이버", "카카오", "삼성", "현대",
    "국민건강보험", "근로복지공단", "국세청", "국민연금공단",
}
BRAND_INSTITUTION_PENALTY_MULTIPLIER = 0.5

# ---- doc_count 미확인 시 FinalScore 계산용 중립값 ----
OPPORTUNITY_NEUTRAL_FOR_UNKNOWN_DOC = 3.0
EFFICIENCY_NEUTRAL_FOR_UNKNOWN_DOC = 3.0

# ---- 브랜드/롱테일 가산 ----
LONGTAIL_MIN_LENGTH = 6
BRAND_LONGTAIL_MAX_BONUS = 1.5

# ---- EfficiencyScore 스케일 계수 ----
EFFICIENCY_LOG_SCALE = 5.0

# [v19.7.1] PyInstaller 빌드 환경에서 실행 파일 위치를 견고하게 판별하기 위해
# sys.frozen 여부를 명시적으로 분기한다. app.py의 BASE_DIR 계산 방식과 동일.
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---- [v19.6 신규, v19.7.1에서 경로만 변경] keyword_history.json 저장/로드 기능 ----
ENABLE_KEYWORD_HISTORY = True        # False로 바꾸면 히스토리 기능 전체를 끌 수 있음
KEYWORD_HISTORY_FILE = os.path.join(BASE_DIR, "keyword_history.json")
KEYWORD_HISTORY_RETENTION_DAYS = 30  # 이 기간을 넘는 기록은 자동 정리(prune)
KEYWORD_HISTORY_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

# ---- [v21 변경] History Penalty 강화(최근 TOP5/TOP10 반복 노출 완화) ----
ENABLE_HISTORY_PENALTY = True        # False면 감점 없이 v19.7.1과 100% 동일 동작
HISTORY_PENALTY_LOOKBACK_DAYS = 7    # 최근 이 기간(일) 이내 TOP5/TOP10 등장만 집계
HISTORY_PENALTY_RATE_PER_HIT = 0.06  # [v21] 0.03 -> 0.06 (등장 1회당 감점률 2배 강화)
HISTORY_PENALTY_MAX_RATE = 0.30      # [v21] 0.15 -> 0.30 (누적 감점률 상한 2배 강화)

# ---- [v21 신규] Specificity Score / News-derived Bonus / Diversity Cap ----
SPECIFICITY_MAX_SCORE = 4.0          # 0~10 스케일 기준 Specificity 가산점 상한
NEWS_DERIVED_BONUS_SCORE = 1.5       # news_derived(title_phrase) 고정 가산점
TOP5_MAX_PER_ANCHOR = 2              # TOP5 내 동일 anchor 최대 개수
TOP10_MAX_PER_ANCHOR = 3             # TOP10 내 동일 anchor 최대 개수


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
        self._error_counts = {"http_400": 0, "http_429": 0, "other": 0}

    def record(self, api_name, success):
        with self._lock:
            key = "ok" if success else "fail"
            self._counts[api_name][key] += 1

    def record_error_type(self, error_message):
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
        with self._lock:
            return dict(self._error_counts)


def _safe_call(fn, tracker, api_name, log=None, context=""):
    try:
        result = fn()
        tracker.record(api_name, True)
        return result
    except Exception as e:
        tracker.record(api_name, False)
        tracker.record_error_type(str(e))
        if log:
            log(f"[scorer] API 호출 실패 ({api_name}, {context}): {e}")
        return None


class _KeywordCache:
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
_KEYWORD_ALLOWED_PATTERN = re.compile(r"^[가-힣a-zA-Z0-9]+$")


def _sanitize_keyword_for_ads(raw_keyword):
    if not raw_keyword:
        return "", False
    cleaned = re.sub(r"\s+", "", raw_keyword.strip())
    if not cleaned:
        return cleaned, False
    if not _KEYWORD_ALLOWED_PATTERN.match(cleaned):
        return cleaned, False
    if not (MIN_KEYWORD_LENGTH_FOR_ADS <= len(cleaned) <= MAX_KEYWORD_LENGTH_FOR_ADS):
        return cleaned, False
    return cleaned, True


# =========================================================================
# 1C. [v21 신규] collector.py 입력 필드 정규화
# =========================================================================
def _normalize_incoming_source_field(candidates):
    """
    [v21] collector.py(v18.8)가 문자열로 부여한 source("seed_based"/
    "news_derived")를 origin_mark로 옮겨 보존하고, scorer 내부 규약인
    리스트 타입 source(["news"] 등)로 정규화한다.

    이 처리를 하지 않으면 _compute_freshness()의 "news" not in source 검사가
    "news" not in "seed_based" 형태의 부분 문자열 검사로 오동작해 뉴스 기반
    후보의 신선도 계산이 왜곡된다. collector.py는 수정하지 않고 scorer.py
    진입점에서만 안전하게 흡수한다.
    """
    for c in candidates:
        raw_source = c.get("source")
        if isinstance(raw_source, str):
            c["origin_mark"] = raw_source
            c["source"] = ["news"]
        else:
            c.setdefault("origin_mark", "unknown")
            c.setdefault("source", ["news"])


# =========================================================================
# 2. 1단계: 검색량 확인
# =========================================================================
def _fetch_volume_one(ads_api, keyword, tracker, log, cache):
    cached = cache.get_search_volume(keyword)
    if cached != "MISS":
        return keyword, cached
    cleaned, _ = _sanitize_keyword_for_ads(keyword)
    if log:
        log(f"[scorer] ADS 요청(volume): original='{keyword}' -> 전달값='{cleaned}'")
    vol = _safe_call(lambda: ads_api.get_search_volume(cleaned), tracker, "ads", log, f"volume:{keyword}")
    time.sleep(0.3 + random.random() * 0.2)
    result = vol if isinstance(vol, int) else None
    cache.set_search_volume(keyword, result)
    return keyword, result


def _select_volume_check_targets(candidates, limit=SEARCH_VOLUME_CHECK_LIMIT):
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
    selected_keywords, skipped_keywords = _select_volume_check_targets(candidates)

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
    kw = rep["keyword"]
    cleaned, _ = _sanitize_keyword_for_ads(kw)
    related = _safe_call(
        lambda: ads_api.get_related_keywords(cleaned, limit=RELATED_LIMIT),
        tracker, "ads", log, f"related:{kw}"
    )
    time.sleep(0.5 + random.random() * 0.3)
    failed = related is None
    return rep, (related or []), failed


def _expand_related_keywords(survived, ads_api, tracker, log=None, max_workers=MAX_WORKERS):
    if not hasattr(ads_api, "get_related_keywords"):
        if log:
            log("[scorer] ads_api.get_related_keywords 미제공 - 2단계 확장을 건너뜁니다.")
        return []

    reps = _select_representatives(survived)

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
    ranked = sorted(expanded, key=lambda c: -(c.get("search_volume", 0)))
    return ranked[:limit]


def _expand_related_l2_one(ads_api, rep, tracker, log):
    kw = rep["keyword"]
    cleaned, _ = _sanitize_keyword_for_ads(kw)
    related = _safe_call(
        lambda: ads_api.get_related_keywords(cleaned, limit=RELATED_LIMIT),
        tracker, "ads", log, f"related_l2:{kw}"
    )
    time.sleep(0.5 + random.random() * 0.3)
    failed = related is None
    return rep, (related or []), failed


def _expand_related_keywords_level2(expanded, ads_api, tracker, existing_keywords, log=None,
                                     max_workers=RELATED_L2_MAX_WORKERS):
    if not hasattr(ads_api, "get_related_keywords"):
        if log:
            log("[scorer] ads_api.get_related_keywords 미제공 - 2차 확장을 건너뜁니다.")
        return []

    targets_all = _select_l2_targets(expanded, RELATED_L2_TOP_N)

    valid_targets = []
    invalid_target_keywords = []
    for rep in targets_all:
        _, is_valid = _sanitize_keyword_for_ads(rep["keyword"])
        if is_valid:
            valid_targets.append(rep)
        else:
            invalid_target_keywords.append(rep["keyword"])

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
    seen_keywords = set(existing_keywords)

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
# 4. 3단계: 문서수 확인 (버킷 방식 제한 / 배치 처리 + 429 조기중단)
# =========================================================================
def _select_doc_check_targets(pool, limit=DOC_COUNT_CHECK_LIMIT):
    ranked = sorted(
        pool, key=lambda c: -(c.get("search_volume", 0) * c.get("intent_score", 0.3))
    )

    bucket_a = ranked[:DOC_COUNT_BUCKET_A_SIZE]
    remainder = ranked[DOC_COUNT_BUCKET_A_SIZE:]

    by_category = {}
    for c in remainder:
        by_category.setdefault(c.get("category", ""), []).append(c)
    for cat in by_category:
        by_category[cat].sort(
            key=lambda c: -(c.get("search_volume", 0) * c.get("intent_score", 0.3))
        )

    categories = list(by_category.keys())
    idx_by_cat = {cat: 0 for cat in categories}
    bucket_b = []
    while len(bucket_b) < DOC_COUNT_BUCKET_B_SIZE and categories:
        progressed = False
        for cat in categories:
            if len(bucket_b) >= DOC_COUNT_BUCKET_B_SIZE:
                break
            i = idx_by_cat[cat]
            if i < len(by_category[cat]):
                bucket_b.append(by_category[cat][i])
                idx_by_cat[cat] = i + 1
                progressed = True
        if not progressed:
            break

    selected = bucket_a + bucket_b
    selected_ids = {id(c) for c in selected}
    skipped = [c for c in ranked if id(c) not in selected_ids]

    return selected[:limit], skipped


def _fetch_doc_count_one(search_api, keyword, tracker, log, cache):
    cached = cache.get_doc_count(keyword)
    if cached != "MISS":
        return keyword, cached
    count = _safe_call(
        lambda: search_api.get_blog_doc_count(keyword), tracker, "search", log, f"doc:{keyword}"
    )
    time.sleep(0.15 + random.random() * 0.15)
    cache.set_doc_count(keyword, count)
    return keyword, count


def _check_doc_counts(targets, search_api, tracker, cache, log=None, max_workers=DOC_MAX_WORKERS):
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
    return keyword, ratio


def _check_datalab(targets, datalab_api, tracker, cache, log=None, max_workers=DATALAB_MAX_WORKERS):
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
    if doc_count is None:
        return 1.0
    for threshold, multiplier in DOC_COUNT_PENALTY_TIERS:
        if doc_count >= threshold:
            return multiplier
    return 1.0


def _generic_anchor_penalty_multiplier(anchor, intent_word, doc_count):
    if doc_count is None:
        return 1.0
    if anchor in GENERIC_RISK_ANCHORS and not intent_word and doc_count > GENERIC_ANCHOR_DOC_THRESHOLD:
        return GENERIC_ANCHOR_PENALTY_MULTIPLIER
    return 1.0


def _brand_institution_penalty_multiplier(keyword):
    if not keyword:
        return 1.0
    for brand in BRAND_INSTITUTION_KEYWORDS:
        if brand in keyword:
            return BRAND_INSTITUTION_PENALTY_MULTIPLIER
    return 1.0


def _brand_longtail_bonus_multiplier(keyword, intent_word, anchor):
    bonus = 1.0
    if intent_word:
        bonus += 0.25
    stripped = (keyword or "").replace(" ", "")
    stripped_anchor = (anchor or "").replace(" ", "")
    if len(stripped) >= LONGTAIL_MIN_LENGTH and stripped != stripped_anchor:
        bonus += 0.15
    return min(bonus, BRAND_LONGTAIL_MAX_BONUS)


def _compute_opportunity_score(search_volume, doc_count, anchor, intent_word, keyword):
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
    if doc_count is None:
        return None
    efficiency = search_volume / max(doc_count, 1)
    score = math.log10(efficiency + 1) * EFFICIENCY_LOG_SCALE
    return round(max(0.0, min(score, 10.0)), 2)


def _compute_category_score(category_weight):
    weight = category_weight if category_weight is not None else 1.0
    score = weight * 5.0
    return round(max(0.0, min(score, 10.0)), 2)


def _compute_datalab_score(datalab_ratio):
    ratio = datalab_ratio if datalab_ratio is not None else DATALAB_NEUTRAL_RATIO
    score = ratio * 5.0
    return round(max(0.0, min(score, 10.0)), 2)


def _compute_efficiency(search_volume, doc_count):
    if doc_count is None:
        return 0.0
    return round(search_volume / max(doc_count, 1), 2)


# =========================================================================
# 7B. [v21 신규] Specificity Score / News-derived Bonus
# =========================================================================
def _compute_specificity_score(keyword, anchor, intent_word):
    """
    [v21] 단일어/범용어(대출, 보험, 환급, 지원금 단독)는 0점, 복합어(실손보험,
    청년전세대출 등)나 다어절 구문(민생지원금 지급일 등)일수록 가산점을
    준다. news_derived title_phrase처럼 이미 구체적인 구문에 자연스럽게
    높은 점수가 붙도록 설계했다. 0~10 스케일 기준, 최대 SPECIFICITY_MAX_SCORE.
    """
    plain_kw = (keyword or "").replace(" ", "")
    plain_anchor = (anchor or "").replace(" ", "")
    word_count = len((keyword or "").split())

    if not plain_kw or plain_kw == plain_anchor:
        return 0.0

    score = 0.0
    if word_count == 1 and len(plain_kw) > len(plain_anchor):
        score += 2.0          # 복합어(단일 토큰이지만 anchor 원형보다 구체적)
    if word_count >= 2:
        score += 2.0          # 다어절 구문
        if word_count >= 3:
            score += 1.0
    if intent_word:
        score += 1.0          # 검색의도어 포함

    return round(min(score, SPECIFICITY_MAX_SCORE), 2)


def _compute_news_derived_bonus(entry):
    """
    [v21] collector.py(v18.8)가 부여한 derived_from == "title_phrase"인
    후보에 고정 가산점을 준다. source 필드(리스트/문자열 혼용 이슈로
    _normalize_incoming_source_field에서 이미 origin_mark로 옮겨졌음)는
    참조하지 않고 derived_from만 사용해 충돌을 피한다.
    """
    if entry.get("derived_from") == "title_phrase":
        return NEWS_DERIVED_BONUS_SCORE
    return 0.0


# =========================================================================
# 8. 등급 분류 (위험 / 보류 / TOP5 / TOP10)
# =========================================================================
def _build_risk_reasons(entry):
    reasons = []
    anchor = entry.get("anchor", "")
    doc_count = entry.get("doc_count") or 0
    has_intent = bool(entry.get("intent_word"))

    if anchor in GENERIC_RISK_ANCHORS and not has_intent and doc_count > RISK_DOC_ABS:
        reasons.append(f"범용 상시 키워드('{anchor}') + 문서수 {doc_count:,}건으로 경쟁 과다")
    return reasons


def _build_hold_reasons(entry):
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
    if entry.get("expansion_depth") == 2:
        tags.append("연관검색어 2차 확장")
    # [v21 신규] 다양성 관련 태그
    if entry.get("news_derived_bonus", 0) > 0:
        tags.append("뉴스기반 가산")
    if entry.get("specificity_score", 0) >= 3.0:
        tags.append("구체적 키워드")
    if entry.get("anchor_cap_deferred"):
        tags.append("앵커 다양성 제한으로 순위 조정")
    return tags


# =========================================================================
# 9. keyword_history.json 저장/로드 + History Penalty
# =========================================================================
def _load_keyword_history(path, log=None):
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
    try:
        run_at_str = datetime.now().strftime(KEYWORD_HISTORY_TIMESTAMP_FORMAT)
        existing_history = _load_keyword_history(KEYWORD_HISTORY_FILE, log)
        new_records = _build_history_records(finalized, run_at_str)
        merged_history = existing_history + new_records
        pruned_history = _prune_keyword_history(merged_history, KEYWORD_HISTORY_RETENTION_DAYS)
        _save_keyword_history(KEYWORD_HISTORY_FILE, pruned_history, log)
        if log:
            log(f"[scorer] keyword_history.json 갱신 완료 - 이번 실행 {len(new_records)}건 추가 / "
                f"보관 중 {len(pruned_history)}건 (최근 {KEYWORD_HISTORY_RETENTION_DAYS}일)")
    except Exception as e:
        if log:
            log(f"[scorer] keyword_history 처리 중 예외 발생(무시하고 계속 진행): {e}")


def _count_recent_top_appearances(history_records, lookback_days=HISTORY_PENALTY_LOOKBACK_DAYS):
    """
    최근 lookback_days일 이내 grade가 TOP5/TOP10인 항목만 keyword 단위로
    집계한다. (run_at, keyword) 조합으로 유일화해 실행 1회당 1회로만 카운트.
    """
    cutoff = datetime.now() - timedelta(days=lookback_days)
    seen_run_keyword = set()
    counts = {}
    for r in history_records:
        if r.get("grade") not in ("TOP5", "TOP10"):
            continue
        run_at = r.get("run_at", "")
        try:
            dt = datetime.strptime(run_at, KEYWORD_HISTORY_TIMESTAMP_FORMAT)
        except Exception:
            continue
        if dt < cutoff:
            continue
        keyword = r.get("keyword", "")
        if not keyword:
            continue
        dedup_key = (run_at, keyword)
        if dedup_key in seen_run_keyword:
            continue
        seen_run_keyword.add(dedup_key)
        counts[keyword] = counts.get(keyword, 0) + 1
    return counts


def _apply_history_penalty_default(entry):
    """감점 비대상(보류/위험/doc_count 미확인) 후보의 기본값."""
    entry["adjusted_final_score"] = entry["final_score"]
    entry["history_penalty_hits"] = 0
    entry["history_penalty_rate"] = 0.0


def _apply_history_penalty(entry, appearance_counts):
    """최근 TOP5/TOP10 등장 횟수 기반 adjusted_final_score 계산. [v21] 강화된 상수 적용."""
    hits = appearance_counts.get(entry.get("keyword", ""), 0)
    rate = min(hits * HISTORY_PENALTY_RATE_PER_HIT, HISTORY_PENALTY_MAX_RATE)
    entry["history_penalty_hits"] = hits
    entry["history_penalty_rate"] = rate
    entry["adjusted_final_score"] = round(entry["final_score"] * (1 - rate), 2)


def _apply_diversity_bonus(entry):
    """
    [v21 신규] History Penalty가 적용된 adjusted_final_score에 Specificity
    Score와 News-derived Bonus를 가산한다. "감점 후 가산" 순서로 설계해
    반복 노출 감점과 구체성/뉴스 가산이 서로 상쇄되지 않고 누적되게 한다.
    """
    specificity = _compute_specificity_score(
        entry.get("keyword", ""), entry.get("anchor", ""), entry.get("intent_word")
    )
    news_bonus = _compute_news_derived_bonus(entry)
    entry["specificity_score"] = specificity
    entry["news_derived_bonus"] = news_bonus
    entry["adjusted_final_score"] = round(
        entry["adjusted_final_score"] + specificity + news_bonus, 2
    )


# =========================================================================
# 9B. [v21 신규] Category/Anchor Cap을 반영한 Diversity-aware TOP N 선정
# =========================================================================
def _select_diverse_top_n(sorted_pool, target_size, min_search_volume, max_per_anchor):
    """
    [v21] adjusted_final_score 내림차순으로 이미 정렬된 sorted_pool을
    순서대로 순회하며, anchor 하나당 max_per_anchor개까지만 선택한다.
    검색량 게이트(min_search_volume)는 기존 v19.8과 동일하게 유지한다.
    상한에 걸려 제외된 후보는 deferred pool로 넘어가 다음 단계(TOP10 또는
    보류)에서 다시 기회를 얻는다. 점수 순위 자체를 바꾸는 것이 아니라
    "동일 순위 구간에서 무엇을 우선 채택할지"만 다양성 기준으로 조정한다.
    """
    selected, deferred = [], []
    anchor_counts = {}

    for entry in sorted_pool:
        entry.setdefault("hold_reasons", [])

        if entry.get("search_volume", 0) < min_search_volume:
            entry["hold_reasons"].append(
                f"검색량 {entry.get('search_volume', 0)}건 ({min_search_volume} 미만)으로 기준 미달"
            )
            deferred.append(entry)
            continue

        if len(selected) >= target_size:
            entry["hold_reasons"].append(
                f"상위 {target_size}건 선정 후 순위 밖(다양성 반영)"
            )
            deferred.append(entry)
            continue

        anchor = entry.get("anchor", "")
        if anchor_counts.get(anchor, 0) >= max_per_anchor:
            entry["hold_reasons"].append(
                f"앵커 다양성 제한('{anchor}' 이미 {max_per_anchor}개 선정)으로 이번 순위에서 보류"
            )
            entry["anchor_cap_deferred"] = True
            deferred.append(entry)
            continue

        selected.append(entry)
        anchor_counts[anchor] = anchor_counts.get(anchor, 0) + 1

    return selected, deferred


# =========================================================================
# 10. 메인 인터페이스
# =========================================================================
def score_candidates(candidates, apis, log=None, max_workers=MAX_WORKERS):
    # [v21 신규] collector.py 입력 필드 정규화 (source 타입 충돌 방지)
    _normalize_incoming_source_field(candidates)

    tracker = ApiHealthTracker()
    cache = _KeywordCache()
    search_api = apis.get("search")
    ads_api = apis.get("ads")
    datalab_api = apis.get("datalab")

    if log:
        log(f"[scorer] 입력 후보 {len(candidates)}건, 5단계 검증 파이프라인 시작")

    survived_v, dropped_v, held_v = _check_search_volume(candidates, ads_api, tracker, cache, log, max_workers)

    expanded = _expand_related_keywords(survived_v, ads_api, tracker, log, max_workers)
    pool = _merge_pools(survived_v, expanded)

    existing_keywords_for_l2 = {c["keyword"] for c in pool}
    expanded_l2 = _expand_related_keywords_level2(
        expanded, ads_api, tracker, existing_keywords_for_l2, log, RELATED_L2_MAX_WORKERS
    )
    pool = _merge_pools(pool, expanded_l2)

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

    remaining = []
    finalized = []
    for entry in scored:
        if entry["doc_count"] is None:
            entry["grade"] = "보류"
            entry["risk_reasons"] = []
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
            _apply_history_penalty_default(entry)
            finalized.append(entry)
            continue

        risk_reasons = _build_risk_reasons(entry)
        if risk_reasons:
            entry["grade"] = "위험"
            entry["risk_reasons"] = risk_reasons
            entry["hold_reasons"] = []
            _apply_history_penalty_default(entry)
            finalized.append(entry)
            continue

        entry["risk_reasons"] = []
        entry["hold_reasons"] = _build_hold_reasons(entry)
        remaining.append(entry)

    if ENABLE_HISTORY_PENALTY:
        _history_records_for_penalty = _load_keyword_history(KEYWORD_HISTORY_FILE, log)
        _appearance_counts = _count_recent_top_appearances(
            _history_records_for_penalty, HISTORY_PENALTY_LOOKBACK_DAYS
        )
        for entry in remaining:
            _apply_history_penalty(entry, _appearance_counts)
    else:
        for entry in remaining:
            _apply_history_penalty_default(entry)

    # [v21 신규] Specificity Score + News-derived Bonus 적용
    # (History Penalty가 반영된 adjusted_final_score 위에 가산하는 순서)
    for entry in remaining:
        _apply_diversity_bonus(entry)

    remaining.sort(key=lambda e: -e["adjusted_final_score"])

    # [v21 변경] 단순 슬라이싱 대신 Category/Anchor Cap을 반영한
    # Diversity-aware 선정 로직으로 교체
    top5_list, rest_pool = _select_diverse_top_n(
        remaining, TOP5_SIZE, TOP5_MIN_SEARCH_VOLUME, TOP5_MAX_PER_ANCHOR
    )
    for entry in top5_list:
        entry["grade"] = "TOP5"
        finalized.append(entry)

    top10_eligible, top10_ineligible = _select_diverse_top_n(
        rest_pool, TOP10_SIZE, TOP10_MIN_SEARCH_VOLUME, TOP10_MAX_PER_ANCHOR
    )
    for entry in top10_eligible:
        entry["grade"] = "TOP10"
        finalized.append(entry)
    for entry in top10_ineligible:
        entry["grade"] = "보류"
        if not entry["hold_reasons"]:
            entry["hold_reasons"] = ["TOP15 순위 밖"]
        finalized.append(entry)

    finalized.sort(key=lambda e: -e["adjusted_final_score"])
    for rank, entry in enumerate(finalized, start=1):
        entry["rank"] = rank
        entry["reason_tags"] = _build_reason_tags(entry)
        entry.pop("sample_titles", None)
        entry.pop("_brand_penalty_applied", None)
        entry.pop("anchor_cap_deferred", None)

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

        error_summary = tracker.error_summary()
        if error_summary["http_400"] or error_summary["http_429"] or error_summary["other"]:
            log(f"[scorer] ads API 실패 유형 집계 - HTTP 400(파라미터 오류) {error_summary['http_400']}회, "
                f"HTTP 429(과다호출) {error_summary['http_429']}회, 기타 {error_summary['other']}회")

        penalty_hit_count = sum(1 for e in finalized if e.get("history_penalty_hits", 0) > 0)
        log(f"[scorer] History Penalty(최근 {HISTORY_PENALTY_LOOKBACK_DAYS}일 TOP5/TOP10 반복, "
            f"등장1회당 {HISTORY_PENALTY_RATE_PER_HIT*100:.0f}%/누적상한 {HISTORY_PENALTY_MAX_RATE*100:.0f}%) "
            f"적용 대상 {penalty_hit_count}건 (ENABLE_HISTORY_PENALTY={ENABLE_HISTORY_PENALTY})")

        # [v21 신규] Specificity/News-derived Bonus 및 Anchor Cap 적용 통계
        news_bonus_count = sum(1 for e in finalized if e.get("news_derived_bonus", 0) > 0)
        avg_specificity = (
            sum(e.get("specificity_score", 0) for e in finalized if e["grade"] in ("TOP5", "TOP10"))
            / max(sum(1 for e in finalized if e["grade"] in ("TOP5", "TOP10")), 1)
        )
        top5_anchors = [e.get("anchor", "") for e in finalized if e["grade"] == "TOP5"]
        top10_anchors = [e.get("anchor", "") for e in finalized if e["grade"] == "TOP10"]
        log(f"[scorer] [v21] News-derived Bonus 적용 {news_bonus_count}건, "
            f"TOP5+TOP10 평균 Specificity {avg_specificity:.2f} "
            f"(TOP5/TOP10 앵커 상한: {TOP5_MAX_PER_ANCHOR}/{TOP10_MAX_PER_ANCHOR})")
        log(f"[scorer] [v21] TOP5 anchor 분포: {top5_anchors}")
        log(f"[scorer] [v21] TOP10 anchor 분포: {top10_anchors}")

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
         "intent_score": 0.5, "category_weight": 1.5, "category_meta": {"cpc": "high"}, "source": "seed_based"},
        {"keyword": "민생지원금 지급일", "category": "지원금", "anchor": "지원금",
         "intent_word": "지급일", "mentions": 4, "sample_titles": [],
         "seed_query": "민생지원금", "first_pub_date": "2026-06-29", "latest_pub_date": "2026-06-30",
         "intent_score": 0.4, "category_weight": 1.5, "category_meta": {"cpc": "high"},
         "source": "news_derived", "derived_from": "title_phrase"},
    ]
    apis = {"search": _DummySearch(), "ads": _DummyAds(), "datalab": _DummyDataLab()}
    results, health = score_candidates(dummy_candidates, apis, log=_print_log)
    for r in results:
        print(r["keyword"], r["grade"], r.get("adjusted_final_score"),
              r.get("specificity_score"), r.get("news_derived_bonus"), r.get("origin_mark"))
    print("api_health:", health)
