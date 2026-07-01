# -*- coding: utf-8 -*-
"""
collector.py (v18.2) - Discovery 단계 전담

[이 파일의 역할]
- 수익형 카테고리별 시드 키워드 관리
- Google News RSS / 네이버 뉴스 API 조회
- 기사 제목에서 수익형 후보 문구만 추출 (같은 기사 안에서만 bigram 조합)
- 언론사명 / 날짜 / 지역명 / 인물명 / 영어 잔재어 제거
- 후보를 keyword, category, mentions, articles, recency 등 필드로 반환

[이 파일이 하지 않는 일 - 다른 파일의 책임]
- 수익형 여부 재판단, 카테고리 가중치 부여 -> profit_filter.py
- API로 문서수/검색량/DataLab 조회 -> naver_search_api.py
- 점수 계산, 등급(TOP5/TOP10/보류/위험) 분류 -> scorer.py
- 화면 표시 -> app.py

[핵심 설계]
- "기사 제목 -> 무조건 인접 토큰 조합" 방식을 사용하지 않는다.
- 카테고리 루트 단어(보험/대출/환급/지원금/연금/세금/청약/부동산/카드)를 포함한 토큰만
  '앵커(anchor)'로 인정하고, 앵커 단독 또는 앵커+검색의도 단어(신청/환급/할인/인상 등)
  조합만 후보로 채택한다.
- 앵커도 의도어도 아닌 토큰(반도체, 상임위, 배재고 등)은 조합 자체가 발생하지 않으므로
  STOPWORD를 계속 추가하는 임시방편 없이 노이즈가 원천적으로 제거된다.
"""

import re
import html
import time
import json
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed

# ------------------------------------------------------------------
# 1) 수익형 카테고리 시드 키워드
#    - 딕셔너리의 key 자체가 카테고리 '루트 단어'로도 사용된다.
#    - 카테고리를 추가/삭제하려면 이 딕셔너리만 수정하면 된다.
# ------------------------------------------------------------------
CATEGORY_SEEDS = {
    "보험": ["보험", "실손보험", "자동차보험", "건강보험", "암보험", "보험료"],
    "대출": ["대출", "신용대출", "전세대출", "정책자금대출", "대출금리"],
    "환급": ["환급", "세금환급", "환급금", "연말정산 환급", "미환급금"],
    "지원금": ["지원금", "정부지원금", "청년지원금", "출산지원금", "지원금 신청"],
    "연금": ["연금", "국민연금", "기초연금", "퇴직연금", "연금개혁"],
    "세금": ["세금", "종합소득세", "재산세", "자동차세", "종부세"],
    "청약": ["청약", "주택청약", "특별공급", "청약통장"],
    "부동산": ["부동산", "전세", "월세", "아파트 분양", "부동산 규제"],
    "카드": ["카드", "신용카드", "체크카드", "카드혜택", "카드사용"],
}

CATEGORY_ROOTS = list(CATEGORY_SEEDS.keys())  # 앵커 판정에 사용되는 루트 단어 목록

SEED_KEYWORDS_FLAT = set()
for _seeds in CATEGORY_SEEDS.values():
    SEED_KEYWORDS_FLAT.update(_seeds)

# ------------------------------------------------------------------
# 2) 검색의도 단어 - 앵커와 결합했을 때만 후보로 인정하는 수식어
# ------------------------------------------------------------------
INTENT_WORDS = {
    "신청", "대상", "조건", "방법", "신청방법", "신청기간", "접수", "마감",
    "지급", "지급일", "지급대상", "환급", "환급금", "할인", "인상", "인하",
    "한도", "기준", "개정", "변경", "확대", "축소", "서류", "절차", "발표",
    "공고", "조회", "계산", "계산법", "신청서", "자격", "대상자", "만기",
    "갱신", "가입", "해지", "보장", "특약", "공제", "감면", "면제", "혜택",
}

