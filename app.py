import tkinter as tk
from tkinter import ttk, messagebox
from collector import collect_issue_keywords
from naver_api import get_keyword_data
from scorer import score_keyword
from title_engine import make_titles

def run_analysis():
    tree.delete(*tree.get_children())

    cid = customer_entry.get().strip()
    api = api_entry.get().strip()
    secret = secret_entry.get().strip()

    if not cid or not api or not secret:
        messagebox.showwarning("입력 필요", "네이버 API 정보를 입력하세요.")
        return

    status.config(text="이슈 키워드 수집 중...")
    root.update()

    issue_keywords = collect_issue_keywords()

    results = []

    error_count = 0

for kw in issue_keywords[:50]:
    try:
        data = get_keyword_data(kw, cid, api, secret)

        if not data:
            continue

        for item in data[:5]:
            scored = score_keyword(item, kw)
            results.append(scored)

    except Exception as e:
        error_count += 1
        print("API 오류:", kw, e)

if not results:
    messagebox.showwarning(
        "분석 결과 없음",
        f"분석 결과가 0개입니다.\n후보 키워드 수: {len(issue_keywords)}개\nAPI 오류 수: {error_count}개"
    )

    results.sort(key=lambda x: x["final_score"], reverse=True)

    for r in results[:50]:
        tree.insert("", "end", values=(
            r["keyword"],
            r["pc"],
            r["mobile"],
            r["competition"],
            r["issue_score"],
            r["profit_score"],
            r["final_score"],
            r["type"]
        ))

    status.config(text=f"완료: {len(results)}개 분석")

def show_titles():
    selected = tree.selection()
    if not selected:
        messagebox.showwarning("선택 필요", "키워드를 선택하세요.")
        return

    kw = tree.item(selected[0], "values")[0]
    search_titles, home_titles = make_titles(kw)

    title_box.delete("1.0", tk.END)
    title_box.insert(tk.END, "[검색용 제목 3개]\n")
    for i, t in enumerate(search_titles, 1):
        title_box.insert(tk.END, f"{i}. {t}\n")

    title_box.insert(tk.END, "\n[홈판용 제목 3개]\n")
    for i, t in enumerate(home_titles, 1):
        title_box.insert(tk.END, f"{i}. {t}\n")

root = tk.Tk()
root.title("v14 실시간 이슈 수익형 키워드 분석기")
root.geometry("1150x750")

tk.Label(root, text="v14 실시간 이슈 수익형 키워드 분석기", font=("맑은 고딕", 17, "bold")).pack(pady=10)

api_frame = tk.LabelFrame(root, text="네이버 검색광고 API")
api_frame.pack(fill="x", padx=15)

tk.Label(api_frame, text="CUSTOMER_ID").grid(row=0, column=0)
customer_entry = tk.Entry(api_frame, width=25)
customer_entry.grid(row=0, column=1, padx=5)

tk.Label(api_frame, text="API_KEY").grid(row=0, column=2)
api_entry = tk.Entry(api_frame, width=30, show="*")
api_entry.grid(row=0, column=3, padx=5)

tk.Label(api_frame, text="SECRET_KEY").grid(row=0, column=4)
secret_entry = tk.Entry(api_frame, width=35, show="*")
secret_entry.grid(row=0, column=5, padx=5)

btn_frame = tk.Frame(root)
btn_frame.pack(pady=10)

tk.Button(btn_frame, text="오늘 이슈+수익 키워드 분석", command=run_analysis, width=30).pack(side="left", padx=5)
tk.Button(btn_frame, text="제목 생성", command=show_titles, width=20).pack(side="left", padx=5)

cols = ("keyword", "pc", "mobile", "competition", "issue", "profit", "final", "type")
tree = ttk.Treeview(root, columns=cols, show="headings", height=20)

headers = {
    "keyword": "키워드",
    "pc": "PC",
    "mobile": "모바일",
    "competition": "경쟁도",
    "issue": "이슈점수",
    "profit": "수익점수",
    "final": "최종점수",
    "type": "유형"
}

for c in cols:
    tree.heading(c, text=headers[c])
    tree.column(c, width=130)

tree.column("keyword", width=300)
tree.pack(fill="both", expand=True, padx=15, pady=10)

title_box = tk.Text(root, height=9)
title_box.pack(fill="x", padx=15)

status = tk.Label(root, text="대기 중")
status.pack(pady=5)

root.mainloop()
