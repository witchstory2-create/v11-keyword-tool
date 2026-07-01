# -*- coding: utf-8 -*-
"""
scorer.py (v18.6)
네이버 블로그 수익형 키워드 발굴 시스템 - 검증/확장/점수화/등급분류 통합 엔진

[파이프라인 내 위치]
  collector.collect_candidates() -> profit_filter.filter_candidates() -> scorer.score_candidates()

[이 파일이 담당하는 5단계]
  1단계 : 검색량 확인        (ads_api.get_search_volume)  - 검색량 0인 후보는 조기 탈락
  2단계 : 연관검색어 확장    (ads_api.get_related_keywords) - "검색량이 확인된" 대표 후보에만 한정
  3단계 : 문서수 확인        (search_api.get_blog_doc_count)
  4단계 : DataLab 확인       (datalab_api.get_trend_ratio)
  5단계 : 점수화/등급분류    IssueScore, OpportunityScore, FinalScore, 위험/보류/TOP5/TOP10

[핵심 설계 원칙 - API 낭비 방지]
  연관검색어 확장은 "검색량이 이미 확인된" 후보 중 카테고리별 대표 상위 N개에만 적용한다.
  검색량 미확인 상태에서 연관검색어를 먼저 뽑으면 후보가 기하급수적으로 늘어나
  문서수/DataLab 조회에 API 호출이 낭비되므로, 반드시 이 순서를 지킨다.

[출력 계약] score_candidates()는 (results, api_health) 튜플을 반환한다.

  results: list[dict], 각 원소는 아래 필드를 포함한다.
    {
        "rank"              : int    # 전체 순위 (탈락하지 않은 후보 중)
        "grade"             : str    # "TOP5" | "TOP10" | "보류" | "위험"
        "keyword"           : str
        "category"          : str
        "final_score"       : float
        "issue_score"       : float
        "opportunity_score" : float
        "category_weight"   : float
        "search_volume"     : int
        "doc_count"         : int
        "efficiency"        : float  # search_volume / max(doc_count, 1)
        "mentions"          : int    # 뉴스 언급 수 (연관검색어로만 발견된 경우 0)
        "timing"            : str    # "오늘" | "오후" | "주간" | "상시"
        "verify_news"       : bool
        "verify_volume"     : bool
        "verify_docs"       : bool
        "verify_datalab"    : bool
        "risk_reasons"      : list[str]
        "hold_reasons"      : list[str]
        "reason_tags"       : list[str]   # 화면 표시용 추천/보류 사유 (예: "검색량↑", "DataLab↑", "CPC높음")
        "source"            : list[str]   # ["news"], ["ads"], 또는 둘 다
    }

  api_health: dict
    {"search": "ok"|"partial"|"fail", "ads": "ok"|"partial"|"fail", "datalab": "ok"|"partial"|"fail"}
    각 API 호출의 성공/실패 비율을 집계해 분석 종료 후 UI 상단에 상시 표시할 수 있게 한다.

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

RISK_DOC_ABS = 50000            # 이 문서수를 넘으면서 범용 앵커이면 "위험"
HOLD_DOC_ABS = 15000            # 이 문서수를 넘으면 "보류"(경쟁 심함)
LOW_EFFICIENCY_CUT = 0.5        # search_volume/doc_count 이 값보다 낮으면 비효율 보류 사유 추가

TOP5_SIZE = 5
TOP10_SIZE = 10                 # TOP5 이후 순위 6~15
MAX_WORKERS = 4

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
                summary[api_name] = "ok"  # 호출 자체가 없었으면 문제 없음으로 간주
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


# =========================================================================
# 2. 1단계: 검색량 확인
# =========================================================================
def _fetch_volume_one(ads_api, keyword, tracker, log):
    vol = _safe_call(lambda: ads_api.get_search_volume(keyword), tracker, "ads", log, f"volume:{keyword}")
    time.sleep(0.08 + random.random() * 0.08)
    return keyword, (vol if isinstance(vol, int) else 0)


def _check_search_volume(candidates, ads_api, tracker, log=None, max_workers=MAX_WORKERS):
    """모든 후보에 대해 검색량을 조회하고, MIN_SEARCH_VOLUME 미만은 탈락시킨다."""
    unique_keywords = list({c["keyword"] for c in candidates})
    volume_map = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_volume_one, ads_api, kw, tracker, log): kw for kw in unique_keywords}
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
    """카테고리별로 (검색량 x intent_score) 기준 상위 N개만 대표로 선정."""
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
    """
    검색량이 확인된 대표 후보에 한정해 연관검색어를 확장한다.
    연관검색어는 API 호출 한 번으로 검색량 데이터까지 함께 오므로, 반환값을 그대로 신뢰하고
    별도의 검색량 재조회를 하지 않는다 (API 절약의 핵심).
    """
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
                    continue  # 원 앵커와 무관한 광범위 결과 배제

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

    # 중복 제거 (동일 키워드가 여러 대표에서 발견될 수 있음)
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
    """뉴스 기반(검색량 확인됨) + 연관검색어 기반 후보를 병합, source 필드 통합."""
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
# 4. 3단계: 문서수 확인
# =========================================================================
def _fetch_doc_count_one(search_api, keyword, tracker, log):
    count = _safe_call(
        lambda: search_api.get_blog_doc_count(keyword), tracker, "search", log, f"doc:{keyword}"
    )
    time.sleep(0.08 + random.random() * 0.08)
    return keyword, count  # None이면 API 실패, 0이면 정상 응답(문서 없음)


def _check_doc_counts(candidates, search_api, tracker, log=None, max_workers=MAX_WORKERS):
    unique_keywords = list({c["keyword"] for c in candidates})
    doc_map = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_fetch_doc_count_one, search_api, kw, tracker, log): kw
            for kw in unique_keywords
        }
        for future in as_completed(futures):
            kw, count = future.result()
            doc_map[kw] = count

    survived, dropped = [], []
    for c in candidates:
        count = doc_map.get(c["keyword"])
        entry = dict(c)
        if count is None:
            # API 자체가 실패한 경우만 탈락 (문서수 0은 유효한 "경쟁 없음" 신호이므로 살린다)
            entry["doc_count"] = None
            entry["verify_docs"] = False
            dropped.append(entry)
        else:
            entry["doc_count"] = count
            entry["verify_docs"] = True
            survived.append(entry)

    if log:
        log(f"[scorer] 3단계 문서수 확인: 통과 {len(survived)}건 / API 실패 탈락 {len(dropped)}건")

    return survived, dropped


# =========================================================================
# 5. 4단계: DataLab 확인
# =========================================================================
def _fetch_trend_one(datalab_api, keyword, tracker, log):
    ratio = _safe_call(
        lambda: datalab_api.get_trend_ratio(keyword), tracker, "datalab", log, f"trend:{keyword}"
    )
    time.sleep(0.08 + random.random() * 0.08)
    return keyword, ratio  # None이면 실패 또는 데이터 없음


def _check_datalab(candidates, datalab_api, tracker, log=None, max_workers=MAX_WORKERS):
    unique_keywords = list({c["keyword"] for c in candidates})
    trend_map = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_fetch_trend_one, datalab_api, kw, tracker, log): kw
            for kw in unique_keywords
        }
        for future in as_completed(futures):
            kw, ratio = future.result()
            trend_map[kw] = ratio

    for c in candidates:
        ratio = trend_map.get(c["keyword"])
        c["datalab_ratio"] = ratio
        c["verify_datalab"] = ratio is not None and ratio >= DATALAB_HARD_CUT

    if log:
        confirmed = sum(1 for c in candidates if c["verify_datalab"])
        log(f"[scorer] 4단계 DataLab 확인: 상승 확인 {confirmed}건 / 전체 {len(candidates)}건")

    return candidates


# =========================================================================
# 6. 신선도 계산 (여기서 계산 - 요청사항 반영)
# =========================================================================
def _compute_freshness(latest_pub_date, source):
    if "news" not in source or not latest_pub_date:
        return 0.5  # 연관검색어 단독 발견 후보는 중립값
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
    mention_component = min(mentions, 20) / 20.0          # 0~1
    freshness_component = freshness                        # 0~1
    ratio = datalab_ratio if datalab_ratio else 0.5
    datalab_component = min(ratio / 2.0, 1.0)               # 0~1 (비율 2.0 이상이면 만점)
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
    reasons = []
    anchor = entry.get("anchor", "")
    doc_count = entry.get("doc_count") or 0
    has_intent = bool(entry.get("intent_word"))

    # "위험"은 오직 범용/상시성 앵커 + 구체적 검색의도 없음 + 문서수 과다에만 적용
    if anchor in GENERIC_RISK_ANCHORS and not has_intent and doc_count > RISK_DOC_ABS:
        reasons.append(f"범용 상시 키워드('{anchor}') + 문서수 {doc_count:,}건으로 경쟁 과다")
    return reasons


def _build_hold_reasons(entry):
    reasons = []
    doc_count = entry.get("doc_count") or 0
    search_volume = entry.get("search_volume", 0)
    efficiency = entry.get("efficiency", 0)

    if RISK_DOC_ABS >= doc_count > HOLD_DOC_ABS:
        reasons.append(f"문서수 과다({doc_count:,}건) - 경쟁 심함")
    if not entry.get("verify_datalab"):
        if entry.get("datalab_ratio") is None:
            reasons.append("DataLab 데이터 없음")
        else:
            reasons.append(f"DataLab 상승 미확인(비율 {entry['datalab_ratio']:.2f})")
    if efficiency < LOW_EFFICIENCY_CUT and doc_count > 0:
        reasons.append(f"검색량 대비 문서수 비효율(효율 {efficiency:.2f})")
    return reasons


def _build_reason_tags(entry):
    tags = []
    if entry.get("search_volume", 0) >= 3000:
        tags.append("검색량↑")
    if entry.get("verify_datalab"):
        tags.append("DataLab↑")
    doc_count = entry.get("doc_count") or 0
    if 0 <= doc_count <= 1500:
        tags.append("경쟁낮음")
    meta = entry.get("category_meta", {})
    if meta.get("cpc") == "high":
        tags.append("CPC높음")
    if "news" in entry.get("source", []) and "ads" in entry.get("source", []):
        tags.append("뉴스+검색 교차확인")
    if entry.get("mentions", 0) >= 5:
        tags.append("다수 매체 언급")
    return tags


def _classify_grade(entry):
    risk_reasons = _build_risk_reasons(entry)
    if risk_reasons:
        return "위험", risk_reasons, []

    hold_reasons = _build_hold_reasons(entry)
    if hold_reasons:
        return "보류", [], hold_reasons

    return None, [], []  # 등급 미확정 -> final_score로 순위 매긴 뒤 TOP5/TOP10/보류(순위밖) 결정


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
    search_api = apis.get("search")
    ads_api = apis.get("ads")
    datalab_api = apis.get("datalab")

    if log:
        log(f"[scorer] 입력 후보 {len(candidates)}건, 5단계 검증 파이프라인 시작")

    # ---- 1단계: 검색량 확인 ----
    survived_v, dropped_v = _check_search_volume(candidates, ads_api, tracker, log, max_workers)

    # ---- 2단계: 연관검색어 확장 (검색량 확인된 대표 후보에만) ----
    expanded = _expand_related_keywords(survived_v, ads_api, tracker, log, max_workers)
    pool = _merge_pools(survived_v, expanded)

    # ---- 3단계: 문서수 확인 ----
    survived_d, dropped_d = _check_doc_counts(pool, search_api, tracker, log, max_workers)

    # ---- 4단계: DataLab 확인 ----
    survived_d = _check_datalab(survived_d, datalab_api, tracker, log, max_workers)

    # ---- 5단계: 신선도 + 점수 계산 ----
    scored = []
    for entry in survived_d:
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

    # ---- 등급 분류 1차 (위험/보류 확정) ----
    remaining = []
    finalized = []
    for entry in scored:
        grade, risk_reasons, hold_reasons = _classify_grade(entry)
        entry["risk_reasons"] = risk_reasons
        entry["hold_reasons"] = hold_reasons
        if grade == "위험":
            entry["grade"] = "위험"
            finalized.append(entry)
        elif grade == "보류":
            entry["grade"] = "보류"
            finalized.append(entry)
        else:
            remaining.append(entry)

    # ---- 등급 분류 2차 (순위 기반 TOP5/TOP10/순위밖 보류) ----
    remaining.sort(key=lambda e: -e["final_score"])
    for idx, entry in enumerate(remaining, start=1):
        if idx <= TOP5_SIZE:
            entry["grade"] = "TOP5"
        elif idx <= TOP5_SIZE + TOP10_SIZE:
            entry["grade"] = "TOP10"
        else:
            entry["grade"] = "보류"
            entry["hold_reasons"] = entry.get("hold_reasons", []) + ["TOP15 순위 밖"]
        finalized.append(entry)

    # ---- reason_tags 및 전체 순위 부여 ----
    finalized.sort(key=lambda e: -e["final_score"])
    for rank, entry in enumerate(finalized, start=1):
        entry["rank"] = rank
        entry["reason_tags"] = _build_reason_tags(entry)
        # 다운스트림(app.py)에서 불필요한 내부 필드 정리
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
        log(f"[scorer] 탈락 현황: 검색량 미달 {len(dropped_v)}건, 문서수 API 실패 {len(dropped_d)}건")

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
