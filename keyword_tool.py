import tkinter as tk
from tkinter import messagebox, filedialog
import requests
import urllib.parse
from datetime import datetime


def get_autocomplete(keyword):
    url = f"https://ac.search.naver.com/nx/ac?q={urllib.parse.quote(keyword)}&st=100"
    try:
        res = requests.get(url, timeout=5)
        data = res.json()
        return [item[0] for item in data["items"][0]]
    except Exception:
        return []


def get_news_keywords():
    url = "https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko"
    try:
        res = requests.get(url, timeout=5)
        keywords = []
        for line in res.text.split("<title>")[1:]:
            text = line.split("</title>")[0]
            if 2 < len(text) < 50 and "Google 뉴스" not in text:
                keywords.append(text)
        return keywords
    except Exception:
        return []


def clean_keywords(data):
    unique = list(set(data))
    return sorted([k.strip() for k in unique if 2 < len(k.strip()) < 40])


def collect_keywords():
    result_box.delete("1.0", tk.END)
    status_label.config(text="키워드 수집 중...")

    seeds = seed_entry.get().strip()
    if not seeds:
        seeds = "대출,보험,연금,청약,세금"

    seed_list = [s.strip() for s in seeds.split(",") if s.strip()]
    all_keywords = []

    for seed in seed_list:
        all_keywords.extend(get_autocomplete(seed))

    all_keywords.extend(get_news_keywords())

    final = clean_keywords(all_keywords)

    for keyword in final:
        result_box.insert(tk.END, keyword + "\n")

    status_label.config(text=f"완료: {len(final)}개 키워드 수집")


def save_keywords():
    content = result_box.get("1.0", tk.END).strip()

    if not content:
        messagebox.showwarning("저장 실패", "저장할 키워드가 없습니다.")
        return

    default_name = f"keywords_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"

    file_path = filedialog.asksaveasfilename(
        defaultextension=".txt",
        initialfile=default_name,
        filetypes=[("Text files", "*.txt")]
    )

    if file_path:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        messagebox.showinfo("저장 완료", "키워드 파일이 저장되었습니다.")


root = tk.Tk()
root.title("v11 키워드 수집기")
root.geometry("700x550")

title_label = tk.Label(root, text="v11 이슈 키워드 수집기", font=("맑은 고딕", 16, "bold"))
title_label.pack(pady=10)

seed_label = tk.Label(root, text="시드 키워드 입력 (쉼표로 구분)")
seed_label.pack()

seed_entry = tk.Entry(root, width=80)
seed_entry.insert(0, "대출,보험,연금,청약,세금")
seed_entry.pack(pady=5)

button_frame = tk.Frame(root)
button_frame.pack(pady=10)

collect_button = tk.Button(button_frame, text="키워드 수집 시작", width=20, command=collect_keywords)
collect_button.grid(row=0, column=0, padx=5)

save_button = tk.Button(button_frame, text="TXT 저장", width=20, command=save_keywords)
save_button.grid(row=0, column=1, padx=5)

result_box = tk.Text(root, width=85, height=22)
result_box.pack(pady=10)

status_label = tk.Label(root, text="대기 중")
status_label.pack(pady=5)

root.mainloop()
