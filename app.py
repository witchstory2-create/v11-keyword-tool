# app.py
# v16.7 - 이슈 수익형 블로그 글 추천기 (광고 경쟁도 배율 / 실질 수익 추정 + 글작성순위 반영)
# 변경 사항(2026-07):
#   - 좌측 표 맨 앞에 "작성순위" 컬럼 추가: 전체 분석 결과를 writing_priority_score 기준으로
#     재정렬해서 "지금 뭘 먼저 써야 하는지"를 숫자로 바로 보여줌.
#   - TOP5 작성 큐 헤더에 기존 "★★★ 어려움" 대신 "글작성순위 N위 · 안내문구"를 함께 표시.
#   - 상세보기 패널에도 동일한 안내문구를 표시해서, 화면에 뜬 제목을 그대로 넘기기 전에
#     "오늘 써야 하는지 / 여유있게 써도 되는지"를 바로 판단할 수 있게 함.

import csv
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from collector import collect_issue_keywords
from naver_api import get_keyword_data
from scorer import score_keyword, filter_top5
from title_engine import make_titles
from outline_engine import generate_outline
from gpt_writer import write_draft
from queue_manager import save_queue, load_queue
from config_manager import save_config, load_config


analysis_results = []
queue_items = []
keyword_lookup = {}  # [NEW] 키워드 -> 전체 분석 데이터(우선순위/안내문구 포함) 빠른 조회용


# ---------------------- 설정 저장/불러오기 ----------------------

def load_saved_config():
    cfg = load_config()
    customer_entry.insert(0, cfg.get("customer_id", ""))
    api_entry.insert(0, cfg.get("api_key", ""))
    secret_entry.insert(0, cfg.get("secret_key", ""))
    gpt_key_entry.insert(0, cfg.get("gemini_key", ""))


def save_current_config():
    save_config({
        "customer_id": customer_entry.get().strip(),
        "api_key": api_entry.get().strip(),
        "secret_key": secret_entry.get().strip(),
        "gemini_key": gpt_key_entry.get().strip(),
    })
    status.config(text="API 키가 저장되었습니다. 다음 실행부터 자동으로 입력됩니다.")


def on_close():
    save_current_config()
    root.destroy()


# ---------------------- 분석 로직 ----------------------

def run_analysis_thread():
    analyze_button.config(state="disabled")
    threading.Thread(target=run_analysis, daemon=True).start()


