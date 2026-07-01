# -*- coding: utf-8 -*-
"""
scorer.py (v18.2) - Verification + Scoring 단계 전담

[이 파일의 역할]
- collector.py -> profit_filter.py를 거친 후보(keyword, category, mentions, articles,
  recency, category_weight, has_intent, profit_tags 등)를 입력으로 받는다.
- naver_search_api.py(NaverSearchAPI/NaverDataLabAPI/NaverAdsAPI)를 호출하여
  검색량 / 문서수 / DataLab 상승률을 실측 교차검증한다.
- 4단계 교차검증을 수행한다: 뉴스 언급(이미 통과) -> 검색량 확인 -> 문서수 확인 -> DataLab 확인.
  API가 '설정되어 있는데' 특정 후보에서 값이 확인되지 않으면 그 후보는 탈락시킨다.
  API 자체가 '설정되지 않은' 경우는 해당 단계 요구를 생략한다(전체 결과가 0이 되는 것을 방지).
- 검색량 대비 문서수 효율을 계산한다.
- FinalScore = IssueScore * OpportunityScore * CategoryWeight 원칙으로 최종 점수를 산출한다.
- 과포화(문서수 과다) 및 범용 상시성 키워드는 하향 조정한다.
- 최종적으로 TOP5 / TOP10 / 보류 / 위험 4단계로 분류한다.

[이 파일이 하지 않는 일 - 다른 파일의 책임]
- 뉴스 수집, 후보 추출 -> collector.py
- 수익형 카테고리 판단, 가중치 원천 데이터 제공 -> profit_filter.py
- 실제 API 호출 구현(서명 생성, HTTP 요청 등) -> naver_search_api.py
- 화면 표시 -> app.py

[등급 정책]
- 위험(빨강) : 오직 '범용 상시성 키워드(시드 자체)'일 때만 부여.
- 보류(회색) : 문서수 과다 또는 DataLab 상승률 미달 등 '오늘 매력적이지 않은' 경우.
- TOP5 / TOP10 : 위험/보류가 아니면서 신뢰도(confidence) 충족 + 순위가 높은 경우.
"""

import math

SPIKE_HARDCUT = 1.3
RISK_DOC_GENERIC = 300_000     # 범용 키워드 + 이 이상 문서수 => 위험(경쟁 극심)
HOLD_DOC_ABS = 1_000_000       # 일반 키워드라도 이 이상 문서수 => 보류(경쟁 심함)
CPC_DEFAULT = 450


def _log(fn, msg):
    if fn:
        fn(msg)


def _norm(value, max_value):
    """value를 0~1 사이로 정규화한다. max_value가 0 이하이면 0을 반환한다."""
    if not max_value or max_value <= 0:
        return 0.0
    return max(0.0, min(1.0, value / max_value))


def estimate_monthly_revenue_won(search_volume, doc_count, cpc=CPC_DEFAULT,
                                  ctr=0.03, ad_click_rate=0.35, rank_share=0.05):
    """
    참고용 추정 월수익(원)을 계산한다.
    - 문서수가 많을수록 포화 페널티(saturation_penalty)가 커져 방문자 추정치가 줄어든다.
    - 이 값은 정밀한 실측이 아니라 우선순위 판단을 돕기 위한 참고 지표임을 화면에 함께 표기해야 한다.
    """
    if not search_volume or search_volume <= 0:
        return None
    saturation_penalty = 1.0
    if doc_count and doc_count > 0:
        saturation_penalty = 1.0 / (1.0 + math.log10(doc_count + 10) / 3.0)
    visits = search_volume * rank_share * saturation_penalty
    clicks = visits * ctr * ad_click_rate
    revenue = clicks * cpc
    return int(revenue)


def _timing_label(recency, spike_ratio):
    """최근성(recency, 시간 단위) + DataLab 상승률을 조합해 작성 타이밍 등급을 매긴다."""
    if recency is not None and recency <= 12 and (spike_ratio or 0) >= SPIKE_HARDCUT:
        return "오늘"
    if recency is not None and recency <= 24:
        return "오후"
    if recency is not None and recency <= 168:
        return "주간"
    return "상시"


