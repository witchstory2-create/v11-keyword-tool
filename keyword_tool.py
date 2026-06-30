import requests
import urllib.parse
from datetime import datetime

def get_autocomplete(keyword):
    url = f"https://ac.search.naver.com/nx/ac?q={urllib.parse.quote(keyword)}&st=100"
    try:
        res = requests.get(url, timeout=5)
        data = res.json()
        return [item[0] for item in data['items'][0]]
    except:
        return []

def get_news_keywords():
    url = "https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko"
    res = requests.get(url, timeout=5)

    keywords = []
    for line in res.text.split("<title>")[1:]:
        text = line.split("</title>")[0]
        if len(text) < 50:
            keywords.append(text)

    return keywords

def run():
    seed = ["대출", "보험", "연금", "청약", "세금"]

    all_keywords = []

    for s in seed:
        all_keywords += get_autocomplete(s)

    all_keywords += get_news_keywords()

    final = list(set([k for k in all_keywords if 2 < len(k) < 30]))

    filename = f"keywords_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"

    with open(filename, "w", encoding="utf-8") as f:
        for k in final:
            f.write(k + "\n")

    print("완료:", len(final))

if __name__ == "__main__":
    run()
