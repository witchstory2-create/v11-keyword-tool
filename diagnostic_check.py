#!/usr/bin/env python3
"""
diagnostic_check.py
--------------------
config.json의 categories 배열이 실제로 collector 실행 경로(run.py / app.py)에
반영되는지를 자동으로 판정하는 진단 스크립트.

이 스크립트는 scorer.py, collector.py, app.py, run.py 등 기존 파이프라인 코드를
전혀 수정하지 않습니다. config.json만 임시로 변경한 뒤 반드시 원상복구합니다.

사용법:
    python diagnostic_check.py
    python diagnostic_check.py --entry run.py
    python diagnostic_check.py --entry app.py --timeout 180
    python diagnostic_check.py --config C:\\path\\to\\config.json

종료 시 다음 판정 중 하나를 출력합니다.
    - CONFIG_DRIVEN
    - HARDCODED_CATEGORY_SEEDS
    - CONFIG_USED_BUT_MAPPING_MISSING
    - ERROR (진단 스크립트 자체의 실행 실패, 시스템 판정이 아님)
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime

MARKER = "테스트카테고리999"
DEFAULT_CONFIG_PATH = "config.json"
CANDIDATE_ENTRY_SCRIPTS = ["run.py", "app.py"]
CANDIDATE_RESULT_FILES = [
    "keyword_history.json",
    "results.json",
    "history.json",
]
CANDIDATE_LOG_FILES = [
    "run.log",
    "app.log",
    "collector.log",
    "pipeline.log",
]


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def backup_config(config_path):
    if not os.path.exists(config_path):
        raise FileNotFoundError(
            f"{config_path} 를 찾을 수 없습니다. "
            f"diagnostic_check.py를 config.json과 같은 폴더에 두고 실행하거나 "
            f"--config 옵션으로 경로를 지정하세요."
        )
    backup_path = f"{config_path}.bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(config_path, backup_path)
    with open(config_path, "r", encoding="utf-8") as f:
        original_text = f.read()
    log(f"config.json 백업 완료 -> {backup_path}")
    return backup_path, original_text


def inject_test_category(config_path):
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if "categories" not in data or not isinstance(data["categories"], list):
        raise KeyError(
            "config.json에 'categories' 리스트 필드가 없습니다. "
            "구조를 먼저 확인해야 진단을 진행할 수 없습니다."
        )

    if MARKER not in data["categories"]:
        data["categories"].append(MARKER)

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    log(f"config.json categories에 '{MARKER}' 임시 추가 완료")


def restore_config(config_path, original_text):
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(original_text)
    log("config.json 원상복구 완료")


def detect_entry_script(explicit_entry=None):
    if explicit_entry:
        if os.path.exists(explicit_entry):
            return explicit_entry
        raise FileNotFoundError(f"지정한 entry script를 찾을 수 없습니다: {explicit_entry}")

    for candidate in CANDIDATE_ENTRY_SCRIPTS:
        if os.path.exists(candidate):
            log(f"실행 대상 자동 감지: {candidate}")
            return candidate

    raise FileNotFoundError(
        "run.py 또는 app.py를 현재 폴더에서 찾을 수 없습니다. "
        "--entry 옵션으로 직접 지정하세요. 예) --entry app.py"
    )


def run_entry_script(entry_script, timeout_sec):
    log(f"{entry_script} 실행 시작 (timeout={timeout_sec}s) ...")
    timed_out = False
    try:
        proc = subprocess.run(
            [sys.executable, entry_script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        returncode = proc.returncode
    except subprocess.TimeoutExpired as e:
        def _decode(x):
            if x is None:
                return ""
            return x if isinstance(x, str) else x.decode("utf-8", "replace")
        stdout = _decode(e.stdout)
        stderr = _decode(e.stderr)
        returncode = None
        timed_out = True
        log(f"실행이 timeout({timeout_sec}s)에 걸려 강제 종료되었습니다. "
            f"부분 캡처된 출력만으로 판정합니다. --timeout 값을 늘려서 재실행을 권장합니다.")

    log(f"실행 종료 (returncode={returncode}, timed_out={timed_out})")
    return stdout, stderr, returncode, timed_out


def collect_existing_file_contents():
    """결과/로그 파일이 존재하면 내용을 모두 읽어 반환 (실행 후 상태 기준)."""
    collected = {}
    for fname in CANDIDATE_RESULT_FILES + CANDIDATE_LOG_FILES:
        if not os.path.exists(fname):
            continue
        try:
            with open(fname, "r", encoding="utf-8", errors="replace") as f:
                collected[fname] = f.read()
        except Exception as e:
            log(f"경고: {fname} 읽기 실패 ({e})")
    return collected


def search_marker(text_sources: dict, marker: str):
    """marker가 발견된 소스 이름 목록 반환."""
    hits = []
    for source_name, content in text_sources.items():
        if content and marker in content:
            hits.append(source_name)
    return hits


def judge(hits, returncode, timed_out, stderr):
    """
    판정 로직:
    - marker가 어디에도 없으면            -> HARDCODED_CATEGORY_SEEDS
    - marker가 있고 정상 종료             -> CONFIG_DRIVEN
    - marker가 stderr(에러/traceback)에 있고
      비정상 종료/timeout               -> CONFIG_USED_BUT_MAPPING_MISSING
    """
    if not hits:
        return "HARDCODED_CATEGORY_SEEDS", (
            f"stdout/stderr/결과파일/로그 어디에서도 '{MARKER}' 문자열이 발견되지 않았습니다. "
            "config.json의 categories 변경이 실행에 아무 영향을 주지 않았습니다. "
            "collector.py 내부의 CATEGORY_SEEDS가 config와 무관하게 하드코딩되어 있을 가능성이 매우 높습니다."
        )

    error_like = (returncode is not None and returncode != 0) or timed_out
    marker_in_stderr = "stderr" in hits and MARKER in (stderr or "")

    if error_like and marker_in_stderr:
        return "CONFIG_USED_BUT_MAPPING_MISSING", (
            f"'{MARKER}'가 stderr(에러/traceback)에서 발견되었고 프로세스가 "
            f"비정상 종료(returncode={returncode}, timed_out={timed_out})되었습니다. "
            "config는 읽히지만 해당 카테고리에 대한 seed/anchor 매핑이 정의되어 있지 않아 실패한 것으로 보입니다."
        )

    return "CONFIG_DRIVEN", (
        f"'{MARKER}'가 다음 위치에서 발견되었습니다: {hits}. "
        "config.json의 categories 값이 실제 실행 경로(쿼리 생성/API 호출/결과 파일)에 반영되고 있습니다."
    )


def main():
    parser = argparse.ArgumentParser(
        description="config.json categories -> collector 반영 여부 자동 진단"
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH,
                         help="config.json 경로 (기본: ./config.json)")
    parser.add_argument("--entry", default=None,
                         help="실행할 entry script (기본: run.py 또는 app.py 자동 감지)")
    parser.add_argument("--timeout", type=int, default=120,
                         help="실행 timeout(초), 기본 120초. 실제 파이프라인이 오래 걸리면 늘리세요.")
    args = parser.parse_args()

    config_path = args.config
    backup_path = None
    original_text = None
    verdict = None
    reason = None

    try:
        # 1. 백업
        backup_path, original_text = backup_config(config_path)

        # 2. 임시 카테고리 주입
        inject_test_category(config_path)

        # 3. entry script 1회 실행
        entry_script = detect_entry_script(args.entry)
        stdout, stderr, returncode, timed_out = run_entry_script(entry_script, args.timeout)

        # 4. 마커 검색 (stdout/stderr + 결과/로그 파일)
        text_sources = {"stdout": stdout, "stderr": stderr}
        text_sources.update(collect_existing_file_contents())

        hits = search_marker(text_sources, MARKER)

        # 5. 판정
        verdict, reason = judge(hits, returncode, timed_out, stderr)

    except Exception as e:
        verdict = "ERROR"
        reason = f"진단 스크립트 자체에서 예외 발생: {type(e).__name__}: {e}"
        log(f"[오류] {reason}")

    finally:
        # 6. 반드시 원상복구
        if original_text is not None:
            try:
                restore_config(config_path, original_text)
            except Exception as restore_err:
                log(
                    f"[치명적 경고] config.json 원상복구 실패!!! "
                    f"즉시 수동으로 백업 파일을 복원하세요: {backup_path} "
                    f"(원인: {restore_err})"
                )

    print()
    print("=" * 60)
    print(f"최종 판정: {verdict}")
    print("-" * 60)
    print(f"판정 근거: {reason}")
    print("=" * 60)
    if backup_path:
        print(f"(백업 파일은 삭제하지 않았습니다: {backup_path})")


if __name__ == "__main__":
    main()
