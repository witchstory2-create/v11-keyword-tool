import json
import os

CONFIG_FILE = "config.json"


def save_config(data):
    """API 키 등 설정값을 로컬 파일에 저장한다."""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_config():
    """저장된 설정값을 불러온다. 파일이 없으면 빈 딕셔너리를 반환."""
    if not os.path.exists(CONFIG_FILE):
        return {}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}
