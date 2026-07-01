# -*- coding: utf-8 -*-
"""
collector.py (v18.5)
네이버 블로그 수익형 키워드 발굴 시스템 - 후보 수집/정제 전담 모듈

[역할]
  - 무작위 뉴스 수집이 아니라, 수익형 카테고리별 시드 쿼리로만 네이버 뉴스를 조회.
  - HTML 엔티티, 언론사명, 날짜, 지역명, 인물 호칭 등 일반 뉴스 노이즈를 원천 차단.
  - 카테고리 앵커(anchor) + 검색의도어(intent word) 조합으로 후보 키워드를 생성.
  - 수익성 판단(profit_filter)이나 점수화(scorer)는 이 파일의 책임이 아님.
    여기서는 "깨끗하고 카테고리가 명확한 후보"만 만들어서 넘긴다.

[출력 계약] collect_candidates()가 반환하는 리스트의 각 원소(dict)는
  아래 필드를 반드시 포함한다. 이후 모든 다운스트림 모듈은 이 필드명을 그대로 사용한다.

    {
        "keyword"       : str   # 후보 키워드 (예: "민생지원금", "자동차보험 갱신")
        "category"      : str   # 소속 수익형 카테고리 (예: "지원금")
        "anchor"        : str   # 이 후보가 매칭된 카테고리 앵커 원형 (예: "지원금")
        "intent_word"   : str|None  # 함께 검출된 검색의도어 (예: "신청방법"), 없으면 None
        "mentions"      : int   # 이 키워드가 등장한 서로 다른 뉴스 기사 수
        "sample_titles" : list[str]  # 근거가 된 원문 기사 제목 샘플 (최대 5개, 정제 후)
        "seed_query"    : str   # 이 후보를 발견한 시드 검색어
        "first_pub_date": str   # 최초 발견 기사 발행일 (YYYY-MM-DD, 알 수 없으면 "")
        "latest_pub_date": str  # 최근 발견 기사 발행일 (YYYY-MM-DD, 알 수 없으면 "")
    }

  이 필드 외의 값(검색량, 문서수, DataLab, 점수, 등급 등)은 여기서 절대 만들지 않는다.
  실측치 조회와 판단은 profit_filter.py / scorer.py의 책임이다.

표준 라이브러리만 사용 (urllib, json, re, html, time, datetime, concurrent.futures)
-> PyInstaller / GitHub Actions 빌드 100% 호환. 외부 pip 패키지 없음.
"""

import re
import html
import time
import random
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed


# =========================================================================
# 1. 수익형 카테고리 시드 & 앵커 정의
# =========================================================================
# anchors : 이 카테고리에 속한다고 판단할 "핵심 원형 단어" 목록.
#           후보 토큰이 이 anchor를 포함하고 있어야 후보로 채택된다.
# seeds   : 실제 네이버 뉴스 API에 보낼 검색어. anchor보다 구체적으로 잡아서
#           카테고리 무관 뉴스가 섞여 들어올 확률을 낮춘다.
CATEGORY_SEEDS = {
    "지원금": {
        "anchors": ["지원금", "지원비", "생계비", "생계지원"],
        "seeds": ["민생지원금", "생계지원금", "긴급지원금", "지원금 신청",
                   "지원금 대상", "정부지원금", "지자체 지원금"],
    },
    "환급": {
        "anchors": ["환급", "환급금", "환급액"],
        "seeds": ["세금환급", "보험료환급", "환급금 신청", "건강보험 환급금",
                   "국민연금 환급", "자동차보험 환급", "환급 대상 조회"],
    },
    "보험": {
        "anchors": ["보험", "보험료", "보험금"],
        "seeds": ["실손보험", "보험료 인상", "보험금 청구", "보험 리모델링",
                   "치아보험", "암보험"],
    },
    "자동차보험": {
        "anchors": ["자동차보험", "car보험", "차보험"],
        "seeds": ["자동차보험 갱신", "자동차보험 비교", "자동차보험료",
                   "다이렉트 자동차보험", "자동차보험 할인"],
    },
    "건강보험": {
        "anchors": ["건강보험", "건보"],
        "seeds": ["건강보험료", "건강보험 환급금", "건강보험 피부양자",
                   "건강보험 지역가입자", "건강보험 본인부담"],
    },
    "대출": {
        "anchors": ["대출", "대환대출", "신용대출"],
        "seeds": ["대환대출", "신용대출 조건", "정책자금 대출", "전세대출",
                   "햇살론", "무직자 대출", "대출 한도"],
    },
    "연금": {
        "anchors": ["연금", "국민연금", "퇴직연금", "irp"],
        "seeds": ["국민연금 조기수령", "퇴직연금 irp", "irp 세액공제",
                   "연금 수령 나이", "주택연금"],
    },
    "세금": {
        "anchors": ["세금", "세액공제", "종부세", "양도세"],
        "seeds": ["종합소득세", "연말정산 세액공제", "양도소득세",
                   "종부세 대상", "부가세 신고"],
    },
    "청약": {
        "anchors": ["청약", "청약통장", "특별공급"],
        "seeds": ["청약통장 조건", "특별공급 자격", "청약 가점제",
                   "신혼부부 특별공급", "생애최초 청약"],
    },
    "부동산": {
        "anchors": ["부동산", "재산세", "취득세"],
        "seeds": ["재산세 조회", "취득세 감면", "부동산 규제지역",
                   "전월세 신고제"],
    },
    "카드": {
        "anchors": ["카드", "카드혜택", "체크카드"],
        "seeds": ["카드 캐시백", "카드 연회비", "체크카드 혜택",
                   "카드 포인트 소멸"],
    },
}