def run_analysis():
    global analysis_results

    cid = customer_entry.get().strip()
    api = api_entry.get().strip()
    secret = secret_entry.get().strip()

    if not cid or not api or not secret:
        root.after(0, lambda: messagebox.showwarning("입력 필요", "네이버 API 정보를 입력하세요."))
        root.after(0, lambda: analyze_button.config(state="normal"))
        return

    root.after(0, save_current_config)
    root.after(0, lambda: status.config(text="이슈 키워드 수집 중..."))

    issue_candidates = collect_issue_keywords()

    if not issue_candidates:
        root.after(0, lambda: messagebox.showwarning("수집 실패", "이슈 키워드를 수집하지 못했습니다."))
        root.after(0, lambda: status.config(text="이슈 키워드 수집 실패"))
        root.after(0, lambda: analyze_button.config(state="normal"))
        return

    total = min(len(issue_candidates), 150)
    root.after(0, lambda: progress.config(maximum=total, value=0))
    root.after(0, lambda: status.config(text=f"후보 키워드 {total}개, 네이버 API 분석 중..."))

    results = []
    error_count = 0

    for idx, meta in enumerate(issue_candidates[:150]):
        kw = meta["keyword"]
        try:
            data = get_keyword_data(kw, cid, api, secret)
            if data:
                for item in data[:5]:
                    # meta: collector.py가 만든 후보 정보 (첫 번째)
                    # item: 네이버 API가 돌려준 검색량/경쟁도/광고깊이/CTR 정보 (두 번째)
                    scored = score_keyword(meta, item)
                    results.append(scored)
        except Exception as e:
            error_count += 1
            print("API 오류:", kw, e)

        root.after(0, lambda i=idx: progress.config(value=i + 1))

    if not results:
        root.after(0, lambda: messagebox.showwarning(
            "분석 결과 없음",
            f"분석 결과가 0개입니다.\n후보 키워드 수: {len(issue_candidates)}개\nAPI 오류 수: {error_count}개"
        ))
        root.after(0, lambda: status.config(text="분석 결과 없음"))
        root.after(0, lambda: analyze_button.config(state="normal"))
        return

    seen = set()
    unique_results = []
    for r in results:
        if r["keyword"] not in seen:
            seen.add(r["keyword"])
            unique_results.append(r)

    analysis_results = unique_results

    def finalize():
        global keyword_lookup

        tree.delete(*tree.get_children())

        # [NEW] "지금 뭘 먼저 써야 하는지" 기준으로 재정렬해서 작성순위를 매김
        priority_sorted = sorted(unique_results, key=lambda x: x["writing_priority_score"], reverse=True)

        keyword_lookup = {}
        for rank, r in enumerate(priority_sorted, 1):
            r["priority_rank"] = rank  # 원본 dict에 순위 저장 (CSV/큐/상세보기에서 재사용)
            keyword_lookup[r["keyword"]] = r

            depth_display = f"x{r['ad_depth_multiplier']}" + (" ⭐" if r.get("low_search_high_value") else "")
            priority_display = f"{rank}위"
            tree.insert("", "end", values=(
                priority_display, r["keyword"], r["pc"], r["mobile"], r["competition"], depth_display,
                r["issue_score"], r["profit_score"], f"{r['estimated_revenue_krw']:,.0f}",
                r["final_score"], r["type"], r["difficulty"]
            ))

        status.config(text=f"완료: {len(unique_results)}개 분석 / API 오류 {error_count}개 "
                            f"(1위=지금 가장 먼저 써야 할 키워드)")
        # 비수익 카테고리는 제외하고, 글작성순위(writing_priority_score) 기준 진짜 TOP5만 큐에 반영
        build_queue(filter_top5(unique_results))
        analyze_button.config(state="normal")

    root.after(0, finalize)


# ---------------------- 제목/개요 상세 보기 ----------------------

def show_titles():
    selected = tree.selection()
    if not selected:
        messagebox.showwarning("선택 필요", "키워드를 선택하세요.")
        return

    values = tree.item(selected[0], "values")
    # 컬럼 순서: priority(0) keyword(1) pc(2) mobile(3) competition(4) addepth(5)
    #            issue(6) profit(7) revenue(8) final(9) type(10) difficulty(11)
    keyword = values[1]
    difficulty = values[11] if len(values) > 11 else "보통"

    # [NEW] keyword_lookup에서 전체 데이터(작성순위/안내문구 포함)를 가져와서 상세보기에 반영
    full_data = keyword_lookup.get(keyword, {})
    render_keyword_detail(keyword, difficulty, full_data)


def render_keyword_detail(keyword, difficulty="보통", full_data=None):
    full_data = full_data or {}
    search_titles, home_titles = make_titles(keyword)
    outline = generate_outline(keyword)

    priority_rank = full_data.get("priority_rank")
    guidance = full_data.get("writing_guidance", "")
    revenue = full_data.get("estimated_revenue_krw")

    detail_box.delete("1.0", tk.END)
    detail_box.insert(tk.END, f"키워드: {keyword} (난이도: {difficulty})\n")

    if priority_rank:
        detail_box.insert(
            tk.END,
            f"▶ 글작성순위 {priority_rank}위 · {guidance}\n"
        )
    if revenue is not None:
        detail_box.insert(tk.END, f"▶ 예상 월수익(추정): 약 {revenue:,.0f}원\n")
    detail_box.insert(tk.END, "\n")

    detail_box.insert(tk.END, "[검색용 제목 3개]\n")
    for i, t in enumerate(search_titles, 1):
        detail_box.insert(tk.END, f"{i}. {t}\n")
    detail_box.insert(tk.END, "\n[홈판용 제목 3개]\n")
    for i, t in enumerate(home_titles, 1):
        detail_box.insert(tk.END, f"{i}. {t}\n")
    detail_box.insert(tk.END, "\n[글 개요]\n" + outline["intro"] + "\n")
    detail_box.insert(tk.END, "\n".join(outline["sections"]) + "\n")
    detail_box.insert(tk.END, "\n[FAQ 항목]\n" + "\n".join(outline["faq"]) + "\n")
    detail_box.insert(tk.END, "\n[태그 추천]\n" + ", ".join(outline["tag_suggestions"]) + "\n")

    gpt_button.config(state="normal", command=lambda: run_draft_writing(keyword, outline, difficulty))


