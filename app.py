# app.py
# v16.8 - 이슈 수익형 블로그 글 추천기 (검색어트렌드 급증 검증 반영)
# 변경 사항(2026-07):
#   1) 데이터랩 검색어트렌드(Client ID/Secret) 입력칸 추가.
#      값을 넣으면 1차 스코어링 후 상위 20개 후보만 추가로 데이터랩 API를 호출해서
#      "뉴스 언급은 많지만 실제 검색량은 평소와 비슷한" 상시성 키워드를 걸러내고,
#      진짜 검색량이 급증한 키워드만 HOT 이슈로 남깁니다.
#      비워두면 트렌드 검증 없이 기존처럼 동작합니다 (필수 아님).
#   2) 좌측 표에 "검증" 컬럼 추가: 🔥(실제 급증 확인) / ℹ(상시성으로 재분류) / -(미검증)
#   3) TOP5 큐, 상세보기에도 검증 결과 문구 표시.

import csv
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from collector import collect_issue_keywords
from naver_api import get_keyword_data
from naver_trend_api import get_search_trend
from scorer import score_keyword, filter_top5, recheck_with_trend
from title_engine import make_titles
from outline_engine import generate_outline
from gpt_writer import write_draft
from queue_manager import save_queue, load_queue
from config_manager import save_config, load_config


analysis_results = []
queue_items = []
keyword_lookup = {}  # 키워드 -> 전체 분석 데이터(우선순위/안내문구/트렌드검증 포함) 빠른 조회용

TREND_RECHECK_TOP_N = 20  # 데이터랩 API 호출 제한을 고려해 상위 N개만 재검증


# ---------------------- 설정 저장/불러오기 ----------------------

def load_saved_config():
    cfg = load_config()
    customer_entry.insert(0, cfg.get("customer_id", ""))
    api_entry.insert(0, cfg.get("api_key", ""))
    secret_entry.insert(0, cfg.get("secret_key", ""))
    gpt_key_entry.insert(0, cfg.get("gemini_key", ""))
    datalab_id_entry.insert(0, cfg.get("datalab_client_id", ""))
    datalab_secret_entry.insert(0, cfg.get("datalab_client_secret", ""))


