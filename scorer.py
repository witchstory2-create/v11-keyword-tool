# -*- coding: utf-8 -*-
"""
scorer.py (v18.7)
네이버 블로그 수익형 키워드 발굴 시스템 - 검증/확장/점수화/등급분류 통합 엔진

[파이프라인 내 위치]
  collector.collect_candidates() -> profit_filter.filter_candidates() -> scorer.score_candidates()

[v18.7 변경 사항 - 과호출 방지 + TOP5/TOP10 미충족 문제 해결]

1) 핵심 버그 수정: 기존 v18.6은 DataLab 미확인(verify_datalab=False)만으로도
   즉시 "보류" 등급이 확정되어 순위 경쟁(TOP5/TOP10)에 참여할 기회조차 얻지
   못했다. DataLab이 대량 실패/timeout 나던 상황에서는 거의 모든 후보가
   순위 경�쟁 전에 "보류"로 확정되어 TOP5/TOP10이 0건이 되는 원인이었다.
   v18.7부터는 "위험"만 순위 경쟁에서 배제하고, 문서수가 확인된 나머지
   후보는 모두 final_score 순위 경쟁에 참여시켜 TOP5/TOP10을 반드시
   상위권부터 채운다. DataLab 미확인 사유는 등급을 낮추지 않고 상세
   패널에 표시되는 정보성 hold_reasons로만 남는다.

2) 문서수 조회(3단계)를 검색량×의도점수 상위 DOC_COUNT_CHECK_LIMIT(50)건으로
   제한한다. 나머지는 API를 호출하지 않고 "호출 제한으로 문서수 미확인"
   사유로 즉시 "보류"(검증보류) 처리한다. 문서수 API 자체가 실패한
   경우도 동일하게 "위험"이 아닌 "보류"(검증보류)로 처리한다.

3) DataLab 조회(4단계)는 문서수가 확인된 후보 중 효율(검색량/문서수) 상위
   DATALAB_CHECK_LIMIT(25)건으로 제한한다. 조회에서 제외되거나 실패한
   후보는 탈락시키지 않고 중립값 DATALAB_NEUTRAL_RATIO(1.0)를 적용해
   점수 계산에는 참여하지만 "상승 확인"으로는 인정하지 않는다.

4) 문서수/DataLab 조회의 병렬 처리 수를 각각 DOC_MAX_WORKERS=2,
   DATALAB_MAX_WORKERS=1로 낮추고 호출 간 대기 시간을 늘려 429(호출
   한도 초과) 발생 자체를 줄인다. (naver_search_api.py v18.8의 재시도/
   backoff와 함께 이중으로 방어)

5) 실행 중 동일 키워드에 대한 중복 API 호출을 막기 위해 검색량/문서수/
   DataLab 결과를 캐시하는 _KeywordCache를 추가했다.

[이 파일이 담당하는 5단계]
  1단계 : 검색량 확인        (ads_api.get_search_volume)  - 검색량 0인 후보는 조기 탈락
  2단계 : 연관검색어 확장    (ads_api.get_related_keywords) - "검색량이 확인된" 대표 후보에만 한정
  3단계 : 문서수 확인        (search_api.get_blog_doc_count) - 상위 50건 제한
  4단계 : DataLab 확인       (datalab_api.get_trend_ratio)   - 상위 25건 제한
  5단계 : 점수화/등급분류    IssueScore, OpportunityScore, FinalScore, 위험/보류/TOP5/TOP10

[출력 계약] score_candidates()는 (results, api_health) 튜플을 반환한다. (v18.6과 동일 필드 유지)

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
MAX_WORKERS = 4                 # 1단계(검색량)/2단계(연관검색어) 병렬 수 - 기존 유지

# [v18.7 신규] 3단계/4단계 API 과호출 방지용 제한값 및 전용 병렬 수
DOC_COUNT_CHECK_LIMIT = 50       # 문서수 조회는 검색량×의도점수 상위 50건까지만 실제 호출
DATALAB_CHECK_LIMIT = 25         # DataLab 조회는 문서수 확인된 후보 중 효율 상위 25건까지만 실제 호출
DOC_MAX_WORKERS = 2              # 문서수 조회 병렬 수 축소 (429 방지)
DATALAB_MAX_WORKERS = 1          # DataLab 조회는 순차 처리 (timeout 방지)

# 범용/상시성 앵커 - "위험"은 오직 이 앵커들에서만 부여 (요청사항: 범용어에만 위험 적용)
GENERIC_RISK_ANCHORS = {"보험", "대출", "연금", "세금", "카드", "부동산", "청약"}


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
    [v18.7 신규] 실행 중(score_candidates 1회 호출 동안) 동일 키워드에 대한
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
# 3. 2단계: 연관검색어 확장 (검색량 확인된 후보 중 대표만) - v18.6과 동일
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
# 4. 3단계: 문서수 확인 (상위 50건 제한 - v18.7 신규)
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
    [v18.7] 대상은 이미 상위 N건으로 제한된 상태로 들어온다.
    문서수 API 실패는 더 이상 candidates에서 제거(drop)하지 않고, doc_count=None,
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
# 5. 4단계: DataLab 확인 (문서수 확인된 후보 중 상위 25건 제한 - v18.7 신규)
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
    [v18.7] 실패/timeout은 더 이상 탈락이 아니라 DATALAB_NEUTRAL_RATIO(1.0) 중립값으로 대체한다.
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


def _compute_opportunity_score(search_volume, doc_count):
    doc_count = doc_count if doc_count is not None else 0
    volume_component = math.log10(search_volume + 1)
    competition_component = math.log10(doc_count + 10)
    score = (volume_component / competition_component) * 3.0
    return round(min(score, 10.0), 2)


def _compute_efficiency(search_volume, doc_count):
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
    [v18.7] 이 함수가 반환하는 사유는 더 이상 등급을 강제로 낮추지 않는다.
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

    # ---- 3단계: 문서수 확인 (상위 50건만 실제 호출) ----
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
            entry["search_volume"], entry.get("doc_count")
        )
        entry["efficiency"] = _compute_efficiency(entry["search_volume"], entry.get("doc_count"))
        entry["final_score"] = round(
            entry["issue_score"] * entry["opportunity_score"] * entry.get("category_weight", 1.0), 2
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

    # ---- 등급 분류 2차: 위험 제외 후보 중 final_score 순으로 TOP5/TOP10 반드시 채우기 ----
    remaining.sort(key=lambda e: -e["final_score"])
    for idx, entry in enumerate(remaining, start=1):
        if idx <= TOP5_SIZE:
            entry["grade"] = "TOP5"
        elif idx <= TOP5_SIZE + TOP10_SIZE:
            entry["grade"] = "TOP10"
        else:
            entry["grade"] = "보류"
            if not entry["hold_reasons"]:
                entry["hold_reasons"] = ["TOP15 순위 밖"]
        finalized.append(entry)

    # ---- reason_tags 및 전체 순위 부여 ----
    finalized.sort(key=lambda e: -e["final_score"])
    for rank, entry in enumerate(finalized, start=1):
        entry["rank"] = rank
        entry["reason_tags"] = _build_reason_tags(entry)
        entry.pop("sample_titles", None)

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
