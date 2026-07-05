"""
run.py (v21.0) - Worker-based Pipeline Orchestrator
collector -> profit_filter -> scorer

역할: config 로딩, API 클라이언트 생성, 파이프라인 순차 호출, 결과 저장, 로깅.
원칙: scorer.py는 절대 수정하지 않으며, run.py는 타입 변환/스키마 해석/
      scoring 로직을 포함하지 않는다.
"""

import os
import sys
import json
import logging
import argparse
import traceback
from pathlib import Path
from datetime import datetime

# ---------------------------------------------------------------------------
# 0. BASE_DIR (PyInstaller-safe, app.py v20.2와 동일 규칙)
# ---------------------------------------------------------------------------
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

sys.path.insert(0, str(BASE_DIR))

# ---------------------------------------------------------------------------
# 1. 기존 production 모듈 (전부 수정하지 않고 그대로 import)
# ---------------------------------------------------------------------------
import collector
import profit_filter
import scorer
import naver_search_api
import config_manager


# ---------------------------------------------------------------------------
# 2. Logger
# ---------------------------------------------------------------------------
def make_logger(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"run_{ts}.log"

    logger = logging.getLogger("run_pipeline")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s")

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)

    logger.info(f"Log file: {log_path}")
    return logger


# ---------------------------------------------------------------------------
# 3. Config / API client 구성
#    NOTE: 아래 키 이름들이 실제 config.json 필드명과 다르면
#    CONFIG_KEY_MAP만 수정하면 된다. 나머지 로직은 손댈 필요 없음.
# ---------------------------------------------------------------------------
CONFIG_KEY_MAP = {
    "search_client_id": ["naver_client_id", "search_client_id", "client_id"],
    "search_client_secret": ["naver_client_secret", "search_client_secret", "client_secret"],
    "ads_api_key": ["ads_api_key", "ads_license_key"],
    "ads_secret_key": ["ads_secret_key"],
    "ads_customer_id": ["ads_customer_id"],
    "datalab_client_id": ["datalab_client_id", "naver_client_id"],
    "datalab_client_secret": ["datalab_client_secret", "naver_client_secret"],
}


def _pick(config: dict, keys: list, default=None):
    for k in keys:
        if k in config and config[k]:
            return config[k]
    return default


def load_runtime_config(log: logging.Logger) -> dict:
    try:
        config = config_manager.load_config()
        if not isinstance(config, dict):
            log.warning("config_manager.load_config() 반환값이 dict가 아님 -> 빈 dict로 대체")
            config = {}
    except Exception as e:
        log.error(f"config 로드 실패: {e} -> 빈 dict로 진행")
        config = {}
    return config


def build_search_api(config: dict, log: logging.Logger):
    """collector.collect_candidates(search_api=...) 에 주입될 객체"""
    try:
        client_id = _pick(config, CONFIG_KEY_MAP["search_client_id"])
        client_secret = _pick(config, CONFIG_KEY_MAP["search_client_secret"])
        if not client_id or not client_secret:
            log.error("Naver Search API 키 누락 -> collector가 빈 결과를 반환할 수 있음")
        return naver_search_api.NaverSearchAPI(client_id=client_id, client_secret=client_secret)
    except Exception as e:
        log.error(f"NaverSearchAPI 초기화 실패: {e}\n{traceback.format_exc()}")
        return None


def build_apis(config: dict, log: logging.Logger) -> dict:
    """scorer.score_candidates(filtered, apis, log) 에 주입될 dict.
    scorer.py 내부의 apis.get(...) 호출부 key와 정확히 맞아야 하므로,
    실행 후 로그에 KeyError가 찍히면 이 dict의 key 이름만 맞춰주면 된다."""
    apis = {}

    try:
        client_id = _pick(config, CONFIG_KEY_MAP["search_client_id"])
        client_secret = _pick(config, CONFIG_KEY_MAP["search_client_secret"])
        apis["search"] = naver_search_api.NaverSearchAPI(client_id=client_id, client_secret=client_secret)
    except Exception as e:
        log.error(f"apis['search'] 생성 실패: {e}")
        apis["search"] = None

    try:
        ads_key = _pick(config, CONFIG_KEY_MAP["ads_api_key"])
        ads_secret = _pick(config, CONFIG_KEY_MAP["ads_secret_key"])
        ads_customer = _pick(config, CONFIG_KEY_MAP["ads_customer_id"])
        apis["ads"] = naver_search_api.NaverAdsAPI(
            api_key=ads_key, secret_key=ads_secret, customer_id=ads_customer
        )
    except Exception as e:
        log.error(f"apis['ads'] 생성 실패: {e}")
        apis["ads"] = None

    try:
        dl_id = _pick(config, CONFIG_KEY_MAP["datalab_client_id"])
        dl_secret = _pick(config, CONFIG_KEY_MAP["datalab_client_secret"])
        apis["datalab"] = naver_search_api.NaverDataLabAPI(client_id=dl_id, client_secret=dl_secret)
    except Exception as e:
        log.error(f"apis['datalab'] 생성 실패: {e}")
        apis["datalab"] = None

    return apis


