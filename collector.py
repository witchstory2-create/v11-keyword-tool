# -*- coding: utf-8 -*-
"""
collector.py (v18.7)
네이버 블로그 수익형 키워드 발굴 시스템 - 후보 수집/정제 전담 모듈

[v18.7 변경 사항 - articles 필드 추가]
후보 dict에 articles=[{title, url, snippet, date}, ...] 필드를 추가하여
기사 원문 메타데이터를 파이프라인 전체(profit_filter -> scorer -> app)로
전달한다. URL 기준 중복 제거, 후보당 최대 5건 보존. 기존 필드는 전혀
제거/변경하지 않았다(순수 additive patch).

[v18.6 변경 사항 - 정치/국제/연예/스포츠/학교 맥락 오프토픽 기사 제외]
기사 단위로 OFFTOPIC_CONTEXT_WORDS가 함께 등장하면 그 기사에서는 후보를
전혀 추출하지 않는다. 정상 처리 로직, 앵커/의도어 정의, 병렬 처리 구조는
전혀 변경하지 않았다.

[출력 계약] collect_candidates()가 반환하는 리스트의 각 원소(dict)는
아래 필드를 포함한다.

    {
        "keyword"        : str
        "category"       : str
        "anchor"         : str
        "intent_word"    : str|None
        "mentions"       : int
        "sample_titles"  : list[str]
        "articles"       : list[{"title","url","snippet","date"}]  # v18.7 신규
        "seed_query"     : str
        "first_pub_date" : str
        "latest_pub_date": str
    }

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
# [v18.7] 진단 로그 헬퍼 - log 콜백 우선 사용, 없으면 print 폴백
# =========================================================================
def _diag_log(msg, log=None, log_callback=None):
    fn = log or log_callback
    if callable(fn):
        try:
            fn(msg)
            return
        except Exception:
            pass
    print(msg)


# =========================================================================
# 1. 수익형 카테고리 시드 & 앵커 정의 (v18.5와 완전히 동일 - 변경 없음)
# =========================================================================
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

INTENT_WORDS = [
    "신청", "신청방법", "신청기간", "신청조건", "대상", "대상자", "조건",
    "자격", "방법", "금액", "한도", "지급일", "지급대상", "확인", "조회",
    "서류", "접수", "기간", "혜택", "환급대상", "수령", "신청서",
]

# =========================================================================
# 2. 노이즈 블랙리스트 (v18.5와 동일)
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

GENERIC_NEWS_WORDS = [
    "속보", "단독", "종합", "영상", "포토", "인터뷰", "사설", "칼럼",
    "오피니언", "이슈", "화제", "리포트", "특보", "브리핑", "논평",
    "기자수첩", "사진", "그래픽",
]

ENGLISH_STOPWORDS = [
    "and", "the", "of", "quot", "amp", "nbsp", "com", "co", "kr",
    "http", "https", "www", "news", "article", "html",
]

PERSON_SUFFIXES = ["씨", "대표", "의원", "시장", "지사", "총리", "장관",
                    "청장", "국장", "회장", "위원장", "교수", "박사"]

PARTICLE_SUFFIXES = [
    "으로부터", "에서부터", "까지도", "이라도", "에서는", "에게서",
    "으로는", "부터는", "에서", "으로", "에게", "까지", "부터", "이라",
    "라는", "이나", "보다", "처럼", "만큼", "이라는",
    "이는", "은는", "이에", "에는", "이랑", "이며", "하고",
    "은", "는", "이", "가", "을", "를", "의", "에", "도", "만", "과", "와", "로",
]

# =========================================================================
# [v18.6] 정치/국제/연예/스포츠/학교 맥락어
# =========================================================================
OFFTOPIC_CONTEXT_WORDS = {
    "정치": [
        "정치", "국회", "여당", "야당", "정당", "총선", "대선", "국정감사",
        "탄핵", "청와대", "정상회담", "국무총리", "대통령실", "당대표",
        "원내대표", "정국", "여야", "국정운영", "개헌", "특검",
    ],
    "국제": [
        "국제", "외신", "특파원", "유엔", "나토", "정상회담", "순방",
        "외교부", "주한미군", "국제사회", "다자외교", "국제기구",
    ],
    "연예": [
        "연예", "배우", "가수", "아이돌", "드라마", "예능", "콘서트",
        "컴백", "열애", "결혼발표", "스캔들", "소속사", "팬미팅",
        "뮤직비디오", "발라드", "걸그룹", "보이그룹",
    ],
    "스포츠": [
        "스포츠", "축구", "야구", "농구", "배구", "올림픽", "월드컵",
        "경기결과", "감독", "국가대표", "리그", "챔피언스리그", "메달",
        "프로야구", "프로축구",
    ],
    "학교": [
        "수능", "입시", "초등학교", "중학교", "고등학교", "교육부",
        "학생회", "대학입시", "수시", "정시", "교권", "학교폭력",
    ],
}
_OFFTOPIC_FLAT = set()
for _words in OFFTOPIC_CONTEXT_WORDS.values():
    _OFFTOPIC_FLAT.update(_words)

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
# 3. 텍스트 정제 함수 (v18.5와 동일)
# =========================================================================
def _clean_title(raw_title):
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
        return True
    if NON_KOR_ENG_NUM_PATTERN.search(token):
        return True
    return False


def _tokenize(cleaned_title):
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


def _has_offtopic_context(tokens):
    for tok in tokens:
        if tok in _OFFTOPIC_FLAT:
            return True
    return False


# =========================================================================
# 4. 후보 키워드 추출 (앵커 + 검색의도어 조합) - v18.5와 동일
# =========================================================================
def _extract_candidates_from_tokens(tokens, category, anchors):
    candidates = []
    n = len(tokens)
    for i, tok in enumerate(tokens):
        matched_anchor = next((a for a in anchors if a in tok), None)
        if not matched_anchor:
            continue

        candidates.append({"keyword": tok, "anchor": matched_anchor, "intent_word": None})

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
        time.sleep(0.15 + random.random() * 0.15)
    return items


def _process_seed(search_api, category, seed_query, anchors, log=None):
    """
    [v18.6] 기사 단위로 오프토픽 맥락 여부를 먼저 확인하고, 해당되면
    이 기사에서는 후보를 만들지 않고 건너뛴다(offtopic_skipped 집계).
    [v18.7] mentions_set/sample_titles와 함께 articles(원문 메타데이터)도
    보존한다. URL 기준 중복 제거, 후보당 최대 5건.
    """
    news_items = _fetch_seed_news(search_api, seed_query, log=log)
    local_agg = {}
    offtopic_skipped = 0
    empty_skipped = 0

    for item in news_items:
        raw_title = item.get("title", "")
        raw_desc = item.get("description", "")
        pub_date = _parse_pub_date(item.get("pubDate", ""))
        link = item.get("link") or item.get("originallink") or raw_title

        cleaned_title = _clean_title(raw_title)
        cleaned_desc = _clean_title(raw_desc)
        combined_text = f"{cleaned_title} {cleaned_desc}".strip()
        if not combined_text:
            empty_skipped += 1
            continue

        tokens = _tokenize(combined_text)
        if not tokens:
            empty_skipped += 1
            continue

        # [v18.6] 정치/국제/연예/스포츠/학교 맥락이면 이 기사는 통째로 건너뜀
        if _has_offtopic_context(tokens):
            offtopic_skipped += 1
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
                    "articles": [],          # [v18.7] 기사 원문 메타데이터 보존
                    "seed_query": seed_query,
                    "first_pub_date": pub_date,
                    "latest_pub_date": pub_date,
                }
            entry = local_agg[key]
            entry["mentions_set"].add(link)
            if cleaned_title and cleaned_title not in entry["sample_titles"] and len(entry["sample_titles"]) < 5:
                entry["sample_titles"].append(cleaned_title)

            # [v18.7] articles 필드 채우기 - URL 기준 중복 제거, 후보당 최대 5개
            if link and not any(a["url"] == link for a in entry["articles"]) and len(entry["articles"]) < 5:
                entry["articles"].append({
                    "title": cleaned_title,
                    "url": link,
                    "snippet": cleaned_desc,
                    "date": pub_date,
                })

            if cand["intent_word"] and not entry["intent_word"]:
                entry["intent_word"] = cand["intent_word"]
            if pub_date:
                if not entry["first_pub_date"] or pub_date < entry["first_pub_date"]:
                    entry["first_pub_date"] = pub_date
                if not entry["latest_pub_date"] or pub_date > entry["latest_pub_date"]:
                    entry["latest_pub_date"] = pub_date

    if log and (offtopic_skipped > 0):
        log(f"[collector] category='{category}', seed='{seed_query}': "
            f"오프토픽(정치/국제/연예/스포츠/학교) 기사 {offtopic_skipped}건 제외, "
            f"본문없음 {empty_skipped}건 제외")

    return local_agg, offtopic_skipped, empty_skipped, len(news_items)


# =========================================================================
# 6. 메인 인터페이스
# =========================================================================
def collect_candidates(search_api, discovery_target=None, light_filter_target=None,
                        log=None, max_workers=4, max_per_category=60):
    """
    [v18.7] 반환값의 필드 구조는 v18.6에 articles 필드만 추가된 것이다.
    나머지 필드/로직은 v18.5와 완전히 동일하다(호환성 유지).
    """
    categories = discovery_target if discovery_target else list(CATEGORY_SEEDS.keys())
    categories = [c for c in categories if c in CATEGORY_SEEDS]
    if not categories:
        if log:
            log("[collector] 유효한 카테고리가 없어 전체 카테고리로 진행합니다.")
        categories = list(CATEGORY_SEEDS.keys())

    extra_by_category = {}
    if light_filter_target:
        for cat in categories:
            anchors = CATEGORY_SEEDS[cat]["anchors"]
            matched = [kw for kw in light_filter_target if any(a in kw for a in anchors)]
            if matched:
                extra_by_category[cat] = matched

    tasks = []
    for cat in categories:
        anchors = CATEGORY_SEEDS[cat]["anchors"]
        seeds = list(CATEGORY_SEEDS[cat]["seeds"]) + extra_by_category.get(cat, [])
        for seed in seeds:
            tasks.append((cat, seed, anchors))

    if log:
        log(f"[collector] 수집 대상 카테고리: {', '.join(categories)} (시드 쿼리 {len(tasks)}건)")

    global_agg = {}
    total_news_fetched = 0
    total_offtopic_skipped = 0
    total_empty_skipped = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(_process_seed, search_api, cat, seed, anchors, log): (cat, seed)
            for cat, seed, anchors in tasks
        }
        for future in as_completed(future_map):
            cat, seed = future_map[future]
            try:
                local_agg, offtopic_skipped, empty_skipped, fetched_count = future.result()
            except Exception as e:
                if log:
                    log(f"[collector] 시드 처리 실패 (category='{cat}', seed='{seed}'): {e}")
                continue

            total_news_fetched += fetched_count
            total_offtopic_skipped += offtopic_skipped
            total_empty_skipped += empty_skipped

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

                    # [v18.7] articles 병합 - URL 기준 중복 제거, 최대 5개
                    for a in entry.get("articles", []):
                        if not any(x["url"] == a["url"] for x in g["articles"]) and len(g["articles"]) < 5:
                            g["articles"].append(a)

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
                "articles": entry.get("articles", []),   # [v18.7]
                "seed_query": entry["seed_query"],
                "first_pub_date": entry["first_pub_date"],
                "latest_pub_date": entry["latest_pub_date"],
            })

    if log:
        log(f"[collector] 원본 뉴스 조회 {total_news_fetched}건 - "
            f"오프토픽(정치/국제/연예/스포츠/학교) 제외 {total_offtopic_skipped}건, "
            f"본문없음 제외 {total_empty_skipped}건")
        log(f"[collector] 최종 후보 수: {len(result)}건 (카테고리 {len(by_category)}개)")

    # [v18.7] 검증용 로그 - articles 도착 확인 (log 콜백 우선, print 폴백)
    _with_articles = [c for c in result if c.get("articles")]
    _diag_log(f"[DIAG] articles 보유 후보 수: {len(_with_articles)} / 전체 {len(result)}", log=log)
    if _with_articles:
        _sample = _with_articles[0]["articles"][0]
        _diag_log(f"[DIAG] 첫 번째 articles 샘플: {_sample}", log=log)
        _has_tag = bool(re.search(r"</?b>", _sample.get("snippet", "") + _sample.get("title", "")))
        _diag_log(f"[DIAG] snippet/title 내 <b> 태그 잔존 여부: {_has_tag}", log=log)
    else:
        _diag_log("[DIAG] articles가 채워진 후보가 없습니다. link/pub_date 값이 비어있는지 확인 필요.", log=log)

    return result


if __name__ == "__main__":
    class _DummyAPI:
        def search_news(self, query, display=100, start=1, sort="date"):
            return []

    def _print_log(msg):
        print(msg)

    res = collect_candidates(_DummyAPI(), discovery_target=["지원금"], log=_print_log)
    print(f"결과 {len(res)}건")
