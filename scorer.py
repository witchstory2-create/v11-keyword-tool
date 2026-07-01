# -*- coding: utf-8 -*-
"""
scorer.py (v17 - 수익형 키워드 발굴기)

Verification 데이터(문서수/검색량/DataLab)를 받아
IssueScore / OpportunityScore를 계산하고,
위험 판정 + 스파이크 하드컷을 적용해 최종 버킷을 분류한다.

버킷 종류: TOP5 / TOP10 / 상시추천 / 보류 / 위험
"""

import math

CATEGORY_WEIGHTS = {
    "보험": 1.4, "대출금융": 1.3, "세금": 1.3, "연금": 1.2,
    "부동산": 1.1, "지원금": 1.0, "기타": 0.8,
}

# collector.py의 GENERIC_ROOT_BLOCK과 동일한 개념.
# bigram 등 조합형 키워드 안에 이 단어가 '포함'되어 있고 문서수가 과도하면 위험 처리.
GENERIC_ROOT_WORDS = {
    "보험", "대출", "연금", "환급", "지원", "지원금", "정부", "세금", "카드",
}

SPIKE_HARDCUT = 1.3          # 이 미만이면 '이슈성 없음(상시성)'으로 강등
STEADY_OPPORTUNITY_MIN = 0.55  # 상시성이어도 이 이상이면 '상시추천'으로 별도 표시
RISK_DOC_COUNT_WITH_GENERIC = 300_000   # 범용어 포함 + 이 이상 문서수 -> 위험
RISK_DOC_COUNT_ABSOLUTE = 1_000_000     # 범용어 여부와 무관하게 이 이상이면 무조건 위험


def profit_label(weight: float) -> str:
    if weight >= 1.3:
        return "상"
    if weight >= 1.0:
        return "중"
    return "하"


def star_rating(final_score: float) -> str:
    if final_score >= 85:
        return "★★★★★"
    if final_score >= 70:
        return "★★★★☆"
    if final_score >= 55:
        return "★★★☆☆"
    if final_score >= 40:
        return "★★☆☆☆"
    if final_score >= 25:
        return "★☆☆☆☆"
    return "☆☆☆☆☆"


def _minmax_normalize(values):
    finite = [v for v in values if v is not None]
    if not finite:
        return [0.0 for _ in values]
    lo, hi = min(finite), max(finite)
    if hi - lo < 1e-9:
        return [0.5 if v is not None else 0.0 for v in values]
    return [(v - lo) / (hi - lo) if v is not None else 0.0 for v in values]


def saturation_multiplier(doc_count):
    if doc_count is None:
        return 0.6
    if doc_count > 1_000_000:
        return 0.10
    if doc_count > 300_000:
        return 0.25
    if doc_count > 100_000:
        return 0.50
    if doc_count > 30_000:
        return 0.80
    return 1.0


def contains_generic_root(keyword: str) -> str or None:
    """키워드에 포함된 범용 명사가 있으면 그 단어를 반환, 없으면 None."""
    for root in GENERIC_ROOT_WORDS:
        if root in keyword:
            return root
    return None


def efficiency_label(search_volume, doc_count):
    """검색량 대비 문서수 비율을 사람이 이해할 수 있는 등급으로 변환."""
    if not search_volume or doc_count is None:
        return None, "효율 미검증"
    ratio = search_volume / max(doc_count, 1)
    if ratio >= 5:
        return ratio, "효율 매우 좋음"
    if ratio >= 1:
        return ratio, "효율 좋음"
    if ratio >= 0.1:
        return ratio, "효율 보통"
    return ratio, "효율 나쁨(경쟁 과다)"


