# -*- coding: utf-8 -*-
"""
profit_filter.py (v18.6)
네이버 블로그 수익형 키워드 발굴 시스템 - 수익형 판별 전담 모듈 (역할 축소판)

[역할 - 이 파일이 하는 일만 명확히 한정]
  1) 카테고리 활성화 여부 확인 (profit_categories.json의 active 플래그)
  2) 제외 키워드(정치/연예/스포츠 등) 안전장치 필터링
  3) 검색의도 점수(intent_score) 계산
  4) 카테고리 가중치(category_weight) 및 CPC/경쟁도 메타데이터(category_meta) 부여

[이 파일이 더 이상 하지 않는 일 -> scorer.py로 이동]
  - 연관검색어 확장 (검색량이 확인된 후보만 확장해야 하므로 scorer.py의 책임)
  - 신선도(freshness) 계산 (scorer.py가 뉴스/DataLab을 모두 알고 있으므로 IssueScore 계산 시 함께 처리)
  - 추천 태그(profit_tags/reason_tags) 생성 (모든 실측치가 모이는 scorer.py에서 최종 생성)

[입력] collector.collect_candidates()의 출력
  {keyword, category, anchor, intent_word, mentions, sample_titles,
   seed_query, first_pub_date, latest_pub_date}

[출력 계약] filter_candidates()가 반환하는 리스트의 각 원소(dict)는
  입력 필드를 모두 유지한 채 아래 필드를 추가로 포함한다.

    {
        ... (입력 필드 전부 유지) ...
        "intent_score"    : float   # 0.0~1.0, 검색의도 명확성 점수
        "category_weight" : float   # profit_categories.json에서 부여된 카테고리 가중치
        "category_meta"   : dict    # {"cpc": "high"|"mid"|"low", "competition": "high"|"mid"|"low"}
        "source"          : list[str]  # 이 단계에서는 항상 ["news"] (collector가 뉴스만 수집하므로)
    }

  검색량/문서수/DataLab 조회, 연관검색어 확장, 신선도 계산, 최종 점수·등급 분류는
  전부 scorer.py의 책임이다. 이 파일은 "수익형 카테고리에 속하고 검색의도가 있는가"까지만 판단한다.

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


def _default_config():
    # cpc / competition 메타데이터 추가 (향후 scorer.py의 OpportunityScore에서 활용)
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
            "신청": 1.0, "신청방법": 1.0, "신청기간": 0.9, "신청조건": 0.9, "신청서": 0.8,
            "대상": 0.9, "대상자": 0.9, "조건": 0.9, "자격": 0.9,
            "조회": 0.9, "확인": 0.8, "지급일": 0.9, "지급대상": 0.9,
            "계산": 0.7, "비교": 0.8, "추천": 0.6, "후기": 0.5,
            "방법": 0.8, "준비물": 0.6, "제출서류": 0.7, "서류": 0.6,
            "접수": 0.8, "한도": 0.8, "금액": 0.7, "혜택": 0.6,
            "기간": 0.6, "사용처": 0.7, "환급대상": 0.9, "수령": 0.7,
        },
        "exclude_keywords": [
            "대선", "총선", "국회", "정당", "지지율", "탄핵", "대통령", "여야",
            "아이돌", "드라마", "예능", "배우", "가수", "컴백", "열애",
            "프로야구", "월드컵", "올림픽", "축구", "야구", "농구",
        ],
    }


def load_profit_config(path=None, log=None):
    """profit_categories.json을 로드. 없으면 기본값으로 새로 생성."""
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
    merged["exclude_keywords"] = user_cfg.get("exclude_keywords", default["exclude_keywords"])
    return merged


# =========================================================================
# 1. 검색의도 점수 계산
# =========================================================================
def _compute_intent_score(keyword, intent_word, intent_words_cfg):
    if intent_word and intent_word in intent_words_cfg:
        return intent_words_cfg[intent_word]
    best = 0.0
    for word, weight in intent_words_cfg.items():
        if word in keyword:
            best = max(best, weight)
    return best if best > 0 else 0.3


# =========================================================================
# 2. 제외 키워드 안전장치
# =========================================================================
def _is_excluded(keyword, sample_titles, exclude_keywords):
    text = keyword + " " + " ".join(sample_titles or [])
    return any(ex in text for ex in exclude_keywords)


# =========================================================================
# 3. 메인 인터페이스
# =========================================================================
def filter_candidates(candidates, category_config_path=None, log=None):
    """
    Parameters
    ----------
    candidates : list[dict]
        collector.collect_candidates()의 출력.
    category_config_path : str | None
        profit_categories.json 경로. None이면 실행 파일 옆 기본 경로 사용.
    log : callable | None
        로그 콜백. log(message: str)

    Returns
    -------
    list[dict] : 위 "출력 계약"에 정의된 필드가 추가된 후보 리스트.
    """
    cfg = load_profit_config(category_config_path, log=log)
    category_cfg = cfg["categories"]
    intent_words_cfg = cfg["intent_words"]
    exclude_keywords = cfg["exclude_keywords"]

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