def run_draft_writing(keyword, outline, difficulty):
    api_key = gpt_key_entry.get().strip() or None
    status.config(text="글 초안 작성 중...")
    root.update()
    draft = write_draft(keyword, outline, api_key, difficulty)
    detail_box.insert(tk.END, "\n" + "=" * 40 + "\n[완성 글 초안 - 그대로 복사해서 사용 가능]\n" + "=" * 40 + "\n\n")
    detail_box.insert(tk.END, draft + "\n")
    status.config(text="글 초안 작성 완료. 클립보드 복사 버튼으로 바로 복사할 수 있습니다.")


# ---------------------- 오늘의 TOP5 작성 큐 ----------------------

def build_queue(top5):
    global queue_items
    queue_items = []
    for idx, item in enumerate(top5, 1):
        queue_items.append({
            "rank": idx,
            "keyword": item["keyword"],
            "final_score": item["final_score"],
            "difficulty": item["difficulty"],
            "estimated_revenue_krw": item["estimated_revenue_krw"],
            "ad_depth_multiplier": item["ad_depth_multiplier"],
            "low_search_high_value": item["low_search_high_value"],
            "reason": item["reasons"],
            "status": "미작성",
            # [NEW] 글작성순위/안내문구 큐에도 함께 저장
            "writing_guidance": item.get("writing_guidance", ""),
            "type_code": item.get("type_code", "RECURRING_PROFIT"),
        })
    save_queue(queue_items)
    render_queue()


def render_queue():
    for widget in queue_inner.winfo_children():
        widget.destroy()

    if not queue_items:
        tk.Label(queue_inner, text="분석을 실행하면 오늘의 TOP5 작성 큐가 표시됩니다.").pack(pady=10)
        return

    for q in queue_items:
        row = tk.Frame(queue_inner, relief="groove", borderwidth=1)
        row.pack(fill="x", pady=4, padx=4)

        status_icon = {"미작성": "☐", "작성완료": "☑", "발행완료": "✅"}.get(q["status"], "☐")
        hidden_gem = " ⭐숨은고수익" if q.get("low_search_high_value") else ""
        header = (f"{q['rank']}위  {q['keyword']}   난이도: {q['difficulty']}   "
                   f"예상수익: {q['estimated_revenue_krw']:,.0f}원{hidden_gem}   "
                   f"{status_icon} {q['status']}")
        tk.Label(row, text=header, font=("맑은 고딕", 11, "bold"), anchor="w",
                 wraplength=560, justify="left").pack(fill="x", padx=6, pady=2)

        # [NEW] "글작성순위 N위 · 오늘 작성 권장 (...)" 형태의 안내문구를 눈에 띄게 표시
        guidance_text = q.get("writing_guidance", "")
        if guidance_text:
            color = "#d35400" if q.get("type_code") == "HOT_ISSUE" else "#2c3e50"
            tk.Label(row, text=f"▶ 글작성순위 {q['rank']}위 · {guidance_text}",
                     font=("맑은 고딕", 10, "bold"), fg=color, anchor="w",
                     wraplength=560, justify="left").pack(fill="x", padx=6, pady=(0, 2))

        reason_text = "   ".join(f"✔ {r}" for r in q["reason"])
        tk.Label(row, text=reason_text, fg="gray20", anchor="w",
                 wraplength=520, justify="left").pack(fill="x", padx=6)

        btns = tk.Frame(row)
        btns.pack(fill="x", pady=3)
        tk.Button(btns, text="작성 시작", command=lambda q=q: render_keyword_detail(
            q["keyword"], q["difficulty"], keyword_lookup.get(q["keyword"], {})
        )).pack(side="left", padx=4)
        tk.Button(btns, text="작성완료 표시", command=lambda q=q: mark_status(q, "작성완료")).pack(side="left", padx=4)
        tk.Button(btns, text="발행완료 표시", command=lambda q=q: mark_status(q, "발행완료")).pack(side="left", padx=4)

    queue_canvas.update_idletasks()
    queue_canvas.config(scrollregion=queue_canvas.bbox("all"))


