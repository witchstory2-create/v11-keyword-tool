import tkinter as tk
from tkinter import ttk, messagebox
import requests, urllib.parse, time, hmac, hashlib, base64

def make_signature(timestamp, method, uri, secret_key):
    msg = f"{timestamp}.{method}.{uri}"
    return base64.b64encode(
        hmac.new(secret_key.encode(), msg.encode(), hashlib.sha256).digest()
    ).decode()

def get_naver_keyword_data(keyword, customer_id, api_key, secret_key):
    uri = "/keywordstool"
    timestamp = str(int(time.time() * 1000))
    signature = make_signature(timestamp, "GET", uri, secret_key)

    headers = {
        "X-Timestamp": timestamp,
        "X-API-KEY": api_key,
        "X-Customer": customer_id,
        "X-Signature": signature,
    }

    params = {"hintKeywords": keyword, "showDetail": "1"}
    url = "https://api.searchad.naver.com/keywordstool"

    res = requests.get(url, headers=headers, params=params, timeout=10)
    res.raise_for_status()
    return res.json().get("keywordList", [])

def score_keyword(item):
    pc = int(str(item.get("monthlyPcQcCnt", "0")).replace("< ", "").replace(",", "") or 0)
    mobile = int(str(item.get("monthlyMobileQcCnt", "0")).replace("< ", "").replace(",", "") or 0)
    total = pc + mobile
    comp = item.get("compIdx", "LOW")

    search_score = min(total / 1000 * 10, 40)

    comp_score = {"LOW": 20, "MID": 12, "HIGH": 5}.get(comp, 8)

    keyword = item.get("relKeyword", "")
    money_words = ["대출", "보험", "연금", "청약", "세금", "환급", "카드", "자동차보험", "건강보험"]
    money_score = 30 if any(w in keyword for w in money_words) else 10

    safe_bad = ["추천", "가입", "신청", "무조건", "100%", "수익"]
    safety_score = 0 if any(w in keyword for w in safe_bad) else 10

    return round(search_score + comp_score + money_score + safety_score, 1)

def make_titles(keyword):
    return [
        f"{keyword} 기준과 조건, 헷갈리는 부분 정리",
        f"{keyword} 비교할 때 꼭 확인해야 할 핵심 포인트",
        f"{keyword} 처음 알아본다면 놓치기 쉬운 내용",
    ]

def run_search():
    tree.delete(*tree.get_children())

    customer_id = customer_entry.get().strip()
    api_key = api_entry.get().strip()
    secret_key = secret_entry.get().strip()
    seed = seed_entry.get().strip()

    if not customer_id or not api_key or not secret_key:
        messagebox.showwarning("입력 필요", "네이버 검색광고 API 정보를 입력하세요.")
        return

    if not seed:
        messagebox.showwarning("입력 필요", "시드 키워드를 입력하세요.")
        return

    try:
        data = get_naver_keyword_data(seed, customer_id, api_key, secret_key)
        rows = []

        for item in data:
            keyword = item.get("relKeyword", "")
            pc = item.get("monthlyPcQcCnt", "0")
            mobile = item.get("monthlyMobileQcCnt", "0")
            comp = item.get("compIdx", "")
            score = score_keyword(item)
            rows.append((score, keyword, pc, mobile, comp))

        rows.sort(reverse=True)

        for score, keyword, pc, mobile, comp in rows[:50]:
            tree.insert("", "end", values=(keyword, pc, mobile, comp, score))

        status_label.config(text=f"완료: {len(rows)}개 키워드 분석")

    except Exception as e:
        messagebox.showerror("오류", str(e))

def show_titles():
    selected = tree.selection()
    if not selected:
        messagebox.showwarning("선택 필요", "키워드를 하나 선택하세요.")
        return

    values = tree.item(selected[0], "values")
    keyword = values[0]

    title_box.delete("1.0", tk.END)
    title_box.insert(tk.END, f"키워드: {keyword}\n\n")

    for i, title in enumerate(make_titles(keyword), 1):
        title_box.insert(tk.END, f"{i}. {title}\n")

root = tk.Tk()
root.title("v13 네이버 API 수익형 키워드 분석기")
root.geometry("980x700")

tk.Label(root, text="v13 네이버 API 수익형 키워드 분석기", font=("맑은 고딕", 17, "bold")).pack(pady=10)

api_frame = tk.LabelFrame(root, text="네이버 검색광고 API 정보")
api_frame.pack(fill="x", padx=15, pady=5)

tk.Label(api_frame, text="CUSTOMER_ID").grid(row=0, column=0, padx=5, pady=5)
customer_entry = tk.Entry(api_frame, width=25)
customer_entry.grid(row=0, column=1, padx=5)

tk.Label(api_frame, text="API_KEY").grid(row=0, column=2, padx=5)
api_entry = tk.Entry(api_frame, width=30)
api_entry.grid(row=0, column=3, padx=5)

tk.Label(api_frame, text="SECRET_KEY").grid(row=0, column=4, padx=5)
secret_entry = tk.Entry(api_frame, width=35, show="*")
secret_entry.grid(row=0, column=5, padx=5)

search_frame = tk.Frame(root)
search_frame.pack(fill="x", padx=15, pady=10)

tk.Label(search_frame, text="시드 키워드").pack(side="left")
seed_entry = tk.Entry(search_frame, width=40)
seed_entry.insert(0, "연금")
seed_entry.pack(side="left", padx=8)

tk.Button(search_frame, text="키워드 분석 시작", command=run_search, width=18).pack(side="left", padx=5)
tk.Button(search_frame, text="제목 생성", command=show_titles, width=15).pack(side="left", padx=5)

columns = ("keyword", "pc", "mobile", "competition", "score")
tree = ttk.Treeview(root, columns=columns, show="headings", height=18)

tree.heading("keyword", text="키워드")
tree.heading("pc", text="PC 검색량")
tree.heading("mobile", text="모바일 검색량")
tree.heading("competition", text="경쟁도")
tree.heading("score", text="수익 점수")

tree.column("keyword", width=300)
tree.column("pc", width=120)
tree.column("mobile", width=120)
tree.column("competition", width=100)
tree.column("score", width=100)

tree.pack(fill="both", expand=True, padx=15, pady=10)

title_box = tk.Text(root, height=7)
title_box.pack(fill="x", padx=15, pady=5)

status_label = tk.Label(root, text="대기 중")
status_label.pack(pady=5)

root.mainloop()