# ---------------------------------------------------------------------------
# 4. Pipeline — collector -> profit_filter -> scorer
# ---------------------------------------------------------------------------
def run_pipeline(categories, max_workers, log: logging.Logger):
    log.info(f"=== PIPELINE START | categories={categories} | max_workers={max_workers} ===")

    config = load_runtime_config(log)
    search_api = build_search_api(config, log)
    apis = build_apis(config, log)

    # ---- STEP 1. collector ----
    log.info("STEP 1/3: collector.collect_candidates() 호출")
    try:
        candidates = collector.collect_candidates(
            search_api=search_api,
            discovery_target=categories,
            light_filter_target=None,
            log=log,
            max_workers=max_workers,
        )
    except Exception as e:
        log.error(f"collector.collect_candidates() 실패: {e}\n{traceback.format_exc()}")
        candidates = []

    log.info(f"STEP 1 결과: candidates={len(candidates) if candidates else 0}건")
    if not candidates:
        log.warning("collector가 빈 결과를 반환함 -> API 키 / discovery_target(categories) 값을 확인할 것")

    # ---- STEP 2. profit_filter ----
    log.info("STEP 2/3: profit_filter.filter_candidates() 호출")
    try:
        filtered = profit_filter.filter_candidates(candidates, log=log)
    except Exception as e:
        log.error(f"profit_filter.filter_candidates() 실패: {e}\n{traceback.format_exc()}")
        filtered = candidates  # 필터 실패 시 원본으로 폴백 (파이프라인 중단 방지)

    log.info(f"STEP 2 결과: filtered={len(filtered) if filtered else 0}건")

    # ---- STEP 3. scorer (수정 금지 영역) ----
    log.info("STEP 3/3: scorer.score_candidates() 호출")
    try:
        results, api_health = scorer.score_candidates(filtered, apis, log=log)
    except Exception as e:
        log.error(f"scorer.score_candidates() 실패: {e}\n{traceback.format_exc()}")
        results, api_health = [], {"status": "error", "error": str(e)}

    log.info(f"STEP 3 결과: results={len(results) if results else 0}건")
    log.info("=== PIPELINE END ===")

    return results, api_health


# ---------------------------------------------------------------------------
# 5. 결과 저장
# ---------------------------------------------------------------------------
def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def save_outputs(results, api_health, out_dir: Path, log: logging.Logger):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_path = out_dir / f"results_{ts}.json"
    health_path = out_dir / f"api_health_{ts}.json"

    save_json(results_path, results)
    save_json(health_path, api_health)

    log.info(f"결과 저장: {results_path}")
    log.info(f"API health 저장: {health_path}")
    return results_path, health_path


# ---------------------------------------------------------------------------
# 6. 요약 리포트 (Grade Order: TOP5 -> TOP10 -> 보류 -> 위험, app.py v20.2 기준)
# ---------------------------------------------------------------------------
GRADE_ORDER = ["TOP5", "TOP10", "보류", "위험"]


def build_summary(results, log: logging.Logger):
    if not results:
        log.warning("results가 비어있음 -> 요약 생성 불가")
        return

    grade_count = {g: 0 for g in GRADE_ORDER}
    no_keyword_count = 0

    for r in results:
        if not isinstance(r, dict):
            continue
        grade = r.get("grade", "미분류")
        if grade in grade_count:
            grade_count[grade] += 1
        if not r.get("keyword"):
            no_keyword_count += 1

    log.info("----- SUMMARY -----")
    for g in GRADE_ORDER:
        log.info(f"  {g}: {grade_count[g]}건")
    log.info(f"  keyword 누락: {no_keyword_count}건 / 전체 {len(results)}건")
    log.info("--------------------")


# ---------------------------------------------------------------------------
# 7. main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Worker-based pipeline runner (collector -> profit_filter -> scorer)"
    )
    parser.add_argument("--categories", type=str, default=None,
                         help="쉼표로 구분된 카테고리 목록. 미지정 시 config.json의 categories 사용")
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--output-dir", type=str, default=str(BASE_DIR / "output"))
    parser.add_argument("--log-dir", type=str, default=str(BASE_DIR / "logs"))
    parser.add_argument("--dry-run", action="store_true",
                         help="결과를 저장하지 않고 파이프라인 동작(단계별 건수/keyword 누락 여부)만 검증")
    args = parser.parse_args()

    log = make_logger(Path(args.log_dir))

    if args.categories:
        categories = [c.strip() for c in args.categories.split(",") if c.strip()]
    else:
        config = load_runtime_config(log)
        categories = config.get("categories", [])
        if not categories:
            log.error("categories가 지정되지 않음 (--categories 옵션 또는 config.json의 categories 필드 확인)")
            sys.exit(1)

    results, api_health = run_pipeline(categories, args.max_workers, log)

    if args.dry_run:
        log.info("[DRY-RUN] 결과 저장을 생략합니다.")
        build_summary(results, log)
        sample = results[:3] if results else []
        log.info(f"[DRY-RUN] 샘플 결과:\n{json.dumps(sample, ensure_ascii=False, indent=2)}")
    else:
        save_outputs(results, api_health, Path(args.output_dir), log)
        build_summary(results, log)


if __name__ == "__main__":
    main()
