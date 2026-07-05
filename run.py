"""
run.py — scorer.py(v19.8) Orchestration Layer
=====================================================================
scorer.py는 절대 수정하지 않는다. 이 파일은 다음 4가지 역할만 수행한다.
    1) candidates JSON 로드
    2) scorer.score_candidates(candidates, apis, log) 호출
    3) 결과(results, api_health)를 JSON 파일로 저장
    4) 실행 요약 콘솔 출력

CONTRACT:
    results, api_health = scorer.score_candidates(candidates, apis, log)

이 파일에는 타입 변환, 스키마 해석, 점수 계산, API 구현 로직을 포함하지 않는다.
=====================================================================
"""

import json
import argparse
from pathlib import Path
from datetime import datetime
from collections import Counter

import scorer


def make_logger(log_path: Path):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("a", encoding="utf-8")

    def log(message: str):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {message}"
        print(line)
        log_file.write(line + "\n")
        log_file.flush()

    return log, log_file


def load_candidates(input_path: Path):
    with input_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get("candidates", list(data.values()))
    return data


def save_json(data, output_path: Path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def build_summary(results, api_health):
    grade_counter = Counter(r.get("grade", "N/A") for r in results)
    lines = [
        "=" * 60,
        "BATCH EXECUTION SUMMARY (scorer.py v19.8)",
        "=" * 60,
        f"총 결과 건수 : {len(results)}",
        "-" * 60,
        "등급 분포:",
    ]
    for grade in ("TOP5", "TOP10", "보류", "위험", "N/A"):
        if grade in grade_counter:
            lines.append(f"  {grade} : {grade_counter[grade]}건")
    lines.append("-" * 60)
    lines.append(f"api_health: {api_health}")
    lines.append("=" * 60)
    return "\n".join(lines)


def build_apis():
    """
    실제 API 클라이언트를 연결하는 지점.
    scorer.py는 apis.get("search"), apis.get("ads"), apis.get("datalab")를
    사용하며, 각 클라이언트가 없으면 scorer.py 내부 _safe_call이 예외를
    흡수해 해당 API를 fail로 집계할 뿐 배치는 중단되지 않는다.

    실제 클라이언트 객체를 여기에 그대로 주입하면 된다.
    """
    apis = {
        "search": None,
        "ads": None,
        "datalab": None,
    }
    return apis


def main():
    parser = argparse.ArgumentParser(description="scorer.py v19.8 batch runner (No-GUI)")
    parser.add_argument("--input", required=True, help="candidates JSON 파일 경로 (531건)")
    parser.add_argument("--output-dir", default="./output", help="결과 저장 디렉터리")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    log_path = output_dir / f"run_{timestamp}.log"
    results_path = output_dir / f"results_{timestamp}.json"
    health_path = output_dir / f"api_health_{timestamp}.json"

    log, log_file = make_logger(log_path)

    try:
        log(f"[run] 시작 - input={args.input}")

        candidates = load_candidates(Path(args.input))
        log(f"[run] candidates 로드 완료 - {len(candidates)}건")

        apis = build_apis()

        results, api_health = scorer.score_candidates(candidates, apis, log)

        save_json(results, results_path)
        save_json(api_health, health_path)

        summary = build_summary(results, api_health)
        log("\n" + summary)

        log(f"[run] 결과 저장 완료 - {results_path}")
        log(f"[run] api_health 저장 완료 - {health_path}")
        log("[run] 정상 종료")

    finally:
        log_file.close()


if __name__ == "__main__":
    main()