def verify_candidate(cand, search_api, datalab_api, ads_api, log=None):
    """
    collector.py -> profit_filter.py가 넘긴 후보에
    naver_search_api.py 실측값(doc_count/search_volume/spike_ratio)을 덧붙인다.
    원본 필드(keyword/category/mentions/articles/recency/category_weight/has_intent/
    profit_tags/matched_seed/generic_flag)는 dict(cand)로 그대로 보존된다.
    """
    keyword = cand["keyword"]
    result = dict(cand)
    result.update({
        "doc_count": None, "doc_status": "검증 실패", "doc_error": None,
        "search_volume": None, "search_status": "검증 실패", "search_error": None,
        "comp_idx": None,
        "spike_ratio": None, "datalab_status": "검증 실패", "datalab_error": None,
    })

    if search_api and search_api.client_id and search_api.client_secret:
        blog_total, err1 = search_api.get_blog_doc_count(keyword)
        news_total, err2 = search_api.get_news_doc_count(keyword)
        if blog_total is not None:
            result["doc_count"] = blog_total + (news_total or 0)
            result["doc_status"] = "검증 완료"
        else:
            result["doc_error"] = err1 or err2
            _log(log, f"[scorer] 문서수 검증 실패({keyword}): {result['doc_error']}")
    else:
        result["doc_error"] = "검색 API 키 미설정"

    if ads_api and ads_api.customer_id and ads_api.license_key and ads_api.secret_key:
        stats, err = ads_api.get_keyword_stats(keyword)
        if stats is not None:
            result["search_volume"] = stats["total"]
            result["comp_idx"] = stats["comp_idx"]
            result["search_status"] = "검증 완료"
        else:
            result["search_error"] = err
            _log(log, f"[scorer] 검색량 검증 실패({keyword}): {err}")
    else:
        result["search_error"] = "검색광고 API 키 미설정"

    if datalab_api and datalab_api.client_id and datalab_api.client_secret:
        ok, spike, err = datalab_api.get_spike_ratio(keyword)
        if ok:
            result["spike_ratio"] = spike
            result["datalab_status"] = "검증 완료"
        else:
            result["datalab_error"] = err
            _log(log, f"[scorer] DataLab 검증 실패({keyword}): {err}")
    else:
        result["datalab_error"] = "DataLab API 키 미설정"

    return result


def _recommend_tags(r):
    """화면에 표시할 '추천 이유' 태그 목록을 만든다."""
    tags = []
    if r.get("category"):
        tags.append(f"카테고리: {r['category']} (가중치 x{r.get('category_weight', 1.0):.1f})")
    if r.get("mentions"):
        tags.append(f"뉴스 언급 {r['mentions']}건")
    if r.get("recency") is not None:
        tags.append(f"최근 언급 {r['recency']}시간 전")
    if r.get("spike_ratio") is not None:
        tags.append(f"DataLab 상승률 x{r['spike_ratio']:.2f}")
    if r.get("search_volume") is not None:
        tags.append(f"검색량 {r['search_volume']:,}")
    if r.get("doc_count") is not None:
        tags.append(f"문서수 {r['doc_count']:,}")
    if r.get("efficiency") is not None:
        tags.append(f"효율(검색량/문서수) {r['efficiency']:.2f}")
    tags.extend(r.get("profit_tags", []))
    return tags


def _risk_reasons(r):
    """'쓰면 안 되는' 진짜 위험 요인만 판정. 오직 범용 상시성 키워드(시드 자체)일 때만 부여."""
    reasons = []
    if r.get("generic_flag"):
        doc = r.get("doc_count")
        if doc is not None and doc > RISK_DOC_GENERIC:
            reasons.append(f"범용(상시성) 키워드 + 문서수 과다({doc:,}건) - 경쟁 극심")
        else:
            reasons.append("범용(상시성) 키워드 - 항상 검색되지만 경쟁도 항상 치열함")
    return reasons


def _hold_reasons(r):
    """위험은 아니지만 '오늘 당장 쓸 만큼 매력적이지 않은' 이유. TOP5/10에서 우선순위가 밀리는 요인."""
    reasons = []
    doc = r.get("doc_count")
    if doc is not None and doc > HOLD_DOC_ABS:
        reasons.append(f"문서수 절대치 과다({doc:,}건) - 경쟁 심함")
    if r.get("spike_ratio") is not None and r["spike_ratio"] < SPIKE_HARDCUT:
        reasons.append(f"DataLab 상승률 미달(x{r['spike_ratio']:.2f} < x{SPIKE_HARDCUT})")
    return reasons


