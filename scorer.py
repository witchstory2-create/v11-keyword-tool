# -*- coding: utf-8 -*-
"""
scorer.py (v19.1)
네이버 블로그 수익형 키워드 발굴 시스템 - 검증/확장/점수화/등급분류 통합 엔진

[파이프라인 내 위치]
  collector.collect_candidates() -> profit_filter.filter_candidates() -> scorer.score_candidates()

[v19.1 변경 사항 - 문서수 조회 상한 확대 (최소 수정)]

  문제: DOC_COUNT_CHECK_LIMIT=50으로 제한되어 있어, 상위 50건 외 후보는
  문서수 API를 호출하지 않고 doc_count=None으로 남아 "보류" 등급 키워드의
  문서수가 화면에 '-'로만 표시되는 문제가 있었다.

  수정: DOC_COUNT_CHECK_LIMIT 값만 50 -> 150으로 상향. 이 상수는
  _select_doc_check_targets()에서 그대로 참조되므로, 파이프라인 구조/
  함수 시그니처/반환 필드는 전혀 변경되지 않는다. DOC_MAX_WORKERS(2)와
  호출 간 sleep(0.15~0.30초)은 429 방지를 위해 기존과 동일하게 유지했다.
  조회 대상이 3배 늘어나는 만큼 문서수 확인 단계의 총 소요 시간도 대략
  3배 늘어날 수 있으니, 실행 후 429/timeout 발생 빈도를 로그로 확인 권장.

  doc_count가 실제로 조회된 보류 키워드는 기존과 동일하게 doc_count에
  정상 숫자가 채워지고, doc_count=None인 경우(API 실패 또는 상위 150건
  밖으로 여전히 미조회)만 그대로 None으로 유지되어 app.py에서 '-'로
  표시된다.

[이하 v19의 설계 배경/로직 설명은 변경 없음 - 참고용으로 유지]

  이전 버전(v18.8)의 한계: OpportunityScore가 log(검색량)/log(문서수) 비율
  기반이라, 검색량 자체가 매우 큰 키워드(예: 검색량 130,000 / 문서수
  1,700,000)는 비율상 여전히 높은 점수를 받아 "자동차보험", "건강보험공단"
  같은 범용/기관 키워드가 TOP5에 올라오는 문제가 있었다.

  1) EfficiencyScore 신설: 검색량/문서수 비율을 log10(효율+1) 기반으로
     0~10 스케일로 환산한 독립 점수.
  2) 문서수 절대값 페널티 강화 (5단계): 100만↑ x0.05, 50만↑ x0.15,
     30만↑ x0.3, 10만↑ x0.6, 5만↑ x0.8
  3) 검색량 게이트 3단: 500 미만 50%감점 / 300 미만 TOP5 제외 /
     100 미만 TOP10도 제외(무조건 보류)
  4) 브랜드/기관명 페널티(x0.5): 공단/심사평가원/정부24/네이버/카카오/
     삼성/현대 등. 문서수 페널티/범용어 앵커 페널티와는 곱하지 않고
     셋 중 가장 강한 배수 하나만 적용.
  5) FinalScore = Opportunity*0.45 + Efficiency*0.25 + Category*0.15
                  + Issue*0.10 + DataLab*0.05

[출력 계약] score_candidates()는 (results, api_health) 튜플을 반환한다. (v19와 동일 필드 유지)

표준 라이브러리만 사용 (math, time, random, threading, datetime, concurrent.futures)
-> PyInstaller / GitHub Actions 빌드 100% 호환. 외부 pip 패키지 없음.
"""

import math
import time
import random
import threading
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor, as_completed


# =========================================================================
# 0. 하이퍼파라미터
# =========================================================================
MIN_SEARCH_VOLUME = 10          # 이보다 검색량이 낮으면 1단계에서 탈락
MAX_RELATED_PER_CATEGORY = 5    # 카테고리별 연관검색어 확장 대표 후보 수 (API 낭비 방지 핵심)
RELATED_LIMIT = 30              # 대표 후보 1개당 받아올 연관검색어 최대 개수
DATALAB_HARD_CUT = 1.3          # 이 비율 이상이어야 "상승"으로 인정
DATALAB_NEUTRAL_RATIO = 1.0     # DataLab 실패/미조회 시 적용하는 중립값 (탈락시키지 않음)