# 검색의도어: 후보 키워드가 "정보 검색성"임을 뒷받침하는 접미어.
# anchor 토큰 옆에 이 단어가 붙어 있으면 별도 bigram 후보로도 채택한다.
INTENT_WORDS = [
    "신청", "신청방법", "신청기간", "신청조건", "대상", "대상자", "조건",
    "자격", "방법", "금액", "한도", "지급일", "지급대상", "확인", "조회",
    "서류", "접수", "기간", "혜택", "환급대상", "수령", "신청서",
]

# =========================================================================
# 2. 노이즈 블랙리스트
# =========================================================================
PRESS_NAMES = [
    "연합뉴스", "조선일보", "중앙일보", "동아일보", "한겨레", "경향신문",
    "매일경제", "한국경제", "서울신문", "국민일보", "세계일보", "문화일보",
    "아시아경제", "머니투데이", "이데일리", "파이낸셜뉴스", "뉴시스",
    "뉴스1", "노컷뉴스", "헤럴드경제", "데일리안", "뉴스핌", "전자신문",
    "디지털타임스", "ZDNet", "지디넷", "블로터", "MBC", "KBS", "SBS",
    "YTN", "JTBC", "채널A", "TV조선", "MBN", "OSEN", "스포츠동아",
    "스타뉴스", "톱스타뉴스", "일간스포츠", "매경이코노미", "시사저널",
    "뉴스토마토", "프레시안", "오마이뉴스", "위키트리", "인사이트",
    "쿠키뉴스", "메디컬투데이", "청년일보", "브릿지경제", "money",
]

LOCATION_STOPWORDS = [
    "서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종",
    "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주",
    "강릉", "수원", "성남", "고양", "용인", "청주", "전주", "포항", "창원",
    "천안", "안산", "안양", "김해", "구미", "춘천", "원주", "여수", "순천",
    "목포", "제천", "충주", "동해", "속초", "삼척",
]

DATE_STOPWORDS = [
    "오늘", "내일", "어제", "모레", "이번주", "다음주", "지난주",
    "월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일",
    "상반기", "하반기", "1분기", "2분기", "3분기", "4분기",
]

# 뉴스 편집/형식 관련 상용어 (기사 제목에서 흔히 붙는 라벨성 단어)
GENERIC_NEWS_WORDS = [
    "속보", "단독", "종합", "영상", "포토", "인터뷰", "사설", "칼럼",
    "오피니언", "이슈", "화제", "리포트", "특보", "브리핑", "논평",
    "기자수첩", "사진", "그래픽",
]

# HTML 엔티티 잔재/영어 잡음
ENGLISH_STOPWORDS = [
    "and", "the", "of", "quot", "amp", "nbsp", "com", "co", "kr",
    "http", "https", "www", "news", "article", "html",
]

# 인물 호칭 접미어 -> 이 접미어가 붙은 토큰은 인물 언급으로 간주해 제거
PERSON_SUFFIXES = ["씨", "대표", "의원", "시장", "지사", "총리", "장관",
                    "청장", "국장", "회장", "위원장", "교수", "박사"]

# 조사/어미 등 흔한 한국어 접미(단순 규칙 기반 어절 정리용)
PARTICLE_SUFFIXES = [
    "으로부터", "에서부터", "까지도", "이라도", "에서는", "에게서",
    "으로는", "부터는", "에서", "으로", "에게", "까지", "부터", "이라",
    "라는", "이나", "보다", "처럼", "만큼", "이라는",
    "이는", "은는", "이에", "에는", "이랑", "이며", "하고",
    "은", "는", "이", "가", "을", "를", "의", "에", "도", "만", "과", "와", "로",
]