def mark_status(q, new_status):
    q["status"] = new_status
    save_queue(queue_items)
    render_queue()


# ---------------------- 내보내기 ----------------------

def copy_to_clipboard():
    root.clipboard_clear()
    root.clipboard_append(detail_box.get("1.0", tk.END))
    status.config(text="클립보드에 복사되었습니다.")


def save_as_txt():
    path = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Text", "*.txt")])
    if path:
        with open(path, "w", encoding="utf-8") as f:
            f.write(detail_box.get("1.0", tk.END))
        status.config(text=f"TXT 저장 완료: {path}")


def save_as_csv():
    if not analysis_results:
        messagebox.showwarning("데이터 없음", "먼저 분석을 실행하세요.")
        return
    path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
    if not path:
        return

    # [NEW] 작성순위 기준으로 정렬해서 저장 (분석 표와 동일한 순서)
    ordered = sorted(analysis_results, key=lambda x: x.get("priority_rank", 999))

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["작성순위", "키워드", "PC", "모바일", "경쟁도", "광고배율", "이슈점수", "수익점수",
                          "예상수익(원)", "최종점수", "유형", "난이도", "숨은고수익여부", "작성가이드"])
        for r in ordered:
            writer.writerow([r.get("priority_rank", ""), r["keyword"], r["pc"], r["mobile"], r["competition"],
                              f"x{r['ad_depth_multiplier']}",
                              r["issue_score"], r["profit_score"],
                              f"{r['estimated_revenue_krw']:,.0f}",
                              r["final_score"], r["type"], r["difficulty"],
                              "예" if r.get("low_search_high_value") else "",
                              r.get("writing_guidance", "")])
    status.config(text=f"CSV 저장 완료: {path}")


# ---------------------- UI 구성 ----------------------

root = tk.Tk()
root.title("v16 이슈 수익형 블로그 글 추천기")
root.geometry("1500x850")

title_label = tk.Label(root, text="v16 이슈 수익형 블로그 글 추천기", font=("맑은 고딕", 17, "bold"))
title_label.pack(pady=8)

api_frame = tk.LabelFrame(root, text="네이버 검색광고 API / Gemini 설정")
api_frame.pack(fill="x", padx=15, pady=5)

tk.Label(api_frame, text="CUSTOMER_ID").grid(row=0, column=0, padx=5, pady=5)
customer_entry = tk.Entry(api_frame, width=20)
customer_entry.grid(row=0, column=1, padx=5, pady=5)

tk.Label(api_frame, text="API_KEY").grid(row=0, column=2, padx=5, pady=5)
api_entry = tk.Entry(api_frame, width=30, show="*")
api_entry.grid(row=0, column=3, padx=5, pady=5)

tk.Label(api_frame, text="SECRET_KEY").grid(row=0, column=4, padx=5, pady=5)
secret_entry = tk.Entry(api_frame, width=35, show="*")
secret_entry.grid(row=0, column=5, padx=5, pady=5)

tk.Label(api_frame, text="Gemini API KEY (선택, 무료 발급 가능)").grid(row=1, column=0, padx=5, pady=5)
gpt_key_entry = tk.Entry(api_frame, width=45, show="*")
gpt_key_entry.grid(row=1, column=1, columnspan=3, padx=5, pady=5, sticky="w")

tk.Button(api_frame, text="API 키 저장", command=save_current_config, width=12).grid(row=1, column=4, padx=5, pady=5)

