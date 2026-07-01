# -*- coding: utf-8 -*-
"""
app.py (v17.1) - 수익형 키워드 발굴기
v16의 UX(작성 큐, 상태 관리, 제목/개요/FAQ/태그, TXT/CSV 저장, 클립보드 복사)
+ v17의 알고리즘(기사 단위 bigram, 검증 3종 API, 포화도 기반 채점)
"""

import os
import sys
import json
import time
import threading
import tkinter as tk
from tkinter import ttk, messagebox
import csv

from naver_search_api import NaverSearchAPI, NaverDataLabAPI, NaverAdsAPI
import collector
import scorer

APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
CONFIG_PATH = os.path.join(APP_DIR, "api_config.json")
STATUS_PATH = os.path.join(APP_DIR, "keyword_status.json")
LOG_PATH = os.path.join(APP_DIR, "trend_debug_log.txt")

COLOR_NAVY = "#152238"
COLOR_BG = "#EEF1F6"
COLOR_CARD = "#FFFFFF"
COLOR_BLUE = "#2F6FED"
COLOR_GREEN = "#1B8A5A"
COLOR_RED = "#D64545"
COLOR_GRAY = "#8A8F98"
FONT_FAMILY = "맑은 고딕"


def write_log(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        write_log(f"저장 실패({path}): {e}")


TITLE_TEMPLATES = [
    ("SEO형", "{kw} 총정리 (최신 기준)"),
    ("질문형", "{kw}, 지금 꼭 알아야 할까?"),
    ("비교형", "{kw} vs 다른 방법, 무엇이 다를까"),
    ("실수방지형", "{kw} 신청 전 이것 모르면 손해봅니다"),
    ("후킹형", "{kw}, 오늘 아니면 늦을 수 있습니다"),
    ("감성형", "{kw} 직접 알아보고 남긴 후기"),
]


def generate_titles(keyword):
    return [(name, tpl.format(kw=keyword)) for name, tpl in TITLE_TEMPLATES]


def generate_outline(keyword, r):
    lines = []
    lines.append(f"1. {keyword}가 최근 이슈가 된 배경")
    lines.append(f"2. {keyword} 핵심 정보 요약")
    lines.append(f"3. {keyword} 관련 자주 묻는 질문")
    lines.append(f"4. {keyword} 신청/확인 방법 (있는 경우)")
    lines.append(f"5. 마무리 및 주의사항")
    return "\n".join(lines)


def generate_faq(keyword):
    faqs = [
        (f"{keyword}는 누구나 해당되나요?", "대상 조건에 따라 다르므로 최신 공고를 함께 확인하는 것이 좋습니다."),
        (f"{keyword} 신청 기한은 언제까지인가요?", "관련 기관 공지에 따라 변경될 수 있어 최신 일정을 확인해야 합니다."),
        (f"{keyword} 관련 주의할 점이 있나요?", "과장 광고나 사칭 사이트에 유의하고 공식 채널을 통해 확인하는 것이 안전합니다."),
    ]
    return faqs


def generate_tags(keyword, r):
    tags = [keyword, "2026", "최신정보"]
    if r.get("generic_flag"):
        tags.append("생활정보")
    if r.get("spike_ratio") and r["spike_ratio"] >= 1.3:
        tags.append("급상승이슈")
    return tags


class ScrollableText(tk.Frame):
    def __init__(self, master, height=10, **kw):
        super().__init__(master, bg=COLOR_CARD)
        self.text = tk.Text(self, height=height, wrap="word", bg=COLOR_CARD,
                             relief="flat", font=(FONT_FAMILY, 10), **kw)
        sb = ttk.Scrollbar(self, command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    def set_text(self, content):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", content)
        self.text.configure(state="disabled")

    def get_text(self):
        return self.text.get("1.0", "end").strip()


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("수익형 키워드 발굴기 v17.1")
        self.root.geometry("1440x900")
        self.root.configure(bg=COLOR_BG)

        self.config_data = load_json(CONFIG_PATH, {})
        self.status_data = load_json(STATUS_PATH, {})
        self.results = []
        self.selected_kw = None
        self.settings_visible = False

        self._setup_style()
        self._build_header()
        self._build_settings_panel()
        self._build_cards()
        self._build_progress()
        self._build_main_panels()
        self._build_queue_panel()

        self._load_config_to_entries()

    # ---------------- style ----------------
    def _setup_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TButton", font=(FONT_FAMILY, 10), padding=6)
        style.configure("Blue.TButton", background=COLOR_BLUE, foreground="white")
        style.map("Blue.TButton", background=[("active", "#1E56C4")])
        style.configure("Treeview", font=(FONT_FAMILY, 10), rowheight=24)
        style.configure("Treeview.Heading", font=(FONT_FAMILY, 10, "bold"))

    # ---------------- header ----------------
    def _build_header(self):
        header = tk.Frame(self.root, bg=COLOR_NAVY, height=54)
        header.pack(fill="x", side="top")
        tk.Label(header, text="수익형 키워드 발굴기 v17.1", bg=COLOR_NAVY, fg="white",
                 font=(FONT_FAMILY, 14, "bold")).pack(side="left", padx=16, pady=10)

        btn_frame = tk.Frame(header, bg=COLOR_NAVY)
        btn_frame.pack(side="right", padx=12)
        ttk.Button(btn_frame, text="⚙ 설정", command=self._toggle_settings).pack(side="right", padx=4)
        ttk.Button(btn_frame, text="▶ 오늘 분석 시작", style="Blue.TButton",
                   command=self.start_analysis).pack(side="right", padx=4)

    # ---------------- settings ----------------
    def _build_settings_panel(self):
        self.settings_outer = tk.Frame(self.root, bg=COLOR_BG)
        self.settings_frame = tk.Frame(self.settings_outer, bg=COLOR_CARD, bd=1, relief="solid")

        def group(parent, title):
            f = tk.LabelFrame(parent, text=title, bg=COLOR_CARD, font=(FONT_FAMILY, 10, "bold"), padx=10, pady=8)
            return f

        row = tk.Frame(self.settings_frame, bg=COLOR_CARD)
        row.pack(fill="x", padx=10, pady=8)

        # 1) 검색 API
        g1 = group(row, "① 네이버 검색 API (뉴스/블로그/문서수)")
        g1.pack(side="left", fill="both", expand=True, padx=5)
        tk.Label(g1, text="Client ID", bg=COLOR_CARD).grid(row=0, column=0, sticky="w")
        self.e_search_id = ttk.Entry(g1, width=28)
        self.e_search_id.grid(row=0, column=1, pady=2)
        tk.Label(g1, text="Client Secret", bg=COLOR_CARD).grid(row=1, column=0, sticky="w")
        self.e_search_secret = ttk.Entry(g1, width=28, show="*")
        self.e_search_secret.grid(row=1, column=1, pady=2)
        ttk.Button(g1, text="네이버 검색 API 연결 테스트",
                   command=self.test_search_api).grid(row=2, column=0, columnspan=2, pady=6, sticky="we")
        self.lbl_search_result = tk.Label(g1, text="", bg=COLOR_CARD, wraplength=260, justify="left")
        self.lbl_search_result.grid(row=3, column=0, columnspan=2, sticky="w")

        # 2) 데이터랩
        g2 = group(row, "② 네이버 데이터랩 (검색어트렌드)")
        g2.pack(side="left", fill="both", expand=True, padx=5)
        tk.Label(g2, text="Client ID", bg=COLOR_CARD).grid(row=0, column=0, sticky="w")
        self.e_dl_id = ttk.Entry(g2, width=28)
        self.e_dl_id.grid(row=0, column=1, pady=2)
        tk.Label(g2, text="Client Secret", bg=COLOR_CARD).grid(row=1, column=0, sticky="w")
        self.e_dl_secret = ttk.Entry(g2, width=28, show="*")
        self.e_dl_secret.grid(row=1, column=1, pady=2)
        ttk.Button(g2, text="검색 API 값 복사", command=self._copy_search_to_datalab).grid(
            row=2, column=0, columnspan=2, pady=2, sticky="we")
        ttk.Button(g2, text="데이터랩 연결 테스트",
                   command=self.test_datalab_api).grid(row=3, column=0, columnspan=2, pady=4, sticky="we")
        self.lbl_dl_result = tk.Label(g2, text="", bg=COLOR_CARD, wraplength=260, justify="left")
        self.lbl_dl_result.grid(row=4, column=0, columnspan=2, sticky="w")

        # 3) 검색광고 API
        g3 = group(row, "③ 네이버 검색광고 API (검색량/경쟁도)")
        g3.pack(side="left", fill="both", expand=True, padx=5)
        tk.Label(g3, text="Customer ID(숫자)", bg=COLOR_CARD).grid(row=0, column=0, sticky="w")
        self.e_ads_customer = ttk.Entry(g3, width=28)
        self.e_ads_customer.grid(row=0, column=1, pady=2)
        tk.Label(g3, text="License Key", bg=COLOR_CARD).grid(row=1, column=0, sticky="w")
        self.e_ads_license = ttk.Entry(g3, width=28)
        self.e_ads_license.grid(row=1, column=1, pady=2)
        tk.Label(g3, text="Secret Key", bg=COLOR_CARD).grid(row=2, column=0, sticky="w")
        self.e_ads_secret = ttk.Entry(g3, width=28, show="*")
        self.e_ads_secret.grid(row=2, column=1, pady=2)
        ttk.Button(g3, text="검색광고 API 연결 테스트",
                   command=self.test_ads_api).grid(row=3, column=0, columnspan=2, pady=6, sticky="we")
        self.lbl_ads_result = tk.Label(g3, text="", bg=COLOR_CARD, wraplength=260, justify="left")
        self.lbl_ads_result.grid(row=4, column=0, columnspan=2, sticky="w")

        ttk.Button(self.settings_frame, text="설정 저장", style="Blue.TButton",
                   command=self._save_config_from_entries).pack(pady=8)

    def _toggle_settings(self):
        if self.settings_visible:
            self.settings_outer.pack_forget()
            self.settings_visible = False
        else:
            self.settings_outer.pack(fill="x", side="top", padx=10, pady=6, before=self.cards_frame)
            self.settings_frame.pack(fill="x")
            self.settings_visible = True

    def _copy_search_to_datalab(self):
        self.e_dl_id.delete(0, "end")
        self.e_dl_id.insert(0, self.e_search_id.get())
        self.e_dl_secret.delete(0, "end")
        self.e_dl_secret.insert(0, self.e_search_secret.get())

    def _load_config_to_entries(self):
        c = self.config_data
        self.e_search_id.insert(0, c.get("search_client_id", ""))
        self.e_search_secret.insert(0, c.get("search_client_secret", ""))
        self.e_dl_id.insert(0, c.get("datalab_client_id", ""))
        self.e_dl_secret.insert(0, c.get("datalab_client_secret", ""))
        self.e_ads_customer.insert(0, c.get("ads_customer_id", ""))
        self.e_ads_license.insert(0, c.get("ads_license_key", ""))
        self.e_ads_secret.insert(0, c.get("ads_secret_key", ""))

    def _save_config_from_entries(self):
        self.config_data = {
            "search_client_id": self.e_search_id.get().strip(),
            "search_client_secret": self.e_search_secret.get().strip(),
            "datalab_client_id": self.e_dl_id.get().strip(),
            "datalab_client_secret": self.e_dl_secret.get().strip(),
            "ads_customer_id": self.e_ads_customer.get().strip(),
            "ads_license_key": self.e_ads_license.get().strip(),
            "ads_secret_key": self.e_ads_secret.get().strip(),
        }
        save_json(CONFIG_PATH, self.config_data)
        messagebox.showinfo("저장 완료", "API 설정이 저장되었습니다.")

    def _get_search_api(self):
        return NaverSearchAPI(self.e_search_id.get().strip(), self.e_search_secret.get().strip())

    def _get_datalab_api(self):
        return NaverDataLabAPI(self.e_dl_id.get().strip(), self.e_dl_secret.get().strip())

    def _get_ads_api(self):
        return NaverAdsAPI(self.e_ads_customer.get().strip(), self.e_ads_license.get().strip(),
                           self.e_ads_secret.get().strip())

    def test_search_api(self):
        def run():
            ok, msg, warn = self._get_search_api().test_connection()
            text = msg + ("\n" + warn if warn else "")
            self.root.after(0, lambda: self.lbl_search_result.configure(
                text=text, fg=COLOR_GREEN if ok else COLOR_RED))
        threading.Thread(target=run, daemon=True).start()

    def test_datalab_api(self):
        def run():
            ok, msg, warn = self._get_datalab_api().test_connection()
            text = msg + ("\n" + warn if warn else "")
            self.root.after(0, lambda: self.lbl_dl_result.configure(
                text=text, fg=COLOR_GREEN if ok else COLOR_RED))
        threading.Thread(target=run, daemon=True).start()

    def test_ads_api(self):
        def run():
            ok, msg, warn = self._get_ads_api().test_connection()
            text = msg + ("\n" + warn if warn else "")
            self.root.after(0, lambda: self.lbl_ads_result.configure(
                text=text, fg=COLOR_GREEN if ok else COLOR_RED))
        threading.Thread(target=run, daemon=True).start()

    # ---------------- cards ----------------
    def _build_cards(self):
        self.cards_frame = tk.Frame(self.root, bg=COLOR_BG)
        self.cards_frame.pack(fill="x", padx=10, pady=(10, 4))
        self.card_vars = {}
        labels = ["검증 완료 키워드", "TOP5 추천", "TOP10 후보", "위험 키워드", "마지막 실행"]
        for lb in labels:
            card = tk.Frame(self.cards_frame, bg=COLOR_CARD, bd=0, highlightbackground="#DDE1E8",
                             highlightthickness=1)
            card.pack(side="left", fill="both", expand=True, padx=5)
            tk.Label(card, text=lb, bg=COLOR_CARD, fg=COLOR_GRAY, font=(FONT_FAMILY, 9)).pack(
                anchor="w", padx=12, pady=(10, 0))
            var = tk.StringVar(value="0")
            tk.Label(card, textvariable=var, bg=COLOR_CARD, fg=COLOR_NAVY,
                     font=(FONT_FAMILY, 20, "bold")).pack(anchor="w", padx=12, pady=(0, 10))
            self.card_vars[lb] = var

    def _update_cards(self):
        verified = sum(1 for r in self.results if r["doc_status"] == "검증 완료"
                       or r["search_status"] == "검증 완료" or r["datalab_status"] == "검증 완료")
        top5 = sum(1 for r in self.results if r["grade"] == "TOP5")
        top10 = sum(1 for r in self.results if r["grade"] == "TOP10")
        risk = sum(1 for r in self.results if r["grade"] == "위험")
        self.card_vars["검증 완료 키워드"].set(str(verified))
        self.card_vars["TOP5 추천"].set(str(top5))
        self.card_vars["TOP10 후보"].set(str(top10))
        self.card_vars["위험 키워드"].set(str(risk))
        self.card_vars["마지막 실행"].set(time.strftime("%H:%M:%S"))

    # ---------------- progress ----------------
    def _build_progress(self):
        f = tk.Frame(self.root, bg=COLOR_BG)
        f.pack(fill="x", padx=10, pady=(0, 6))
        self.progress = ttk.Progressbar(f, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.status_var = tk.StringVar(value="대기 중")
        tk.Label(f, textvariable=self.status_var, bg=COLOR_BG, fg=COLOR_NAVY).pack(side="right")

    # ---------------- main 3-split ----------------
    def _build_main_panels(self):
        main = ttk.PanedWindow(self.root, orient="horizontal")
        main.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        # 왼쪽: TOP30 목록
        left = tk.Frame(main, bg=COLOR_CARD)
        main.add(left, weight=45)

        tk.Label(left, text="TOP30 키워드", bg=COLOR_CARD, font=(FONT_FAMILY, 11, "bold")).pack(
            anchor="w", padx=8, pady=6)
        cols = ("rank", "keyword", "grade", "score", "risk")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", height=25)
        headers = {"rank": "순위", "keyword": "키워드", "grade": "등급", "score": "점수", "risk": "위험여부"}
        widths = {"rank": 40, "keyword": 170, "grade": 55, "score": 55, "risk": 90}
        for c in cols:
            self.tree.heading(c, text=headers[c])
            self.tree.column(c, width=widths[c], anchor="center")
        self.tree.tag_configure("top5", foreground=COLOR_GREEN)
        self.tree.tag_configure("top10", foreground=COLOR_BLUE)
        self.tree.tag_configure("risk", foreground=COLOR_RED)
        self.tree.tag_configure("hold", foreground=COLOR_GRAY)
        vs = ttk.Scrollbar(left, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=(0, 8))
        vs.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select_keyword)

        # 오른쪽: 위(상세분석) / 아래(글초안)
        right = ttk.PanedWindow(main, orient="vertical")
        main.add(right, weight=55)

        detail = tk.Frame(right, bg=COLOR_CARD)
        right.add(detail, weight=45)
        tk.Label(detail, text="선택 키워드 분석", bg=COLOR_CARD, font=(FONT_FAMILY, 11, "bold")).pack(
            anchor="w", padx=8, pady=6)
        self.detail_text = ScrollableText(detail, height=14)
        self.detail_text.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        draft = tk.Frame(right, bg=COLOR_CARD)
        right.add(draft, weight=55)
        top_bar = tk.Frame(draft, bg=COLOR_CARD)
        top_bar.pack(fill="x", padx=8, pady=6)
        tk.Label(top_bar, text="글 초안 (제목 / 개요 / FAQ / 태그)", bg=COLOR_CARD,
                 font=(FONT_FAMILY, 11, "bold")).pack(side="left")
        ttk.Button(top_bar, text="클립보드 복사", command=self.copy_draft_to_clipboard).pack(side="right", padx=2)
        ttk.Button(top_bar, text="TXT 저장", command=self.save_draft_txt).pack(side="right", padx=2)
        ttk.Button(top_bar, text="큐에 추가", style="Blue.TButton", command=self.add_selected_to_queue).pack(
            side="right", padx=2)
        self.draft_text = ScrollableText(draft, height=16)
        self.draft_text.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def _on_select_keyword(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        r = self.results[idx]
        self.selected_kw = r
        self._render_detail(r)
        self._render_draft(r)

    def _render_detail(self, r):
        lines = []
        lines.append(f"■ 키워드: {r['keyword']}   [{r['grade']}]  최종점수 {r['final_score']}")
        lines.append("")
        lines.append(f"- 검색량: {r['search_volume'] if r['search_volume'] is not None else r['search_status']}"
                     + (f" ({r['search_error']})" if r.get("search_error") else ""))
        lines.append(f"- 문서수: {r['doc_count'] if r['doc_count'] is not None else r['doc_status']}"
                     + (f" ({r['doc_error']})" if r.get("doc_error") else ""))
        lines.append(f"- 경쟁도: {r.get('comp_idx') or '-'}")
        lines.append(f"- DataLab 상승률: {r['spike_ratio'] if r['spike_ratio'] is not None else r['datalab_status']}"
                     + (f" ({r['datalab_error']})" if r.get("datalab_error") else ""))
        lines.append(f"- 뉴스 언급: {r.get('news_count', 0)}건")
        lines.append(f"- 효율(검색량/문서수): {r['efficiency']:.2f}" if r.get("efficiency") else "- 효율: 산출 불가")
        lines.append(f"- 추정 월수익(참고용): {r['estimated_revenue_won']:,}원" if r.get("estimated_revenue_won") else "- 추정 월수익: 산출 불가")
        lines.append("")
        lines.append("[위험 사유]")
        if r["risk_reasons"]:
            for reason in r["risk_reasons"]:
                lines.append(f"  ! {reason}")
        else:
            lines.append("  - 없음")
        lines.append("")
        lines.append("[추천 이유 태그]")
        for tag in r["recommend_tags"]:
            lines.append(f"  ✓ {tag}")
        lines.append("")
        lines.append("[참고 기사]")
        lines.append(f"  {r.get('sample_title', '-')}")
        lines.append(f"  {r.get('sample_link', '-')}")
        self.detail_text.set_text("\n".join(lines))

    def _render_draft(self, r):
        kw = r["keyword"]
        lines = []
        lines.append("[제목 6유형]")
        for name, title in generate_titles(kw):
            lines.append(f"  ({name}) {title}")
        lines.append("")
        lines.append("[글 개요]")
        lines.append(generate_outline(kw, r))
        lines.append("")
        lines.append("[FAQ]")
        for q, a in generate_faq(kw):
            lines.append(f"  Q. {q}")
            lines.append(f"  A. {a}")
        lines.append("")
        lines.append("[추천 태그]")
        lines.append("  " + ", ".join(generate_tags(kw, r)))
        self.draft_text.set_text("\n".join(lines))

    def copy_draft_to_clipboard(self):
        text = self.draft_text.get_text()
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        messagebox.showinfo("복사 완료", "글 초안이 클립보드에 복사되었습니다.")

    def save_draft_txt(self):
        if not self.selected_kw:
            return
        kw = self.selected_kw["keyword"]
        path = os.path.join(APP_DIR, f"draft_{kw.replace(' ', '_')}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.draft_text.get_text())
        messagebox.showinfo("저장 완료", f"{path} 에 저장되었습니다.")

    # ---------------- 작성 큐 ----------------
    def _build_queue_panel(self):
        outer = tk.Frame(self.root, bg=COLOR_CARD, height=170, highlightbackground="#DDE1E8",
                         highlightthickness=1)
        outer.pack(fill="x", padx=10, pady=(0, 10))

        top = tk.Frame(outer, bg=COLOR_CARD)
        top.pack(fill="x", padx=8, pady=6)
        tk.Label(top, text="오늘의 TOP5 작성 큐", bg=COLOR_CARD, font=(FONT_FAMILY, 11, "bold")).pack(side="left")
        ttk.Button(top, text="작성 시작", command=lambda: self._set_queue_status("작성중")).pack(side="right", padx=2)
        ttk.Button(top, text="작성완료", command=lambda: self._set_queue_status("작성완료")).pack(side="right", padx=2)
        ttk.Button(top, text="발행완료", command=lambda: self._set_queue_status("발행완료")).pack(side="right", padx=2)
        ttk.Button(top, text="CSV 저장", command=self.export_csv).pack(side="right", padx=8)
        ttk.Button(top, text="TXT 저장(전체)", command=self.export_txt_all).pack(side="right", padx=2)

        cols = ("keyword", "grade", "status")
        self.queue_tree = ttk.Treeview(outer, columns=cols, show="headings", height=5)
        for c, t, w in [("keyword", "키워드", 220), ("grade", "등급", 80), ("status", "상태", 100)]:
            self.queue_tree.heading(c, text=t)
            self.queue_tree.column(c, width=w, anchor="center")
        self.queue_tree.pack(fill="x", padx=8, pady=(0, 8))

    def add_selected_to_queue(self):
        if not self.selected_kw:
            return
        kw = self.selected_kw["keyword"]
        if kw not in self.status_data:
            self.status_data[kw] = "미작성"
        save_json(STATUS_PATH, self.status_data)
        self._refresh_queue_tree()

    def _refresh_queue_tree(self):
        self.queue_tree.delete(*self.queue_tree.get_children())
        grade_map = {r["keyword"]: r["grade"] for r in self.results}
        for kw, status in self.status_data.items():
            self.queue_tree.insert("", "end", iid=kw, values=(kw, grade_map.get(kw, "-"), status))

    def _set_queue_status(self, status):
        sel = self.queue_tree.selection()
        if not sel:
            messagebox.showwarning("선택 필요", "큐에서 키워드를 먼저 선택하세요.")
            return
        kw = sel[0]
        self.status_data[kw] = status
        save_json(STATUS_PATH, self.status_data)
        self._refresh_queue_tree()

    def export_csv(self):
        path = os.path.join(APP_DIR, "keyword_result.csv")
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["키워드", "등급", "점수", "검색량", "문서수", "DataLab상승률", "위험사유"])
            for r in self.results:
                w.writerow([r["keyword"], r["grade"], r["final_score"], r["search_volume"],
                           r["doc_count"], r["spike_ratio"], "; ".join(r["risk_reasons"])])
        messagebox.showinfo("저장 완료", f"{path} 에 저장되었습니다.")

    def export_txt_all(self):
        path = os.path.join(APP_DIR, "keyword_result.txt")
        with open(path, "w", encoding="utf-8") as f:
            for r in self.results:
                f.write(f"[{r['grade']}] {r['keyword']} (점수 {r['final_score']})\n")
        messagebox.showinfo("저장 완료", f"{path} 에 저장되었습니다.")

    # ---------------- 분석 실행 ----------------
    def start_analysis(self):
        self.status_var.set("수집 중...")
        self.progress["value"] = 10
        threading.Thread(target=self._run_pipeline, daemon=True).start()

    def _run_pipeline(self):
        try:
            search_api = self._get_search_api()
            datalab_api = self._get_datalab_api()
            ads_api = self._get_ads_api()

            candidates = collector.collect_candidates(
                search_api=search_api, discovery_target=100, light_filter_target=40, log=write_log)
            self.root.after(0, lambda: self.progress.configure(value=40))
            self.root.after(0, lambda: self.status_var.set("API 검증 중..."))

            results = scorer.score_candidates(
                candidates, search_api=search_api, datalab_api=datalab_api, ads_api=ads_api,
                verify_top_n=40, log=write_log)

            self.results = results
            self.root.after(0, self._on_analysis_done)
        except Exception as e:
            write_log(f"[app] 파이프라인 오류: {e}")
            self.root.after(0, lambda: messagebox.showerror("오류", f"분석 중 오류가 발생했습니다:\n{e}"))
            self.root.after(0, lambda: self.status_var.set("오류 발생"))

    def _on_analysis_done(self):
        self.progress["value"] = 100
        self.status_var.set("완료")
        self._update_cards()
        self._render_tree()

        # TOP5 자동 큐 등록
        for r in self.results:
            if r["grade"] == "TOP5" and r["keyword"] not in self.status_data:
                self.status_data[r["keyword"]] = "미작성"
        save_json(STATUS_PATH, self.status_data)
        self._refresh_queue_tree()

    def _render_tree(self):
        self.tree.delete(*self.tree.get_children())
        top30 = self.results[:30]
        for i, r in enumerate(top30):
            tag = {"TOP5": "top5", "TOP10": "top10", "위험": "risk", "보류": "hold"}.get(r["grade"], "hold")
            risk_text = "; ".join(r["risk_reasons"]) if r["risk_reasons"] else "-"
            self.tree.insert("", "end", iid=str(i),
                             values=(i + 1, r["keyword"], r["grade"], r["final_score"], risk_text),
                             tags=(tag,))


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