RISK_DOC_ABS = 50000            # 이 문서수를 넘으면서 범용 앵커이면 "위험"
HOLD_DOC_ABS = 15000            # 이 문서수를 넘으면 "보류" 사유 추가(경쟁 심함, 등급 배제는 아님)
LOW_EFFICIENCY_CUT = 0.5        # search_volume/doc_count 이 값보다 낮으면 비효율 보류 사유 추가

TOP5_SIZE = 5
TOP10_SIZE = 10                 # TOP5 이후 순위 6~15
MAX_WORKERS = 4                 # 1단계(검색량)/2단계(연관검색어) 병렬 수

# [v19.1] 50 -> 150 으로 상향 (보류 키워드도 문서수를 확인할 수 있도록)
DOC_COUNT_CHECK_LIMIT = 150      # 문서수 조회는 검색량×의도점수 상위 150건까지만 실제 호출
DATALAB_CHECK_LIMIT = 25         # DataLab 조회는 문서수 확인된 후보 중 효율 상위 25건까지만 실제 호출
DOC_MAX_WORKERS = 2              # 문서수 조회 병렬 수 (429 방지, 변경 없음)
DATALAB_MAX_WORKERS = 1          # DataLab 조회는 순차 처리 (timeout 방지, 변경 없음)

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

    def record(self, api_name, success):
        with self._lock:
            key = "ok" if success else "fail"
            self._counts[api_name][key] += 1

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


def _safe_call(fn, tracker, api_name, log=None, context=""):
    """API 호출을 감싸서 예외를 흡수하고 성공/실패를 tracker에 기록. 실패 시 None 반환."""
    try:
        result = fn()
        tracker.record(api_name, True)
        return result
    except Exception as e:
        tracker.record(api_name, False)
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
# 2. 1단계: 검색량 확인
# =========================================================================
def _fetch_volume_one(ads_api, keyword, tracker, log, cache):
    cached = cache.get_search_volume(keyword)
    if cached != "MISS":
        return keyword, cached
    vol = _safe_call(lambda: ads_api.get_search_volume(keyword), tracker, "ads", log, f"volume:{keyword}")
    time.sleep(0.08 + random.random() * 0.08)
    result = vol if isinstance(vol, int) else 0
    cache.set_search_volume(keyword, result)
    return keyword, result


def _check_search_volume(candidates, ads_api, tracker, cache, log=None, max_workers=MAX_WORKERS):
    """모든 후보에 대해 검색량을 조회하고, MIN_SEARCH_VOLUME 미만은 탈락시킨다."""
    unique_keywords = list({c["keyword"] for c in candidates})
    volume_map = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_volume_one, ads_api, kw, tracker, log, cache): kw for kw in unique_keywords}
        for future in as_completed(futures):
            kw, vol = future.result()
            volume_map[kw] = vol

    survived, dropped = [], []
    for c in candidates:
        vol = volume_map.get(c["keyword"], 0)
        entry = dict(c)
        entry["search_volume"] = vol
        entry["verify_volume"] = vol >= MIN_SEARCH_VOLUME
        if entry["verify_volume"]:
            survived.append(entry)
        else:
            dropped.append(entry)

    if log:
        log(f"[scorer] 1단계 검색량 확인: 통과 {len(survived)}건 / 탈락 {len(dropped)}건 "
            f"(기준 {MIN_SEARCH_VOLUME} 미달)")

    return survived, dropped


# =========================================================================
# 3. 2단계: 연관검색어 확장 (검색량 확인된 후보 중 대표만)
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
    related = _safe_call(
        lambda: ads_api.get_related_keywords(kw, limit=RELATED_LIMIT),
        tracker, "ads", log, f"related:{kw}"
    )
    time.sleep(0.1 + random.random() * 0.1)
    return rep, (related or [])


