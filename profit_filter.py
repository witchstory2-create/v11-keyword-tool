# -*- coding: utf-8 -*-
"""
profit_filter.py (v18.7)
네이버 블로그 수익형 키워드 발굴 시스템 - 수익형 판별 전담 모듈 (역할 축소판)

[v18.7 변경 사항 - 수익형 필터 강화]
1) intent_words 기본값에 "환급", "지원", "가입", "금리", "청구"를 추가했다.
   기존에는 이 5개 단어가 목록에 없어 기본값 0.3점만 받아 실제로는 수익형
   의도가 뚜렷한 키워드가 낮은 점수를 받는 문제가 있었다.

2) HIGH_PRIORITY_INTENT_WORDS(신청/조회/조건/대상/계산/환급/지원/가입/비교/
   금리/청구)를 명시하고, 이 의도어가 검출되면 intent_score가 최소 0.85점
   이상이 되도록 _compute_intent_score()에 하한선을 추가했다. 이 11개
   의도어는 명확한 "정보 검색성/신청성" 키워드임이 뚜렷하므로 다른 의도어
   보다 우선적으로 취급한다.

3) exclude_keywords 기본값에 사건사고/국제뉴스/일반 시사성 표현을 추가했다
   (기존에는 정치/연예/스포츠 위주였음).

4) [중요] load_profit_config()의 exclude_keywords 병합 방식을 "통째로 교체"
   에서 "합집합(중복 제거 병합)"으로 변경했다. 기존에는 이미 생성된
   profit_categories.json 파일이 있으면 기본값이 개선되어도 예전 목록으로
   덮여써서 반영되지 않는 문제가 있었다. 이제는 사용자가 커스터마이징한
   제외 키워드를 그대로 유지하면서, 새로 강화된 기본 배제 목록도 함께
   적용된다. intent_words/categories의 병합 방식(부분 업데이트)은 기존과
   동일하게 유지했다.

[역할 - 이 파일이 하는 일만 명확히 한정] (v18.6과 동일)
  1) 카테고리 활성화 여부 확인 (profit_categories.json의 active 플래그)
  2) 제외 키워드(정치/연예/스포츠/사건사고/국제뉴스/일반시사) 안전장치 필터링
  3) 검색의도 점수(intent_score) 계산 - 11개 핵심 의도어 우선 처리
  4) 카테고리 가중치(category_weight) 및 CPC/경쟁도 메타데이터(category_meta) 부여

[이 파일이 더 이상 하지 않는 일 -> scorer.py로 이동] (v18.6과 동일)
  - 연관검색어 확장 / 신선도 계산 / 추천 태그 생성

[출력 계약] filter_candidates()의 반환 필드는 v18.6과 완전히 동일하다 (변경 없음).

표준 라이브러리만 사용 (json, os, sys) -> PyInstaller / GitHub Actions 빌드 100% 호환.
"""

import os
import sys
import json


# =========================================================================
# 0. 경로 / 설정 파일 처리
# =========================================================================
def _base_dir():
    return os.path.dirname(os.path.abspath(sys.argv[0]))


CONFIG_FILENAME = "profit_categories.json"

# [v18.7 신규] 명확한 수익형 검색의도 11종 - 이 의도어는 다른 의도어보다
# 우선적으로 높은 intent_score를 받는다 (최소 0.85점 보장).
HIGH_PRIORITY_INTENT_WORDS = {
    "신청", "조회", "조건", "대상", "계산", "환급", "지원", "가입", "비교", "금리", "청구",
}
HIGH_PRIORITY_MIN_SCORE = 0.85