def compute_scores(candidates: list):
    if not candidates:
        return []

    mentions_list = [c.get("mentions", 0) for c in candidates]
    spike_list = [min(c.get("spike_ratio", 1.0) or 1.0, 5.0) for c in candidates]
    volume_list = [math.log1p(c.get("search_volume") or 0) for c in candidates]
    recency_list = [c.get("recency", 0.3) for c in candidates]

    norm_mentions = _minmax_normalize(mentions_list)
    norm_spike = _minmax_normalize(spike_list)
    norm_volume = _minmax_normalize(volume_list)
    norm_recency = _minmax_normalize(recency_list)

    # 검색량 대비 문서수 비율(효율)을 기회점수의 핵심 축으로 강화
    efficiency_raw = []
    for c in candidates:
        vol = (c.get("search_volume") or 0) + 1
        doc = c.get("doc_count")
        doc_for_log = doc if (doc is not None and doc > 0) else 500_000
        raw = vol / (math.log(doc_for_log + 10) ** 1.5)  # 문서수 페널티를 더 강하게
        efficiency_raw.append(raw)
    norm_efficiency = _minmax_normalize(efficiency_raw)

    results = []
    for i, c in enumerate(candidates):
        issue_score = (
            0.3 * norm_mentions[i] + 0.3 * norm_spike[i]
            + 0.2 * norm_volume[i] + 0.2 * norm_recency[i]
        )
        sat_mult = saturation_multiplier(c.get("doc_count"))
        opportunity_score = norm_efficiency[i] * sat_mult

        cat = c.get("category", "기타")
        cat_weight = CATEGORY_WEIGHTS.get(cat, 0.8)

        # 가중합 방식: 기회점수(문서수/검색량 비율)에 더 큰 비중(0.65)
        final_raw = (0.35 * issue_score + 0.65 * opportunity_score) * cat_weight

        eff_ratio, eff_label = efficiency_label(c.get("search_volume"), c.get("doc_count"))

        results.append({
            **c,
            "issue_score": round(issue_score, 3),
            "opportunity_score": round(opportunity_score, 3),
            "category_weight": cat_weight,
            "profit_label": profit_label(cat_weight),
            "final_raw": final_raw,
            "efficiency_ratio": round(eff_ratio, 3) if eff_ratio is not None else None,
            "efficiency_label": eff_label,
        })

    final_raws = [r["final_raw"] for r in results]
    norm_final = _minmax_normalize(final_raws)
    for r, nf in zip(results, norm_final):
        r["final_score"] = round(nf * 100, 1)

    # ---------------- 위험 판정 ----------------
    for r in results:
        generic_hit = contains_generic_root(r["keyword"])
        doc = r.get("doc_count")
        risk = False
        risk_reasons = []

        if doc is not None and doc > RISK_DOC_COUNT_ABSOLUTE:
            risk = True
            risk_reasons.append(f"문서수 {doc:,}건으로 극단적 과포화")
        elif generic_hit and doc is not None and doc > RISK_DOC_COUNT_WITH_GENERIC:
            risk = True
            risk_reasons.append(f"범용 단어 '{generic_hit}' 포함 + 문서수 {doc:,}건 과포화")

        r["risk"] = risk
        r["risk_reasons"] = risk_reasons

    # ---------------- 스파이크 하드컷 ----------------
    for r in results:
        spike = r.get("spike_ratio", 1.0) or 1.0
        if spike < SPIKE_HARDCUT:
            r["issue_status_final"] = "상시성"
        else:
            r["issue_status_final"] = r.get("trend_status", "상승")

    # ---------------- 순위 부여 (위험 제외 대상만 랭킹) ----------------
    non_risk = [r for r in results if not r["risk"]]
    non_risk.sort(key=lambda r: r["final_score"], reverse=True)
    for rank, r in enumerate(non_risk, start=1):
        r["rank"] = rank

    risk_items = [r for r in results if r["risk"]]
    risk_items.sort(key=lambda r: r["final_score"], reverse=True)
    for r in risk_items:
        r["rank"] = None  # 위험 항목은 정식 순위에서 제외

    # ---------------- 버킷 분류 ----------------
    for r in results:
        if r["risk"]:
            r["bucket"] = "위험"
        elif r["issue_status_final"] == "상시성":
            r["bucket"] = "상시추천" if r["opportunity_score"] >= STEADY_OPPORTUNITY_MIN else "보류"
        elif r["rank"] is not None and r["rank"] <= 5:
            r["bucket"] = "TOP5"
        elif r["rank"] is not None and r["rank"] <= 15:
            r["bucket"] = "TOP10"
        else:
            r["bucket"] = "보류"

    for r in results:
        r["stars"] = star_rating(r["final_score"])
        r["reason_tags"] = _build_reason_tags(r)

    # 최종 정렬: 위험은 맨 아래, 그 외는 점수 내림차순
    results.sort(key=lambda r: (r["risk"], -r["final_score"]))
    return results


def _build_reason_tags(r):
    tags = []

    mentions = r.get("mentions", 0)
    src_cnt = r.get("source_count", 1)
    if mentions >= 3:
        src_txt = " (구글+네이버 동시 언급)" if src_cnt >= 2 else ""
        tags.append(f"뉴스 언급 {mentions}건{src_txt}")

    spike = r.get("spike_ratio")
    status = r.get("trend_status", "미검증")
    if spike is not None:
        if r["issue_status_final"] == "상시성":
            tags.append(f"DataLab 상승률 x{spike:.2f} (이슈성 낮음, 상시성)")
        elif status == "급증":
            tags.append(f"DataLab 급증 x{spike:.2f}")
        elif status == "상승":
            tags.append(f"DataLab 상승 x{spike:.2f}")
        else:
            tags.append("DataLab 미검증")

    doc_count = r.get("doc_count")
    if doc_count is not None:
        tags.append(f"문서수 {doc_count:,}건")
    else:
        tags.append("문서수 미검증")

    vol = r.get("search_volume")
    if vol:
        tags.append(f"검색량 {vol:,}회/월")

    if r.get("efficiency_label"):
        eff_txt = r["efficiency_label"]
        if r.get("efficiency_ratio") is not None:
            eff_txt += f" (검색량÷문서수={r['efficiency_ratio']:.2f})"
        tags.append(eff_txt)

    tags.append(f"예상 수익성 {r.get('profit_label', '-')}")

    if r.get("risk"):
        for reason in r.get("risk_reasons", []):
            tags.append(f"[위험] {reason}")

    return tags