# ------------------------------------------------------------------
# 3) 한글 불용어 (조사/접속어/보조어) - 토큰화 단계에서 1차로 걸러냄
# ------------------------------------------------------------------
STOPWORDS = {
    "그리고", "하지만", "그러나", "이번", "관련", "위해", "통해", "대한",
    "오늘", "내일", "어제", "이날", "한편", "이에", "따라", "가장",
    "완전", "정말", "너무", "매우", "관계자는", "밝혔다", "전했다",
    "것으로", "것이다", "등을", "등이", "됐다", "됐다는", "라며",
    "있다", "없다", "한다", "했다", "하는", "이라", "이라는", "이런",
    "저런", "그런", "이제", "지금", "바로", "역시", "또한", "이후",
}

# ------------------------------------------------------------------
# 4) 영어/HTML 잔재어
# ------------------------------------------------------------------
ENGLISH_STOPWORDS = {
    "and", "the", "quot", "amp", "com", "for", "with", "you", "are",
    "this", "that", "was", "will", "have", "from", "your", "pop",
    "top", "www", "net", "org", "html", "news", "not", "but", "all",
    "can", "has", "had", "her", "his", "one", "our", "out", "who",
    "get", "how", "now", "new", "vs",
}

# ------------------------------------------------------------------
# 5) 언론사명 (Google News RSS 제목 끝에 "- 언론사"로 붙는 접미사 방어용)
# ------------------------------------------------------------------
PRESS_NAMES = {
    "조선일보", "중앙일보", "동아일보", "한겨레", "경향신문", "한국일보",
    "매일경제", "한국경제", "서울신문", "국민일보", "세계일보", "문화일보",
    "머니투데이", "이데일리", "아시아경제", "파이낸셜뉴스", "헤럴드경제",
    "연합뉴스", "뉴시스", "노컷뉴스", "오마이뉴스", "프레시안", "뉴스1",
    "YTN", "SBS", "MBC", "KBS", "JTBC", "채널A", "TV조선", "MBN",
}

# ------------------------------------------------------------------
# 6) 날짜/요일 상시 반복 표현
# ------------------------------------------------------------------
DATE_STOPWORDS = {"월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"}

# ------------------------------------------------------------------
# 7) 한국 행정구역명 (지역명 차단)
# ------------------------------------------------------------------
PLACE_NAMES = {
    "서울", "서울시", "부산", "부산시", "대구", "대구시", "인천", "인천시",
    "광주", "광주시", "대전", "대전시", "울산", "울산시", "세종", "세종시",
    "경기", "경기도", "강원", "강원도", "충북", "충북도", "충남", "충남도",
    "전북", "전북도", "전남", "전남도", "경북", "경북도", "경남", "경남도",
    "제주", "제주도", "수원", "수원시", "성남", "성남시", "용인", "용인시",
    "고양", "고양시", "청주", "청주시", "전주", "전주시", "여수", "여수시",
    "순천", "순천시", "포항", "포항시", "창원", "창원시", "진주", "진주시",
    "강릉", "강릉시", "춘천", "춘천시", "원주", "원주시", "천안", "천안시",
    "아산", "아산시", "김해", "김해시", "양산", "양산시", "거제", "거제시",
    "통영", "통영시", "목포", "목포시", "광양", "광양시", "익산", "익산시",
    "군산", "군산시", "제천", "제천시", "충주", "충주시", "안동", "안동시",
    "구미", "구미시", "경주", "경주시", "파주", "파주시", "김포", "김포시",
    "남양주", "남양주시", "평택", "평택시", "안산", "안산시", "안양", "안양시",
    "부천", "부천시", "시흥", "시흥시", "화성", "화성시", "광명", "광명시",
    "하남", "하남시", "이천", "이천시", "오산", "오산시", "의정부", "의정부시",
}