def _default_config():
    return {
        "categories": {
            "지원금": {"weight": 1.5, "active": True, "cpc": "high", "competition": "mid",
                       "anchors": ["지원금", "지원비", "바우처", "생계비"],
                       "seeds": ["민생지원금", "에너지바우처", "청년지원금", "긴급지원금", "생계지원금"]},
            "환급": {"weight": 1.5, "active": True, "cpc": "high", "competition": "mid",
                     "anchors": ["환급", "환급금", "환급액"],
                     "seeds": ["세금환급", "보험료환급", "건강보험 환급금", "국민연금 환급"]},
            "보험": {"weight": 1.3, "active": True, "cpc": "high", "competition": "high",
                     "anchors": ["보험", "보험료", "보험금"],
                     "seeds": ["실손보험", "치아보험", "암보험", "보험 리모델링"]},
            "자동차보험": {"weight": 1.3, "active": True, "cpc": "high", "competition": "high",
                          "anchors": ["자동차보험", "차보험"],
                          "seeds": ["자동차보험 갱신", "자동차보험 비교", "다이렉트 자동차보험"]},
            "건강보험": {"weight": 1.2, "active": True, "cpc": "mid", "competition": "mid",
                        "anchors": ["건강보험", "건보"],
                        "seeds": ["건강보험료", "건강보험 피부양자", "건강보험 지역가입자"]},
            "대출": {"weight": 1.4, "active": True, "cpc": "high", "competition": "high",
                     "anchors": ["대출", "대환대출", "신용대출"],
                     "seeds": ["대환대출", "정책자금 대출", "전세대출", "햇살론"]},
            "연금": {"weight": 1.2, "active": True, "cpc": "mid", "competition": "mid",
                     "anchors": ["연금", "국민연금", "퇴직연금", "irp"],
                     "seeds": ["국민연금 조기수령", "irp 세액공제", "주택연금"]},
            "세금": {"weight": 1.1, "active": True, "cpc": "mid", "competition": "mid",
                     "anchors": ["세금", "세액공제", "종부세", "양도세"],
                     "seeds": ["연말정산 세액공제", "종합소득세", "양도소득세"]},
            "청약": {"weight": 1.0, "active": True, "cpc": "low", "competition": "mid",
                     "anchors": ["청약", "청약통장", "특별공급"],
                     "seeds": ["청약통장 조건", "특별공급 자격", "생애최초 청약"]},
            "부동산": {"weight": 0.9, "active": True, "cpc": "mid", "competition": "high",
                       "anchors": ["부동산", "재산세", "취득세"],
                       "seeds": ["재산세 조회", "취득세 감면"]},
            "카드": {"weight": 0.9, "active": True, "cpc": "low", "competition": "high",
                     "anchors": ["카드", "카드혜택", "체크카드"],
                     "seeds": ["카드 캐시백", "체크카드 혜택"]},
        },
        "intent_words": {
            # [v18.7] 11개 핵심 의도어 - 신청/조회/조건/대상/계산/환급/지원/가입/비교/금리/청구
            "신청": 1.0, "신청방법": 1.0, "신청기간": 0.9, "신청조건": 0.9, "신청서": 0.8,
            "대상": 0.9, "대상자": 0.9, "조건": 0.9, "자격": 0.9,
            "조회": 0.9, "확인": 0.8, "지급일": 0.9, "지급대상": 0.9,
            "계산": 0.85, "비교": 0.85, "추천": 0.6, "후기": 0.5,
            "방법": 0.8, "준비물": 0.6, "제출서류": 0.7, "서류": 0.6,
            "접수": 0.8, "한도": 0.8, "금액": 0.7, "혜택": 0.6,
            "기간": 0.6, "사용처": 0.7, "환급대상": 0.9, "수령": 0.7,
            # [v18.7 신규 추가]
            "환급": 0.9, "지원": 0.8, "가입": 0.75, "금리": 0.85, "청구": 0.85,
        },
        "exclude_keywords": [
            # 정치
            "대선", "총선", "국회", "정당", "지지율", "탄핵", "대통령", "여야",
            "여당", "야당", "국정감사", "특검", "개헌", "당대표", "원내대표",
            # 연예
            "아이돌", "드라마", "예능", "배우", "가수", "컴백", "열애",
            "콘서트", "스캔들", "팬미팅", "걸그룹", "보이그룹",
            # 스포츠
            "프로야구", "월드컵", "올림픽", "축구", "야구", "농구", "배구",
            "국가대표", "챔피언스리그",
            # [v18.7 신규] 사건사고
            "사망", "숨진채", "화재", "폭발사고", "살인사건", "실종자",
            "긴급체포", "구속영장", "검찰조사", "성폭행", "강도", "방화",
            # [v18.7 신규] 국제뉴스
            "외신", "특파원", "유엔", "나토", "국제사회", "정상회담", "순방",
            "주한미군", "다자외교",
            # [v18.7 신규] 일반 시사
            "논란", "파문", "일파만파", "긴급브리핑", "특별담화", "성명발표",
            "여론조사", "지지율조사",
        ],
    }


def load_profit_config(path=None, log=None):
    """
    profit_categories.json을 로드. 없으면 기본값으로 새로 생성.

    [v18.7 변경] exclude_keywords 병합 방식을 "통째로 교체"에서
    "합집합(중복 제거)"으로 변경. categories/intent_words는 기존과 동일하게
    부분 업데이트(딕셔너리 update) 방식을 유지한다.
    """
    path = path or os.path.join(_base_dir(), CONFIG_FILENAME)
    default = _default_config()

    if not os.path.exists(path):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(default, f, ensure_ascii=False, indent=2)
            if log:
                log(f"[profit_filter] 설정 파일이 없어 기본값으로 생성했습니다: {path}")
        except Exception as e:
            if log:
                log(f"[profit_filter] 설정 파일 생성 실패({e}), 기본값을 메모리에서만 사용합니다.")
        return default

    try:
        with open(path, "r", encoding="utf-8") as f:
            user_cfg = json.load(f)
    except Exception as e:
        if log:
            log(f"[profit_filter] 설정 파일 읽기 실패({e}), 기본값을 사용합니다.")
        return default

    merged = dict(default)
    merged["categories"] = dict(default["categories"])
    merged["categories"].update(user_cfg.get("categories", {}))
    merged["intent_words"] = dict(default["intent_words"])
    merged["intent_words"].update(user_cfg.get("intent_words", {}))

    # [v18.7] exclude_keywords는 교체 대신 합집합으로 병합.
    # 사용자가 파일에서 직접 삭제한 항목까지 되살리지는 않되(합집합이므로
    # 기존 파일에 없는 새 강화 항목은 자동으로 추가되고, 사용자가 파일에
    # 추가해둔 커스텀 제외 키워드도 그대로 유지된다.
    user_exclude = user_cfg.get("exclude_keywords", [])
    merged_exclude = list(default["exclude_keywords"])
    for kw in user_exclude:
        if kw not in merged_exclude:
            merged_exclude.append(kw)
    merged["exclude_keywords"] = merged_exclude

    return merged