def _expand_related_keywords(survived, ads_api, tracker, log=None, max_workers=MAX_WORKERS):
    if not hasattr(ads_api, "get_related_keywords"):
        if log:
            log("[scorer] ads_api.get_related_keywords 미제공 - 2단계 확장을 건너뜁니다.")
        return []

    reps = _select_representatives(survived)
    if log:
        log(f"[scorer] 2단계 연관검색어 확장 대상: {len(reps)}건 (카테고리별 상위 {MAX_RELATED_PER_CATEGORY}개)")

    expanded = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_expand_related_one, ads_api, rep, tracker, log): rep for rep in reps}
        for future in as_completed(futures):
            rep, related_items = future.result()
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

    dedup = {}
    for e in expanded:
        key = (e["category"], e["keyword"])
        if key not in dedup:
            dedup[key] = e
    result = list(dedup.values())

    if log:
        log(f"[scorer] 2단계 연관검색어 신규 후보: {len(result)}건")
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
# 4. 3단계: 문서수 확인 (v19.1: 상위 150건 제한)
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
    """
    unique_keywords = list({c["keyword"] for c in targets})
    doc_map = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_fetch_doc_count_one, search_api, kw, tracker, log, cache): kw
            for kw in unique_keywords
        }
        for future in as_completed(futures):
            kw, count = future.result()
            doc_map[kw] = count

    ok_count = 0
    fail_count = 0
    for c in targets:
        count = doc_map.get(c["keyword"])
        if count is None:
            c["doc_count"] = None
            c["verify_docs"] = False
            c["doc_api_failed"] = True
            c["doc_check_skipped"] = False
            fail_count += 1
        else:
            c["doc_count"] = count
            c["verify_docs"] = True
            c["doc_api_failed"] = False
            c["doc_check_skipped"] = False
            ok_count += 1

    if log:
        log(f"[scorer] 3단계 문서수 확인 대상 {len(targets)}건 - 통과 {ok_count}건 / API 실패(검증보류) {fail_count}건")

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
    """[표시용 원시 효율값] search_volume/doc_count 그대로. UI 표시나 로그용으로 유지."""
    doc_count = doc_count if doc_count is not None else 0
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
    if entry.get("doc_check_skipped"):
        tags.append("문서수 미확인(제한)")
    if entry.get("doc_api_failed"):
        tags.append("문서수 확인 실패")
    if entry.get("search_volume", 0) < LOW_VOLUME_PENALTY_CUT:
        tags.append("저검색량 감점")
    if entry.get("_brand_penalty_applied"):
        tags.append("브랜드/기관명 감점")
    return tags


# =========================================================================
# 9. 메인 인터페이스
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

    # ---- 1단계: 검색량 확인 ----
    survived_v, dropped_v = _check_search_volume(candidates, ads_api, tracker, cache, log, max_workers)

    # ---- 2단계: 연관검색어 확장 (검색량 확인된 대표 후보에만) ----
    expanded = _expand_related_keywords(survived_v, ads_api, tracker, log, max_workers)
    pool = _merge_pools(survived_v, expanded)

    # ---- 3단계: 문서수 확인 (v19.1: 상위 150건만 실제 호출) ----
    doc_targets, doc_skip = _select_doc_check_targets(pool, DOC_COUNT_CHECK_LIMIT)
    _check_doc_counts(doc_targets, search_api, tracker, cache, log, DOC_MAX_WORKERS)
    for c in doc_skip:
        c["doc_count"] = None
        c["verify_docs"] = False
        c["doc_api_failed"] = False
        c["doc_check_skipped"] = True
    if log and doc_skip:
        log(f"[scorer] 3단계 호출 제한: 상위 {DOC_COUNT_CHECK_LIMIT}건 외 {len(doc_skip)}건은 "
            f"문서수 미확인(검증보류) 처리")

    all_after_doc = doc_targets + doc_skip
    doc_known = [c for c in all_after_doc if c["doc_count"] is not None]
    doc_unknown = [c for c in all_after_doc if c["doc_count"] is None]

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

    all_candidates = datalab_targets + datalab_skip + doc_unknown

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
            if entry.get("doc_api_failed"):
                entry["hold_reasons"] = ["문서수 확인 실패(API 오류) - 검증보류로 처리"]
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
        log(f"[scorer] 탈락/제한 현황: 검색량 미달 {len(dropped_v)}건, "
            f"문서수 미확인(API실패+호출제한) {len(doc_unknown)}건, "
            f"DataLab 미조회(중립값 적용) {len(datalab_skip) + len(doc_unknown)}건")

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
