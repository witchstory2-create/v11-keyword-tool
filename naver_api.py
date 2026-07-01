import requests
import time
import hmac
import hashlib
import base64
import urllib.parse


def make_signature(timestamp, method, uri, secret_key):
    message = f"{timestamp}.{method}.{uri}"
    return base64.b64encode(
        hmac.new(secret_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
    ).decode("utf-8")


def normalize_keyword(keyword):
    return keyword.replace(" ", "").strip()


def get_keyword_data(keyword, customer_id, api_key, secret_key):
    uri = "/keywordstool"
    method = "GET"
    timestamp = str(int(time.time() * 1000))

    keyword = normalize_keyword(keyword)

    headers = {
        "X-Timestamp": timestamp,
        "X-API-KEY": api_key,
        "X-Customer": customer_id,
        "X-Signature": make_signature(timestamp, method, uri, secret_key),
    }

    params = {
        "hintKeywords": keyword,
        "showDetail": "1"
    }

    url = "https://api.searchad.naver.com/keywordstool"

    response = requests.get(url, headers=headers, params=params, timeout=10)

    if response.status_code != 200:
        raise Exception(f"API 실패: {response.status_code} / {response.text}")

    return response.json().get("keywordList", [])