BRACKET_PATTERN = re.compile(r"[\[\(【〔《][^\]\)】〕》]*[\]\)】〕》]")
DATE_NUMERIC_PATTERN = re.compile(
    r"\d{4}년|\d{1,2}월\s?\d{1,2}일|\d{1,2}\.\d{1,2}\.?|\d{1,2}/\d{1,2}|\d{4}\.\d{1,2}\.\d{1,2}"
)
PRESS_SUFFIX_PATTERN = re.compile(
    r"\s*[-–—|·]\s*(" + "|".join(re.escape(p) for p in PRESS_NAMES) + r")\s*$"
)
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
NON_KOR_ENG_NUM_PATTERN = re.compile(r"[^가-힣a-zA-Z0-9%\s]")
PURE_NUMBER_PATTERN = re.compile(r"^\d+[%원]?$")


# =========================================================================
# 3. 텍스트 정제 함수
# =========================================================================
def _clean_title(raw_title):
    """HTML 엔티티/태그 제거, 언론사 접미사 제거, 날짜/괄호 표현 제거."""
    if not raw_title:
        return ""
    text = html.unescape(raw_title)
    text = HTML_TAG_PATTERN.sub("", text)
    text = BRACKET_PATTERN.sub(" ", text)
    text = DATE_NUMERIC_PATTERN.sub(" ", text)
    text = PRESS_SUFFIX_PATTERN.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _strip_particle(word):
    """어절 끝의 조사를 단순 규칙으로 제거. 제거 후 길이가 너무 짧아지면 원형 유지."""
    for suf in PARTICLE_SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 2:
            return word[: -len(suf)]
    return word


def _is_person_name_token(token):
    for suf in PERSON_SUFFIXES:
        if token.endswith(suf) and len(token) - len(suf) <= 4:
            return True
    return False


def _is_meaningless_token(token):
    """노이즈 토큰 여부를 다층 블랙리스트와 패턴으로 판별."""
    if not token or len(token) < 2:
        return True
    low = token.lower()
    if low in ENGLISH_STOPWORDS:
        return True
    if token in PRESS_NAMES or token in LOCATION_STOPWORDS:
        return True
    if token in DATE_STOPWORDS or token in GENERIC_NEWS_WORDS:
        return True
    if _is_person_name_token(token):
        return True
    if PURE_NUMBER_PATTERN.match(token):
        return True
    if re.fullmatch(r"[a-zA-Z]+", token) and len(token) <= 3:
        # 짧은 영어 잔재어(and, is, to 등) 차단
        return True
    if NON_KOR_ENG_NUM_PATTERN.search(token):
        # 특수문자/이모지 등이 남아있는 경우
        return True
    return False


def _tokenize(cleaned_title):
    """정제된 제목을 어절 단위로 분리하고 조사를 제거한 뒤 노이즈 토큰을 제거."""
    raw_words = cleaned_title.split(" ")
    tokens = []
    for w in raw_words:
        w = w.strip()
        if not w:
            continue
        w = _strip_particle(w)
        if _is_meaningless_token(w):
            continue
        tokens.append(w)
    return tokens


# =========================================================================
# 4. 후보 키워드 추출 (앵커 + 검색의도어 조합)
# =========================================================================
def _extract_candidates_from_tokens(tokens, category, anchors):
    """
    토큰 리스트에서 카테고리 앵커를 포함한 후보를 뽑는다.
      1) 앵커를 포함하는 단일 토큰 자체가 이미 복합어인 경우 (예: "민생지원금") -> 단독 후보.
      2) 앵커 토큰과 인접한 검색의도어가 있는 경우 -> "앵커어 의도어" bigram 후보 추가.
    """
    candidates = []
    n = len(tokens)
    for i, tok in enumerate(tokens):
        matched_anchor = next((a for a in anchors if a in tok), None)
        if not matched_anchor:
            continue

        # (1) 단독 복합어 후보
        candidates.append({"keyword": tok, "anchor": matched_anchor, "intent_word": None})

        # (2) 인접 토큰이 검색의도어인 경우 bigram 후보
        for j in (i - 1, i + 1):
            if 0 <= j < n:
                neighbor = tokens[j]
                if neighbor in INTENT_WORDS:
                    if j < i:
                        phrase = f"{neighbor} {tok}"
                    else:
                        phrase = f"{tok} {neighbor}"
                    candidates.append({
                        "keyword": phrase,
                        "anchor": matched_anchor,
                        "intent_word": neighbor,
                    })
    return candidates