tk.Label(
    api_frame,
    text="※ 한 번 저장하면 다음 실행부터 자동으로 입력됩니다. (같은 폴더의 config.json에 저장, GitHub에는 올리지 마세요)",
    fg="gray40", font=("맑은 고딕", 8)
).grid(row=2, column=0, columnspan=5, padx=5, pady=2, sticky="w")

button_frame = tk.Frame(root)
button_frame.pack(pady=8, fill="x", padx=15)

analyze_button = tk.Button(button_frame, text="오늘 이슈+수익 키워드 분석", command=run_analysis_thread, width=26)
analyze_button.pack(side="left", padx=5)

title_button = tk.Button(button_frame, text="선택 키워드 상세 보기", command=show_titles, width=20)
title_button.pack(side="left", padx=5)

gpt_button = tk.Button(button_frame, text="글 초안 생성", state="disabled", width=15)
gpt_button.pack(side="left", padx=5)

tk.Button(button_frame, text="클립보드 복사", command=copy_to_clipboard, width=14).pack(side="left", padx=5)
tk.Button(button_frame, text="TXT 저장", command=save_as_txt, width=12).pack(side="left", padx=5)
tk.Button(button_frame, text="CSV 저장", command=save_as_csv, width=12).pack(side="left", padx=5)

progress = ttk.Progressbar(root, length=400, mode="determinate")
progress.pack(pady=3)

main_pane = ttk.PanedWindow(root, orient="horizontal")
main_pane.pack(fill="both", expand=True, padx=15, pady=8)

left_frame = tk.Frame(main_pane)
main_pane.add(left_frame, weight=3)

# [CHANGED] "priority" 컬럼을 맨 앞에 추가
columns = ("priority", "keyword", "pc", "mobile", "competition", "addepth",
           "issue", "profit", "revenue", "final", "type", "difficulty")
tree = ttk.Treeview(left_frame, columns=columns, show="headings", height=25)

headers = {
    "priority": "작성순위", "keyword": "키워드", "pc": "PC", "mobile": "모바일", "competition": "경쟁도",
    "addepth": "광고배율", "issue": "이슈점수", "profit": "수익점수",
    "revenue": "예상수익(원)", "final": "최종점수", "type": "유형", "difficulty": "난이도"
}
for col in columns:
    tree.heading(col, text=headers[col])
    tree.column(col, width=80)
tree.column("priority", width=70, anchor="center")
tree.column("keyword", width=180)
tree.column("type", width=100)
tree.column("revenue", width=95)
tree.column("addepth", width=85)

tree.pack(fill="both", expand=True)
tree.bind("<<TreeviewSelect>>", lambda e: show_titles())

right_pane = ttk.PanedWindow(main_pane, orient="vertical")
main_pane.add(right_pane, weight=4)

queue_outer = tk.LabelFrame(right_pane, text="오늘의 TOP 5 작성 큐 (글작성순위 기준, ⭐=숨은 고수익 키워드)")
right_pane.add(queue_outer, weight=1)

queue_canvas = tk.Canvas(queue_outer, height=280)
queue_scrollbar = tk.Scrollbar(queue_outer, orient="vertical", command=queue_canvas.yview)
queue_inner = tk.Frame(queue_canvas)

queue_inner.bind("<Configure>", lambda e: queue_canvas.config(scrollregion=queue_canvas.bbox("all")))
queue_canvas.create_window((0, 0), window=queue_inner, anchor="nw")
queue_canvas.config(yscrollcommand=queue_scrollbar.set)

queue_canvas.pack(side="left", fill="both", expand=True)
queue_scrollbar.pack(side="right", fill="y")

detail_frame = tk.LabelFrame(right_pane, text="선택 키워드 상세 (제목 / 개요 / FAQ / 완성 글 초안)")
right_pane.add(detail_frame, weight=2)

detail_box = tk.Text(detail_frame, wrap="word")
detail_box.pack(fill="both", expand=True, padx=4, pady=4)

status = tk.Label(root, text="대기 중")
status.pack(pady=5)


# ---------------------- 시작 시 설정/큐 복원 ----------------------

load_saved_config()

restored = load_queue()
if restored:
    queue_items = restored
render_queue()

root.protocol("WM_DELETE_WINDOW", on_close)
root.mainloop()