def save_current_config():
    save_config({
        "customer_id": customer_entry.get().strip(),
        "api_key": api_entry.get().strip(),
        "secret_key": secret_entry.get().strip(),
        "gemini_key": gpt_key_entry.get().strip(),
        "datalab_client_id": datalab_id_entry.get().strip(),
        "datalab_client_secret": datalab_secret_entry.get().strip(),
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

    # ---------------------- [NEW] 상위 후보만 데이터랩 검색어트렌드로 재검증 ----------------------
    datalab_id = datalab_id_entry.get().strip()
    datalab_secret = datalab_secret_entry.get().strip()
    trend_checked_count = 0

    if datalab_id and datalab_secret:
        root.after(0, lambda: status.config(text="상위 키워드 실제 검색 급증 여부 확인 중..."))

        # HOT 이슈로 분류된 것 + final_score 상위 항목을 우선 재검증 대상으로 선정
        unique_results.sort(key=lambda x: x["final_score"], reverse=True)
        recheck_targets = unique_results[:TREND_RECHECK_TOP_N]

        for r in recheck_targets:
            try:
                trend = get_search_trend(r["keyword"], datalab_id, datalab_secret)
                recheck_with_trend(r, trend)
                if trend.get("trend_available"):
                    trend_checked_count += 1
            except Exception as e:
                print("데이터랩 API 오류:", r["keyword"], e)

    analysis_results = unique_results

    def finalize():
        global keyword_lookup

        tree.delete(*tree.get_children())

        priority_sorted = sorted(unique_results, key=lambda x: x["writing_priority_score"], reverse=True)

        keyword_lookup = {}
        for rank, r in enumerate(priority_sorted, 1):
            r["priority_rank"] = rank
            keyword_lookup[r["keyword"]] = r

            depth_display = f"x{r['ad_depth_multiplier']}" + (" ⭐" if r.get("low_search_high_value") else "")
            priority_display = f"{rank}위"

            # [NEW] 검증 컬럼: 🔥(실제 급증 확인) / ℹ(상시성 재분류) / -(미검증)
            if r.get("trend_checked"):
                spike = r.get("spike_ratio") or 1.0
                verify_display = f"🔥x{spike}" if spike >= 2.0 else f"ℹ x{spike}"
            else:
                verify_display = "-"

            tree.insert("", "end", values=(
                priority_display, r["keyword"], r["pc"], r["mobile"], r["competition"], depth_display,
                verify_display,
                r["issue_score"], r["profit_score"], f"{r['estimated_revenue_krw']:,.0f}",
                r["final_score"], r["type"], r["difficulty"]
            ))

        trend_note = f" / 검증 완료 {trend_checked_count}개" if (datalab_id and datalab_secret) else " / 트렌드 미검증(데이터랩 키 미입력)"
        status.config(text=f"완료: {len(unique_results)}개 분석 / API 오류 {error_count}개{trend_note}")

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
    # 컬럼 순서: priority(0) keyword(1) pc(2) mobile(3) competition(4) addepth(5) verify(6)
    #            issue(7) profit(8) revenue(9) final(10) type(11) difficulty(12)
    keyword = values[1]
    difficulty = values[12] if len(values) > 12 else "보통"

    full_data = keyword_lookup.get(keyword, {})
    render_keyword_detail(keyword, difficulty, full_data)


def render_keyword_detail(keyword, difficulty="보통", full_data=None):
    full_data = full_data or {}
    search_titles, home_titles = make_titles(keyword)
    outline = generate_outline(keyword)

    priority_rank = full_data.get("priority_rank")
    guidance = full_data.get("writing_guidance", "")
    revenue = full_data.get("estimated_revenue_krw")
    trend_checked = full_data.get("trend_checked", False)
    spike_ratio = full_data.get("spike_ratio")

    detail_box.delete("1.0", tk.END)
    detail_box.insert(tk.END, f"키워드: {keyword} (난이도: {difficulty})\n")

    if priority_rank:
        detail_box.insert(tk.END, f"▶ 글작성순위 {priority_rank}위 · {guidance}\n")
    if revenue is not None:
        detail_box.insert(tk.END, f"▶ 예상 월수익(추정): 약 {revenue:,.0f}원\n")

    # [NEW] 트렌드 검증 결과 표시
    if trend_checked and spike_ratio is not None:
        if spike_ratio >= 2.0:
            detail_box.insert(tk.END, f"▶ 🔥 실제 검색량 급증 확인 (평소 대비 x{spike_ratio}) - 실시간 이슈 가능성 높음\n")
        else:
            detail_box.insert(tk.END, f"▶ ℹ 실제 검색량은 평소와 비슷함 (x{spike_ratio}) - 상시성 키워드일 가능성, 급하게 쓸 필요 없음\n")
    else:
        detail_box.insert(tk.END, "▶ ⚠ 검색어트렌드 미검증 - 실제 뉴스 검색으로 한 번 확인 권장\n")

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
            "writing_guidance": item.get("writing_guidance", ""),
            "type_code": item.get("type_code", "RECURRING_PROFIT"),
            # [NEW]
            "trend_checked": item.get("trend_checked", False),
            "spike_ratio": item.get("spike_ratio"),
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

        guidance_text = q.get("writing_guidance", "")
        if guidance_text:
            color = "#d35400" if q.get("type_code") == "HOT_ISSUE" else "#2c3e50"
            tk.Label(row, text=f"▶ 글작성순위 {q['rank']}위 · {guidance_text}",
                     font=("맑은 고딕", 10, "bold"), fg=color, anchor="w",
                     wraplength=560, justify="left").pack(fill="x", padx=6, pady=(0, 2))

        # [NEW] 트렌드 검증 결과 한 줄 표시
        if q.get("trend_checked") and q.get("spike_ratio") is not None:
            spike = q["spike_ratio"]
            if spike >= 2.0:
                trend_text = f"🔥 실제 검색량 급증 확인 (평소 대비 x{spike})"
                trend_color = "#c0392b"
            else:
                trend_text = f"ℹ 검색량은 평소와 비슷함 (x{spike}) - 급하게 쓸 필요 없음"
                trend_color = "gray30"
            tk.Label(row, text=trend_text, fg=trend_color, anchor="w",
                     wraplength=560, justify="left").pack(fill="x", padx=6, pady=(0, 2))
        else:
            tk.Label(row, text="⚠ 검색어트렌드 미검증 - 실제 뉴스 검색으로 확인 권장", fg="#7f8c8d",
                     anchor="w", wraplength=560, justify="left").pack(fill="x", padx=6, pady=(0, 2))

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

    ordered = sorted(analysis_results, key=lambda x: x.get("priority_rank", 999))

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["작성순위", "키워드", "PC", "모바일", "경쟁도", "광고배율", "트렌드검증",
                          "이슈점수", "수익점수", "예상수익(원)", "최종점수", "유형", "난이도",
                          "숨은고수익여부", "작성가이드"])
        for r in ordered:
            if r.get("trend_checked"):
                verify = f"x{r.get('spike_ratio')}"
            else:
                verify = "미검증"
            writer.writerow([r.get("priority_rank", ""), r["keyword"], r["pc"], r["mobile"], r["competition"],
                              f"x{r['ad_depth_multiplier']}", verify,
                              r["issue_score"], r["profit_score"],
                              f"{r['estimated_revenue_krw']:,.0f}",
                              r["final_score"], r["type"], r["difficulty"],
                              "예" if r.get("low_search_high_value") else "",
                              r.get("writing_guidance", "")])
    status.config(text=f"CSV 저장 완료: {path}")


# ---------------------- UI 구성 ----------------------

root = tk.Tk()
root.title("v16 이슈 수익형 블로그 글 추천기")
root.geometry("1550x870")

title_label = tk.Label(root, text="v16 이슈 수익형 블로그 글 추천기", font=("맑은 고딕", 17, "bold"))
title_label.pack(pady=8)

api_frame = tk.LabelFrame(root, text="네이버 검색광고 API / 데이터랩 / Gemini 설정")
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

# [NEW] 데이터랩 검색어트렌드 Client ID/Secret 입력칸
tk.Label(api_frame, text="데이터랩 Client ID (선택)").grid(row=1, column=0, padx=5, pady=5)
datalab_id_entry = tk.Entry(api_frame, width=20)
datalab_id_entry.grid(row=1, column=1, padx=5, pady=5)

tk.Label(api_frame, text="데이터랩 Client Secret (선택)").grid(row=1, column=2, padx=5, pady=5)
datalab_secret_entry = tk.Entry(api_frame, width=30, show="*")
datalab_secret_entry.grid(row=1, column=3, padx=5, pady=5)

tk.Label(api_frame, text="Gemini API KEY (선택, 무료 발급 가능)").grid(row=2, column=0, padx=5, pady=5)
gpt_key_entry = tk.Entry(api_frame, width=45, show="*")
gpt_key_entry.grid(row=2, column=1, columnspan=3, padx=5, pady=5, sticky="w")

tk.Button(api_frame, text="API 키 저장", command=save_current_config, width=12).grid(row=2, column=4, padx=5, pady=5)

tk.Label(
    api_frame,
    text="※ 한 번 저장하면 다음 실행부터 자동으로 입력됩니다. (같은 폴더의 config.json에 저장, GitHub에는 올리지 마세요)\n"
         "※ 데이터랩 Client ID/Secret을 입력하면 상위 20개 키워드의 실제 검색 급증 여부를 추가로 검증합니다 (비워두면 검증 없이 진행).",
    fg="gray40", font=("맑은 고딕", 8), justify="left"
).grid(row=3, column=0, columnspan=6, padx=5, pady=2, sticky="w")

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

# [CHANGED] "verify"(검증) 컬럼 추가
columns = ("priority", "keyword", "pc", "mobile", "competition", "addepth", "verify",
           "issue", "profit", "revenue", "final", "type", "difficulty")
tree = ttk.Treeview(left_frame, columns=columns, show="headings", height=25)

headers = {
    "priority": "작성순위", "keyword": "키워드", "pc": "PC", "mobile": "모바일", "competition": "경쟁도",
    "addepth": "광고배율", "verify": "검증",
    "issue": "이슈점수", "profit": "수익점수",
    "revenue": "예상수익(원)", "final": "최종점수", "type": "유형", "difficulty": "난이도"
}
for col in columns:
    tree.heading(col, text=headers[col])
    tree.column(col, width=75)
tree.column("priority", width=65, anchor="center")
tree.column("keyword", width=170)
tree.column("type", width=100)
tree.column("revenue", width=90)
tree.column("addepth", width=75)
tree.column("verify", width=75)

tree.pack(fill="both", expand=True)
tree.bind("<<TreeviewSelect>>", lambda e: show_titles())

right_pane = ttk.PanedWindow(main_pane, orient="vertical")
main_pane.add(right_pane, weight=4)

queue_outer = tk.LabelFrame(right_pane, text="오늘의 TOP 5 작성 큐 (글작성순위 기준, 🔥=검색급증 확인, ⭐=숨은 고수익 키워드)")
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
