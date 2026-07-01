import requests
import time
import hmac
import hashlib
import base64

def make_signature(timestamp, method, uri, secret_key):
    message = f"{timestamp}.{method}.{uri}"
    return base64.b64encode(
        hmac.new(secret_key.encode(), message.encode(), hashlib.sha256).digest()
    ).decode()

def get_keyword_data(keyword, customer_id, api_key, secret_key):
    uri = "/keywordstool"
    timestamp = str(int(time.time() * 1000))

    headers = {
        "X-Timestamp": timestamp,
        "X-API-KEY": api_key,
        "X-Customer": customer_id,
        "X-Signature": make_signature(timestamp, "GET", uri, secret_key)
    }

    params = {
        "hintKeywords": keyword,
        "showDetail": "1"
    }

    url = "https://api.searchad.naver.com/keywordstool"
    res = requests.get(url, headers=headers, params=params, timeout=10)
    res.raise_for_status()

    return res.json().get("keywordList", [])