def score_candidates(candidates, search_api=None, datalab_api=None, ads_api=None,
                      verify_top_n=40, log=None):
    """
    Verification + Scoring 전담 함수.

    처리 순서:
    1) 어느 API가 실제로 설정되어 있는지 확인한다(전체 미설정 시 경고 로그만 남기고 계속 진행).
    2) verify_top_n개까지의 후보에 대해 verify_candidate()로 실측값을 채운다.
    3) 4단계 교차검증: '설정된' API에서 값이 확인되지 않은 후보는 탈락시킨다.
    4) IssueScore(뉴스언급+DataLab상승률+검색량+최근성)와
       OpportunityScore(검색량*상승률/문서수 로그, 0~1 정규화)를 계산한다.
    5) FinalScore = IssueScore * OpportunityScore * CategoryWeight 원칙에
       범용어 페널티, 보류 페널티, API 신뢰도(confidence)를 추가로 곱한다.
    6) 순위와 위험/보류 사유를 기준으로 TOP5/TOP10/보류/위험 등급을 매긴다.
    """
    search_enabled = bool(search_api and search_api.client_id and search_api.client_secret)
    ads_enabled = bool(ads_api and ads_api.customer_id and ads_api.license_key and ads_api.secret_key)
    datalab_enabled = bool(datalab_api and datalab_api.client_id and datalab_api.client_secret)

    if not (search_enabled or ads_enabled or datalab_enabled):
        _log(log, "[scorer] 경고: 검색/검색광고/DataLab API가 모두 설정되지 않았습니다. "
                  "뉴스 언급만으로 결과를 생성하며 정확도가 크게 떨어집니다.")

    _log(log, f"[scorer] 검증 대상 {min(len(candidates), verify_top_n)}개 "
              f"(검색API={'ON' if search_enabled else 'OFF'}, "
              f"검색광고API={'ON' if ads_enabled else 'OFF'}, "
              f"DataLabAPI={'ON' if datalab_enabled else 'OFF'})")

    verified = []
    drop_stats = {"doc": 0, "search_volume": 0, "datalab": 0}

    for cand in candidates[:verify_top_n]:
        v = verify_candidate(cand, search_api, datalab_api, ads_api, log=log)

        # ---- 4단계 교차검증: 뉴스 언급(이미 통과, mentions>=1) -> 검색량 -> 문서수 -> DataLab ----
        if search_enabled and v["doc_count"] is None:
            drop_stats["doc"] += 1
            continue
        if ads_enabled and (v["search_volume"] is None or v["search_volume"] <= 0):
            drop_stats["search_volume"] += 1
            continue
        if datalab_enabled and v["spike_ratio"] is None:
            drop_stats["datalab"] += 1
            continue

        verified.append(v)

    _log(log, f"[scorer] 4단계 교차검증 통과: {len(verified)}개 "
              f"(문서수 탈락 {drop_stats['doc']}, 검색량 탈락 {drop_stats['search_volume']}, "
              f"DataLab 탈락 {drop_stats['datalab']})")

    if not verified:
        return []

    max_mentions = max([v.get("mentions", 0) for v in verified] or [1])
    max_search = max([v.get("search_volume") or 0 for v in verified] or [1])
    max_opp_raw = 0.0

    # 1차 패스: IssueScore, OpportunityScore(raw), 효율 계산
    for v in verified:
        doc = v.get("doc_count")
        sv = v.get("search_volume")
        spike = v.get("spike_ratio")

        v["efficiency"] = (sv / math.log(doc + 10)) if (sv is not None and doc is not None) else None

        recency_score = 1.0
        if v.get("recency") is not None:
            recency_score = max(0.0, 1.0 - min(v["recency"], 168) / 168.0)

        issue_score = (
            0.3 * _norm(v.get("mentions", 0), max_mentions)
            + 0.3 * _norm((spike or 0), 3.0)
            + 0.2 * _norm((sv or 0), max_search)
            + 0.2 * recency_score
        )
        v["issue_score"] = round(issue_score, 4)

        if sv is not None and doc is not None:
            opp_raw = (sv * max(spike or 1.0, 1.0)) / math.log(doc + 10)
        else:
            opp_raw = 0.0
        v["_opp_raw"] = opp_raw
        max_opp_raw = max(max_opp_raw, opp_raw)

    # 2차 패스: OpportunityScore 정규화 + CategoryWeight 반영한 FinalScore
    for v in verified:
        opp_norm = _norm(v["_opp_raw"], max_opp_raw)
        v["opportunity_score"] = round(opp_norm, 4)

        category_weight = v.get("category_weight", 1.0)

        v["risk_reasons"] = _risk_reasons(v)
        v["hold_reasons"] = _hold_reasons(v)
        v["recommend_tags"] = _recommend_tags(v)
        v["timing"] = _timing_label(v.get("recency"), v.get("spike_ratio"))
        sv, doc = v.get("search_volume"), v.get("doc_count")
        v["estimated_revenue_won"] = estimate_monthly_revenue_won(sv, doc) if (sv and doc is not None) else None

        generic_penalty = 0.4 if v["risk_reasons"] else 1.0
        hold_penalty = 0.75 if v["hold_reasons"] else 1.0

        verified_cnt = sum([
            1 if v["doc_status"] == "검증 완료" else 0,
            1 if v["search_status"] == "검증 완료" else 0,
            1 if v["datalab_status"] == "검증 완료" else 0,
        ])
        confidence = verified_cnt / 3.0
        v["confidence"] = confidence

        # FinalScore = IssueScore * OpportunityScore * CategoryWeight (핵심 원칙)
        base = v["issue_score"] * (0.3 + 0.7 * v["opportunity_score"]) * 100
        final_score = base * category_weight * generic_penalty * hold_penalty * (0.4 + 0.6 * confidence)
        v["final_score"] = round(final_score, 2)
        del v["_opp_raw"]

    verified.sort(key=lambda x: x["final_score"], reverse=True)

    for i, v in enumerate(verified):
        if v["risk_reasons"]:
            v["grade"] = "위험"
        elif v["hold_reasons"] and v["confidence"] < 0.67:
            v["grade"] = "보류"
        elif i < 5 and v["confidence"] >= 0.34:
            v["grade"] = "TOP5"
        elif i < 10 and v["confidence"] >= 0.34:
            v["grade"] = "TOP10"
        else:
            v["grade"] = "보류"

    return verified
