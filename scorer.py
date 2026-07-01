# -*- coding: utf-8 -*-
"""
scorer.py (v17.2) - Verification + Scoring 단계
검색량(검색광고API) / 문서수(검색API) / DataLab 상승률을 실측하여
IssueScore, OpportunityScore, FinalScore를 계산하고 등급(TOP5/TOP10/보류/위험)을 매긴다.

[등급 정책]
- 위험(빨강)  : 오직 '범용 상시성 키워드(보험/대출/연금 등)'일 때만 부여.
- 보류(회색)  : 문서수 과다 또는 DataLab 상승률 미달처럼 '오늘 당장 매력적이지 않은' 경우.
- TOP5 / TOP10 : 위험/보류가 아니면서 신뢰도(confidence)가 충족되고 순위가 높은 경우.

API 호출이 실패하면 해당 필드는 '검증 실패'로 표시하고 실패 이유를 함께 기록한다.
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
    if not max_value or max_value <= 0:
        return 0.0
    return max(0.0, min(1.0, value / max_value))


def estimate_monthly_revenue_won(search_volume, doc_count, cpc=CPC_DEFAULT,
                                  ctr=0.03, ad_click_rate=0.35, rank_share=0.05):
    if not search_volume or search_volume <= 0:
        return None
    saturation_penalty = 1.0
    if doc_count and doc_count > 0:
        saturation_penalty = 1.0 / (1.0 + math.log10(doc_count + 10) / 3.0)
    visits = search_volume * rank_share * saturation_penalty
    clicks = visits * ctr * ad_click_rate
    revenue = clicks * cpc
    return int(revenue)


def verify_candidate(cand, search_api, datalab_api, ads_api, log=None):
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
    tags = []
    if r.get("news_count"):
        tags.append(f"뉴스 언급 {r['news_count']}건")
    if r.get("spike_ratio") is not None:
        tags.append(f"DataLab 상승률 x{r['spike_ratio']:.2f}")
    if r.get("search_volume") is not None:
        tags.append(f"검색량 {r['search_volume']:,}")
    if r.get("doc_count") is not None:
        tags.append(f"문서수 {r['doc_count']:,}")
    if r.get("efficiency") is not None:
        tags.append(f"효율(검색량/문서수) {r['efficiency']:.2f}")
    return tags


def _risk_reasons(r):
    """'쓰면 안 되는' 진짜 위험 요인만 판정. 오직 범용 상시성 키워드일 때만 부여."""
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
    _log(log, f"[scorer] 검증 대상 {min(len(candidates), verify_top_n)}개")
    verified = []
    for cand in candidates[:verify_top_n]:
        v = verify_candidate(cand, search_api, datalab_api, ads_api, log=log)
        verified.append(v)

    max_news = max([v.get("news_count", 0) for v in verified] or [1])
    max_search = max([v.get("search_volume") or 0 for v in verified] or [1])

    for v in verified:
        doc = v.get("doc_count")
        sv = v.get("search_volume")
        spike = v.get("spike_ratio")

        if sv is not None and doc is not None:
            v["efficiency"] = sv / math.log(doc + 10)
        else:
            v["efficiency"] = None

        issue_score = (
            0.3 * _norm(v.get("news_count", 0), max_news)
            + 0.3 * _norm((spike or 0), 3.0)
            + 0.2 * _norm((sv or 0), max_search)
            + 0.2 * 1.0
        )
        v["issue_score"] = round(issue_score, 4)

        if sv is not None and doc is not None:
            opp_score = (sv * max(spike or 1.0, 1.0)) / math.log(doc + 10)
        else:
            opp_score = 0.0
        v["opportunity_score"] = round(opp_score, 4)

        # 위험/보류 사유 분리 판정
        v["risk_reasons"] = _risk_reasons(v)
        v["hold_reasons"] = _hold_reasons(v)
        v["recommend_tags"] = _recommend_tags(v)
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

        final_score = v["issue_score"] * 40 + min(v["opportunity_score"], 500) * 0.1
        final_score = final_score * generic_penalty * hold_penalty * (0.4 + 0.6 * confidence)
        v["final_score"] = round(final_score, 2)

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