# =========================================================================
# 1. 검색의도 점수 계산
# =========================================================================
def _compute_intent_score(keyword, intent_word, intent_words_cfg):
    """
    [v18.7] HIGH_PRIORITY_INTENT_WORDS(신청/조회/조건/대상/계산/환급/지원/가입/
    비교/금리/청구)가 검출되면 최소 HIGH_PRIORITY_MIN_SCORE(0.85)를 보장한다.
    나머지 로직(intent_word 우선 확인 -> keyword 내 포함 여부 스캔 -> 기본값 0.3)은
    v18.6과 동일하다.
    """
    score = 0.0
    matched_high_priority = False

    if intent_word and intent_word in intent_words_cfg:
        score = intent_words_cfg[intent_word]
        if intent_word in HIGH_PRIORITY_INTENT_WORDS:
            matched_high_priority = True
    else:
        best = 0.0
        for word, weight in intent_words_cfg.items():
            if word in keyword:
                best = max(best, weight)
                if word in HIGH_PRIORITY_INTENT_WORDS:
                    matched_high_priority = True
        score = best if best > 0 else 0.3

    # keyword 자체에 핵심 의도어가 직접 포함되어 있는 경우도 추가로 확인
    # (intent_word 필드가 None이어도 keyword 문자열 안에 있을 수 있음)
    if not matched_high_priority:
        for hp_word in HIGH_PRIORITY_INTENT_WORDS:
            if hp_word in keyword:
                matched_high_priority = True
                break

    if matched_high_priority:
        score = max(score, HIGH_PRIORITY_MIN_SCORE)

    return score


# =========================================================================
# 2. 제외 키워드 안전장치 (v18.6과 동일)
# =========================================================================
def _is_excluded(keyword, sample_titles, exclude_keywords):
    text = keyword + " " + " ".join(sample_titles or [])
    return any(ex in text for ex in exclude_keywords)


# =========================================================================
# 3. 메인 인터페이스
# =========================================================================
def filter_candidates(candidates, category_config_path=None, log=None):
    """
    [v18.7] 함수 시그니처와 반환 필드는 v18.6과 완전히 동일하다.
    로그에 입력 후보 수를 추가로 남겨 파이프라인 단계별 추적이 쉬워졌다.
    """
    cfg = load_profit_config(category_config_path, log=log)
    category_cfg = cfg["categories"]
    intent_words_cfg = cfg["intent_words"]
    exclude_keywords = cfg["exclude_keywords"]

    if log:
        log(f"[profit_filter] 입력 후보 {len(candidates)}건, 수익형 판별 시작 "
            f"(제외 키워드 {len(exclude_keywords)}종)")

    result = []
    dropped_inactive, dropped_excluded = 0, 0

    for c in candidates:
        cat_conf = category_cfg.get(c["category"])
        if not cat_conf or not cat_conf.get("active", True):
            dropped_inactive += 1
            continue
        if _is_excluded(c["keyword"], c.get("sample_titles"), exclude_keywords):
            dropped_excluded += 1
            continue

        intent_score = _compute_intent_score(c["keyword"], c.get("intent_word"), intent_words_cfg)
        out = dict(c)
        out["intent_score"] = round(intent_score, 2)
        out["category_weight"] = cat_conf.get("weight", 1.0)
        out["category_meta"] = {
            "cpc": cat_conf.get("cpc", "mid"),
            "competition": cat_conf.get("competition", "mid"),
        }
        out["source"] = ["news"]
        result.append(out)

    if log:
        log(f"[profit_filter] 통과 {len(result)}건 "
            f"(비활성 카테고리 제외 {dropped_inactive}건, 제외키워드 매치 {dropped_excluded}건)")

    return result


if __name__ == "__main__":
    def _print_log(msg):
        print(msg)

    dummy = [
        {"keyword": "민생지원금", "category": "지원금", "anchor": "지원금",
         "intent_word": None, "mentions": 12, "sample_titles": ["민생지원금 지급 시작"],
         "seed_query": "민생지원금", "first_pub_date": "2026-06-28", "latest_pub_date": "2026-06-30"},
    ]
    for r in filter_candidates(dummy, log=_print_log):
        print(r)