def _parse_pub_date(raw_pub_date):
    """네이버 뉴스 API의 pubDate(RFC822 유사 포맷)를 YYYY-MM-DD로 변환. 실패 시 빈 문자열."""
    if not raw_pub_date:
        return ""
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            dt = datetime.strptime(raw_pub_date, fmt)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            continue
    return ""


# =========================================================================
# 5. 뉴스 조회 (카테고리 시드 단위)
# =========================================================================
def _fetch_seed_news(search_api, seed_query, pages=2, display=100, log=None):
    """
    하나의 시드 쿼리에 대해 네이버 뉴스 API를 조회.
    search_api는 naver_search_api.NaverSearchAPI 인스턴스를 가정하며,
    search_news(query, display, start, sort) -> list[dict(title, description, pubDate, link)]
    형태의 메서드를 제공한다고 가정한다.
    """
    items = []
    for page in range(pages):
        start = page * display + 1
        try:
            result = search_api.search_news(seed_query, display=display, start=start, sort="date")
            if not result:
                break
            items.extend(result)
            if len(result) < display:
                break
        except Exception as e:
            if log:
                log(f"[collector] 뉴스 조회 실패 (seed='{seed_query}', page={page}): {e}")
            break
        time.sleep(0.15 + random.random() * 0.15)  # API 과호출 방지용 소폭 지연
    return items


def _process_seed(search_api, category, seed_query, anchors, log=None):
    """시드 쿼리 하나를 처리하여 (category, seed_query, 후보 리스트, 원문기사 메타)를 반환."""
    news_items = _fetch_seed_news(search_api, seed_query, log=log)
    local_agg = {}  # keyword -> {"mentions":set(article_id), "sample_titles":[], "anchor", "intent_word", pub dates}

    for item in news_items:
        raw_title = item.get("title", "")
        raw_desc = item.get("description", "")
        pub_date = _parse_pub_date(item.get("pubDate", ""))
        link = item.get("link") or item.get("originallink") or raw_title

        cleaned_title = _clean_title(raw_title)
        cleaned_desc = _clean_title(raw_desc)
        combined_text = f"{cleaned_title} {cleaned_desc}".strip()
        if not combined_text:
            continue

        tokens = _tokenize(combined_text)
        if not tokens:
            continue

        found = _extract_candidates_from_tokens(tokens, category, anchors)
        for cand in found:
            key = cand["keyword"]
            if key not in local_agg:
                local_agg[key] = {
                    "keyword": key,
                    "category": category,
                    "anchor": cand["anchor"],
                    "intent_word": cand["intent_word"],
                    "mentions_set": set(),
                    "sample_titles": [],
                    "seed_query": seed_query,
                    "first_pub_date": pub_date,
                    "latest_pub_date": pub_date,
                }
            entry = local_agg[key]
            entry["mentions_set"].add(link)
            if cleaned_title and cleaned_title not in entry["sample_titles"] and len(entry["sample_titles"]) < 5:
                entry["sample_titles"].append(cleaned_title)
            # intent_word가 있는 후보를 우선(더 구체적인 정보이므로) 갱신
            if cand["intent_word"] and not entry["intent_word"]:
                entry["intent_word"] = cand["intent_word"]
            if pub_date:
                if not entry["first_pub_date"] or pub_date < entry["first_pub_date"]:
                    entry["first_pub_date"] = pub_date
                if not entry["latest_pub_date"] or pub_date > entry["latest_pub_date"]:
                    entry["latest_pub_date"] = pub_date

    return local_agg