# ------------------------------------------------------------------
# 8) 정치인/외국 정상 등 - 일반 뉴스에 반복 등장하지만 수익형 키워드와 무관한 인물명
# ------------------------------------------------------------------
PERSON_NAMES = {
    "시진핑", "푸틴", "트럼프", "바이든", "김정은", "기시다", "이시바",
    "젤렌스키", "이재명", "한동훈", "조국", "윤석열", "오세훈", "홍준표",
    "이낙연", "안철수", "나경원",
}

PARTICLE_SUFFIX = ("은", "는", "이", "가", "을", "를", "의", "에", "에서",
                    "으로", "로", "와", "과", "도", "만", "까지", "부터")


def _strip_particle(token):
    """단어 끝에 붙은 조사를 제거한다."""
    for p in sorted(PARTICLE_SUFFIX, key=len, reverse=True):
        if len(token) > len(p) + 1 and token.endswith(p):
            return token[: -len(p)]
    return token


def _clean_title(title):
    """HTML 엔티티 해제 + 언론사 접미사 제거 + 특수문자 제거"""
    title = html.unescape(title)  # &quot; &amp; &#39; 등을 실제 문자로 변환
    # Google News RSS 형식: "헤드라인 - 언론사명" -> 끝의 " - 언론사" 구간 제거
    title = re.sub(r"\s[-|]\s[^-|]{1,20}$", "", title)
    title = re.sub(r"\[[^\]]*\]", " ", title)
    title = re.sub(r"\([^)]*\)", " ", title)
    title = re.sub(r"<[^>]+>", " ", title)
    title = re.sub(r"[^가-힣0-9A-Za-z%\s]", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def _is_meaningless_token(t):
    """의미 없는/무관한 토큰 판정 (언론사명, 날짜, 지역명, 인물명, 영어 잔재어 등)"""
    if not t:
        return True
    if t.isdigit():
        return True
    if t in PRESS_NAMES or t in DATE_STOPWORDS or t in PLACE_NAMES or t in PERSON_NAMES:
        return True
    if re.match(r"^\d{4}년$", t) or re.match(r"^\d{1,2}월$", t) or re.match(r"^\d{1,2}일$", t):
        return True
    low = t.lower()
    if low in ENGLISH_STOPWORDS:
        return True
    if t.isascii() and t.isalpha() and len(t) <= 4:
        return True
    return False


def _tokenize(title):
    tokens = []
    for raw in _clean_title(title).split(" "):
        if not raw:
            continue
        t = _strip_particle(raw)
        if len(t) < 2:
            continue
        if t in STOPWORDS:
            continue
        if _is_meaningless_token(t):
            continue
        tokens.append(t)
    return tokens


def _is_pure_seed(phrase):
    """후보가 시드 키워드 자체와 완전히 동일할 때만 차단(부분 포함이 아닌 완전 일치)"""
    return phrase in SEED_KEYWORDS_FLAT


def _matched_roots(token):
    """토큰(또는 문구) 안에 포함된 카테고리 루트 단어 목록을 반환"""
    return [root for root in CATEGORY_ROOTS if root in token]


def _is_anchor(token):
    """토큰이 카테고리 루트 단어를 포함하고 있으면 그 루트를, 아니면 None을 반환"""
    roots = _matched_roots(token)
    return roots[0] if roots else None


def extract_profit_phrases(title):
    """
    같은 기사 제목 안에서만 처리한다.
    - 앵커(카테고리 루트 단어를 포함한 토큰) 단독 -> 후보 (예: 자동차보험, 미환급금)
    - 앵커 + 검색의도 단어 인접 조합 -> 후보 (예: 보험료 인상, 건강보험 환급)
    - 앵커 + 앵커 인접 조합 -> 후보 (예: 국민연금 건강보험)
    - 그 외(앵커도 의도어도 아닌 일반 명사끼리의 조합)는 절대 후보로 만들지 않는다.
      -> '반도체', '상임위', '배재고' 같은 무관 단어가 조합될 경로 자체를 차단.
    반환: (candidates 리스트, {후보문구: [매칭된 카테고리 루트]} 딕셔너리)
    """
    tokens = _tokenize(title)
    candidates = []
    categories_by_candidate = {}

    for t in tokens:
        if _is_anchor(t) and len(t) >= 3 and not _is_pure_seed(t):
            candidates.append(t)
            categories_by_candidate[t] = _matched_roots(t)

    for i in range(len(tokens) - 1):
        a, b = tokens[i], tokens[i + 1]
        a_anchor = _is_anchor(a)
        b_anchor = _is_anchor(b)
        a_intent = a in INTENT_WORDS
        b_intent = b in INTENT_WORDS

        phrase = None
        if a_anchor and b_intent:
            phrase = f"{a} {b}"
        elif a_intent and b_anchor:
            phrase = f"{a} {b}"
        elif a_anchor and b_anchor:
            phrase = f"{a} {b}"

        if phrase and not _is_pure_seed(phrase):
            candidates.append(phrase)
            categories_by_candidate[phrase] = list(set(_matched_roots(phrase)))

    return candidates, categories_by_candidate


def fetch_google_news_rss(query, limit=15, timeout=6):
    """Google News RSS에서 특정 검색어에 대한 기사 목록을 가져온다."""
    url = "https://news.google.com/rss/search?q=%s&hl=ko&gl=KR&ceid=KR:ko" % urllib.parse.quote(query)
    items = []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as res:
            data = res.read()
        root = ET.fromstring(data)
        for item in root.findall(".//item")[:limit]:
            title = item.findtext("title") or ""
            link = item.findtext("link") or ""
            pub = item.findtext("pubDate") or ""
            if title:
                items.append({"title": title, "link": link, "pub": pub, "source": "google_rss"})
    except Exception:
        pass
    return items


def fetch_naver_news_by_keyword(client_id, client_secret, keyword, display=15, timeout=6):
    """네이버 검색 Open API(뉴스)에서 특정 검색어에 대한 기사 목록을 가져온다."""
    items = []
    if not client_id or not client_secret:
        return items
    url = ("https://openapi.naver.com/v1/search/news.json?query=%s&display=%d&sort=date"
           % (urllib.parse.quote(keyword), display))
    try:
        req = urllib.request.Request(url, headers={
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret,
        })
        with urllib.request.urlopen(req, timeout=timeout) as res:
            j = json.loads(res.read().decode("utf-8", errors="replace"))
        for it in j.get("items", []):
            title = re.sub(r"<[^>]+>", "", it.get("title", ""))
            title = html.unescape(title)
            items.append({"title": title, "link": it.get("link", ""),
                          "pub": it.get("pubDate", ""), "source": "naver_news"})
    except Exception:
        pass
    return items


def _parse_pubdate_to_epoch(pub):
    """RFC822 형식(pubDate) 문자열을 epoch 시간(float)으로 변환. 실패하면 None."""
    if not pub:
        return None
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(pub)
        return dt.timestamp()
    except Exception:
        return None


def _fetch_one_seed(category, seed, client_id, client_secret):
    """하나의 (카테고리, 시드) 조합에 대해 구글+네이버 뉴스를 모두 수집"""
    articles = []
    articles += fetch_google_news_rss(seed, limit=15)
    articles += fetch_naver_news_by_keyword(client_id, client_secret, seed, display=15)
    return category, seed, articles


def collect_candidates(search_api=None, discovery_target=100, light_filter_target=40, log=None,
                        max_workers=8):
    """
    Discovery 전담 함수.

    처리 순서:
    1) CATEGORY_SEEDS에 등록된 모든 (카테고리, 시드) 조합에 대해
       Google News RSS + 네이버 뉴스 API를 병렬로 조회한다.
    2) 각 기사 제목에서 extract_profit_phrases()로 '앵커 기반' 후보만 추출한다.
    3) 후보별로 언급 건수(mentions), 참고 기사(articles, 최대 3개),
       최근성(recency, 시간 단위)을 집계한다.
    4) mentions 기준으로 상위 discovery_target개를 1차 선별하고,
       범용 시드 자체(generic_flag)는 뒤로 밀어 light_filter_target개로 축소한다.

    반환되는 각 후보 딕셔너리 필드:
      - keyword       : 후보 문구
      - category      : 매칭된 수익형 카테고리(복수 가능, 콤마 구분)
      - mentions      : 뉴스 언급 건수(중복 기사 집계)
      - articles      : 실제 기사 목록(최대 3개, title/link/source/pub 포함)
      - recency       : 가장 최근 기사로부터 경과 시간(시간 단위, float). 알 수 없으면 None.
      - matched_seed  : 이 후보를 발견하게 된 시드 키워드 목록(콤마 구분)
      - generic_flag  : 후보가 시드 키워드 자체와 완전히 동일한지 여부
    """
    def _log(msg):
        if log:
            log(msg)

    client_id = search_api.client_id if search_api else None
    client_secret = search_api.client_secret if search_api else None

    tasks = [(category, seed) for category, seeds in CATEGORY_SEEDS.items() for seed in seeds]
    _log(f"[collector] 카테고리 {len(CATEGORY_SEEDS)}개 / 시드 {len(tasks)}개 조회 시작")

    cand_map = {}
    now = time.time()
    total_articles = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_fetch_one_seed, cat, seed, client_id, client_secret)
                   for cat, seed in tasks]
        for future in as_completed(futures):
            try:
                search_category, seed, articles = future.result()
            except Exception as e:
                _log(f"[collector] 시드 수집 실패: {e}")
                continue

            total_articles += len(articles)
            for art in articles:
                title = art.get("title", "")
                if not title:
                    continue
                pub_epoch = _parse_pubdate_to_epoch(art.get("pub", ""))
                phrases, cat_map = extract_profit_phrases(title)
                for p in set(phrases):
                    entry = cand_map.setdefault(p, {
                        "keyword": p,
                        "categories": set(),
                        "matched_seeds": set(),
                        "mentions": 0,
                        "articles": [],
                        "latest_epoch": None,
                    })
                    entry["mentions"] += 1
                    entry["categories"].update(cat_map.get(p, [search_category]))
                    entry["matched_seeds"].add(seed)
                    if len(entry["articles"]) < 3:
                        entry["articles"].append({
                            "title": title, "link": art.get("link", ""),
                            "source": art.get("source", "unknown"), "pub": art.get("pub", ""),
                        })
                    if pub_epoch is not None:
                        if entry["latest_epoch"] is None or pub_epoch > entry["latest_epoch"]:
                            entry["latest_epoch"] = pub_epoch

    _log(f"[collector] 수집 기사 수(중복 포함): {total_articles}")

    candidates = []
    for c in cand_map.values():
        keyword = c["keyword"]
        if len(keyword.replace(" ", "")) < 2:
            continue
        recency = None
        if c["latest_epoch"] is not None:
            recency = round((now - c["latest_epoch"]) / 3600.0, 2)  # 시간 단위
        candidates.append({
            "keyword": keyword,
            "category": ", ".join(sorted(c["categories"])),
            "mentions": c["mentions"],
            "articles": c["articles"],
            "recency": recency,
            "matched_seed": ", ".join(sorted(c["matched_seeds"])),
            "generic_flag": _is_pure_seed(keyword),
        })

    candidates.sort(key=lambda x: x["mentions"], reverse=True)
    candidates = candidates[:discovery_target]
    _log(f"[collector] 1차 후보 수(앵커+검색의도 기반, 카테고리 매칭 확인됨): {len(candidates)}")

    candidates.sort(key=lambda x: (x["generic_flag"], -x["mentions"]))
    result = candidates[:light_filter_target]
    _log(f"[collector] 경량 필터 후 profit_filter 전달 대상: {len(result)}")
    return result
