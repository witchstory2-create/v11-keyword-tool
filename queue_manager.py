import json
import os
from datetime import date

QUEUE_FILE = "daily_queue.json"


def save_queue(queue_items):
    data = {"date": str(date.today()), "items": queue_items}
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_queue():
    if not os.path.exists(QUEUE_FILE):
        return None
    with open(QUEUE_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    if data.get("date") != str(date.today()):
        return None
    return data.get("items", [])