# =========================================================================
# 6. 메인 인터페이스
# =========================================================================
def collect_candidates(search_api, discovery_target=None, light_filter_target=None,
                        log=None, max_workers=4, max_per_category=60):
    """
    수익형 카테고리 뉴스를 수집하고, 노이즈를 제거한 후보 키워드 리스트를 반환한다.

    Parameters
    ----------
    search_api : naver_search_api.NaverSearchAPI
        뉴스 검색을 수행할 API 클라이언트. search_news(query, display, start, sort) 필요.
    discovery_target : list[str] | None
        수집 대상 카테고리 이름 목록. None이면 CATEGORY_SEEDS의 전체 카테고리를 사용.
    light_filter_target : list[str] | None
        추가로 포함하고 싶은 보조 시드 키워드 목록(선택). 각 카테고리에 매칭되는 항목만 반영.
    log : callable | None
        로그 출력 콜백. log(message:str) 형태.
    max_workers : int
        시드 쿼리 병렬 조회 스레드 수.
    max_per_category : int
        카테고리별로 반환할 최대 후보 수(멘션 수 기준 상위 N개). 다운스트림 부담 완화용.

    Returns
    -------
    list[dict] : 위 "출력 계약"에 정의된 필드를 가진 후보 딕셔너리 리스트.
    """
    categories = discovery_target if discovery_target else list(CATEGORY_SEEDS.keys())
    categories = [c for c in categories if c in CATEGORY_SEEDS]
    if not categories:
        if log:
            log("[collector] 유효한 카테고리가 없어 전체 카테고리로 진행합니다.")
        categories = list(CATEGORY_SEEDS.keys())

    # 보조 시드 키워드 병합(선택)
    extra_by_category = {}
    if light_filter_target:
        for cat in categories:
            anchors = CATEGORY_SEEDS[cat]["anchors"]
            matched = [kw for kw in light_filter_target if any(a in kw for a in anchors)]
            if matched:
                extra_by_category[cat] = matched

    tasks = []  # (category, seed_query, anchors)
    for cat in categories:
        anchors = CATEGORY_SEEDS[cat]["anchors"]
        seeds = list(CATEGORY_SEEDS[cat]["seeds"]) + extra_by_category.get(cat, [])
        for seed in seeds:
            tasks.append((cat, seed, anchors))

    if log:
        log(f"[collector] 수집 대상 카테고리: {', '.join(categories)} (시드 쿼리 {len(tasks)}건)")

    global_agg = {}  # (category, keyword) -> aggregated entry

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(_process_seed, search_api, cat, seed, anchors, log): (cat, seed)
            for cat, seed, anchors in tasks
        }
        for future in as_completed(future_map):
            cat, seed = future_map[future]
            try:
                local_agg = future.result()
            except Exception as e:
                if log:
                    log(f"[collector] 시드 처리 실패 (category='{cat}', seed='{seed}'): {e}")
                continue

            for key, entry in local_agg.items():
                gkey = (cat, key)
                if gkey not in global_agg:
                    global_agg[gkey] = entry
                else:
                    g = global_agg[gkey]
                    g["mentions_set"] |= entry["mentions_set"]
                    for t in entry["sample_titles"]:
                        if t not in g["sample_titles"] and len(g["sample_titles"]) < 5:
                            g["sample_titles"].append(t)
                    if entry["intent_word"] and not g["intent_word"]:
                        g["intent_word"] = entry["intent_word"]
                    if entry["first_pub_date"] and (
                        not g["first_pub_date"] or entry["first_pub_date"] < g["first_pub_date"]
                    ):
                        g["first_pub_date"] = entry["first_pub_date"]
                    if entry["latest_pub_date"] and (
                        not g["latest_pub_date"] or entry["latest_pub_date"] > g["latest_pub_date"]
                    ):
                        g["latest_pub_date"] = entry["latest_pub_date"]

            if log:
                log(f"[collector] 완료: category='{cat}', seed='{seed}', 신규 후보 {len(local_agg)}건")

    # 카테고리별 상위 max_per_category개로 컷하고 최종 출력 계약 형태로 변환
    by_category = {}
    for (cat, key), entry in global_agg.items():
        by_category.setdefault(cat, []).append(entry)

    result = []
    for cat, entries in by_category.items():
        entries.sort(key=lambda e: len(e["mentions_set"]), reverse=True)
        for entry in entries[:max_per_category]:
            result.append({
                "keyword": entry["keyword"],
                "category": entry["category"],
                "anchor": entry["anchor"],
                "intent_word": entry["intent_word"],
                "mentions": len(entry["mentions_set"]),
                "sample_titles": entry["sample_titles"],
                "seed_query": entry["seed_query"],
                "first_pub_date": entry["first_pub_date"],
                "latest_pub_date": entry["latest_pub_date"],
            })

    if log:
        log(f"[collector] 최종 후보 수: {len(result)}건 (카테고리 {len(by_category)}개)")

    return result


if __name__ == "__main__":
    # 간단한 단독 실행 테스트(실제 API 키 없이는 동작하지 않음. 구조 확인용).
    class _DummyAPI:
        def search_news(self, query, display=100, start=1, sort="date"):
            return []

    def _print_log(msg):
        print(msg)

    res = collect_candidates(_DummyAPI(), discovery_target=["지원금"], log=_print_log)
    print(f"결과 {len(res)}건")
