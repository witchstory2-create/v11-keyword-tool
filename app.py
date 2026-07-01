# -*- coding: utf-8 -*-
"""
app.py (v17 - 수익형 키워드 발굴기)

목표: "오늘 무엇을 써야 하는가"가 아니라
     "오늘 돈 될 가능성이 있는 키워드만 골라내는 것"에 집중.

레이아웃: 상단(API설정) - 상단(카드형 요약) - 중앙(키워드 테이블) - 우측(상세 패널) - 하단(상태바)
"""

import os
import sys
import json
import threading
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox

import collector
import scorer
from naver_search_api import NaverOpenAPI, NaverAdAPI, verify_keyword, write_debug_log

APP_TITLE = "수익형 키워드 발굴기 v17"
DISPLAY_TOP_N = 40  # 위험 포함 전체를 다 보여줌 (검증 대상이 40개이므로)

BUCKET_COLOR = {
    "TOP5": "#d4f4dd",       # 진한 초록 계열
    "TOP10": "#fff6d5",      # 옅은 노랑
    "상시추천": "#dbe9ff",    # 옅은 파랑
    "보류": "#f0f0f0",       # 회색
    "위험": "#fddede",       # 빨강 계열
}
BUCKET_ORDER = {"TOP5": 0, "TOP10": 1, "상시추천": 2, "보류": 3, "위험": 4}


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(app_dir(), "blog_config.json")


def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_config(cfg: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


class SummaryCard(ttk.Frame):
    """상단 카드형 요약 영역의 카드 1개."""
    def __init__(self, parent, title, value="-", bg="#ffffff"):
        super().__init__(parent, style="Card.TFrame")
        self.configure(padding=10)
        self.value_var = tk.StringVar(value=value)
        ttk.Label(self, text=title, style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(self, textvariable=self.value_var, style="CardValue.TLabel").pack(anchor="w", pady=(4, 0))

    def set_value(self, value):
        self.value_var.set(str(value))


class KeywordApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1480x800")
        self.configure(bg="#f4f6f9")

        self.results = []
        self.cfg = load_config()

        self._setup_style()
        self._build_config_frame()
        self._build_summary_cards()
        self._build_main_frame()
        self._build_status_bar()

    # ---------------- 스타일 ----------------
    def _setup_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Card.TFrame", background="#ffffff", relief="flat")
        style.configure("CardTitle.TLabel", background="#ffffff", foreground="#666666",
                        font=("맑은 고딕", 9))
        style.configure("CardValue.TLabel", background="#ffffff", foreground="#222222",
                        font=("맑은 고딕", 18, "bold"))
        style.configure("Treeview", rowheight=26, font=("맑은 고딕", 10))
        style.configure("Treeview.Heading", font=("맑은 고딕", 10, "bold"))

    # ---------------- API 설정 ----------------
    def _build_config_frame(self):
        frame = ttk.LabelFrame(self, text="API 설정")
        frame.pack(fill="x", padx=12, pady=(10, 6))

        self.vars = {
            "naver_client_id": tk.StringVar(value=self.cfg.get("naver_client_id", "")),
            "naver_client_secret": tk.StringVar(value=self.cfg.get("naver_client_secret", "")),
            "ad_api_key": tk.StringVar(value=self.cfg.get("ad_api_key", "")),
            "ad_secret_key": tk.StringVar(value=self.cfg.get("ad_secret_key", "")),
            "ad_customer_id": tk.StringVar(value=self.cfg.get("ad_customer_id", "")),
        }
        rows = [
            ("네이버 검색 API Client ID", "naver_client_id", False),
            ("네이버 검색 API Client Secret", "naver_client_secret", True),
            ("검색광고 API License Key", "ad_api_key", False),
            ("검색광고 API Secret Key", "ad_secret_key", True),
            ("검색광고 API Customer ID", "ad_customer_id", False),
        ]
        for i, (label, key, is_secret) in enumerate(rows):
            r, c = divmod(i, 3)
            ttk.Label(frame, text=label).grid(row=r * 2, column=c, sticky="w", padx=8, pady=(6, 0))
            entry = ttk.Entry(frame, textvariable=self.vars[key], width=32, show="*" if is_secret else "")
            entry.grid(row=r * 2 + 1, column=c, sticky="w", padx=8, pady=(0, 6))

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=4, column=0, columnspan=3, pady=6)
        ttk.Button(btn_frame, text="네이버 검색 API 연결 테스트", command=self.test_open_api).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="검색광고 API 연결 테스트", command=self.test_ad_api).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="설정 저장", command=self.on_save_config).pack(side="left", padx=4)

    def on_save_config(self):
        cfg = {k: v.get().strip() for k, v in self.vars.items()}
        save_config(cfg)
        self.cfg = cfg
        messagebox.showinfo("저장 완료", "API 설정이 저장되었습니다.")

    def _make_open_api(self):
        return NaverOpenAPI(self.vars["naver_client_id"].get().strip(),
                            self.vars["naver_client_secret"].get().strip())

    def _make_ad_api(self):
        return NaverAdAPI(self.vars["ad_api_key"].get().strip(),
                          self.vars["ad_secret_key"].get().strip(),
                          self.vars["ad_customer_id"].get().strip())

    def test_open_api(self):
        ok, msg = self._make_open_api().test_connection()
        (messagebox.showinfo if ok else messagebox.showerror)("검색 API 테스트", msg)

    def test_ad_api(self):
        ok, msg = self._make_ad_api().test_connection()
        (messagebox.showinfo if ok else messagebox.showerror)("검색광고 API 테스트", msg)

    # ---------------- 상단 카드형 요약 ----------------
    def _build_summary_cards(self):
        card_row = ttk.Frame(self)
        card_row.pack(fill="x", padx=12, pady=(0, 6))

        self.card_total = SummaryCard(card_row, "검증 완료 키워드")
        self.card_top5 = SummaryCard(card_row, "TOP5")
        self.card_top10 = SummaryCard(card_row, "TOP10 보조추천")
        self.card_steady = SummaryCard(card_row, "상시추천")
        self.card_risk = SummaryCard(card_row, "위험 키워드")
        self.card_updated = SummaryCard(card_row, "마지막 실행")

        for card in (self.card_total, self.card_top5, self.card_top10,
                    self.card_steady, self.card_risk, self.card_updated):
            card.pack(side="left", fill="x", expand=True, padx=4)

        run_frame = ttk.Frame(self)
        run_frame.pack(fill="x", padx=12, pady=(0, 6))
        self.run_btn = ttk.Button(run_frame, text="▶ 오늘의 수익형 키워드 분석 시작",
                                  command=self.on_run_pipeline)
        self.run_btn.pack(side="left")

    # ---------------- 중앙 테이블 + 우측 상세 패널 ----------------
    def _build_main_frame(self):
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=12, pady=6)

        center = ttk.LabelFrame(body, text="키워드 분석 결과")
        center.pack(side="left", fill="both", expand=True)

        columns = ("rank", "keyword", "bucket", "score", "stars", "vol", "doc", "eff", "trend")
        headers = {
            "rank": "순위", "keyword": "키워드", "bucket": "등급", "score": "점수",
            "stars": "평점", "vol": "검색량", "doc": "문서수", "eff": "효율", "trend": "DataLab",
        }
        self.tree = ttk.Treeview(center, columns=columns, show="headings", height=30)
        widths = {"rank": 50, "keyword": 220, "bucket": 90, "score": 70, "stars": 100,
                 "vol": 90, "doc": 100, "eff": 130, "trend": 110}
        for col in columns:
            self.tree.heading(col, text=headers[col])
            self.tree.column(col, width=widths[col], anchor="center")
        self.tree.pack(fill="both", expand=True, padx=6, pady=6)

        for bucket, color in BUCKET_COLOR.items():
            self.tree.tag_configure(bucket, background=color)

        self.tree.bind("<<TreeviewSelect>>", self.on_select_row)

        right = ttk.LabelFrame(body, text="상세 분석 패널", width=420)
        right.pack(side="left", fill="y", padx=(8, 0))
        right.pack_propagate(False)

        self.detail_text = tk.Text(right, width=46, height=42, wrap="word",
                                   font=("맑은 고딕", 10), relief="flat", bg="#fbfbfc")
        self.detail_text.pack(fill="both", expand=True, padx=8, pady=8)
        self.detail_text.config(state="disabled")

    # ---------------- 하단 상태바 ----------------
    def _build_status_bar(self):
        bar = ttk.Frame(self)
        bar.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar(value="대기 중")
        ttk.Label(bar, textvariable=self.status_var, anchor="w",
                 padding=(10, 4)).pack(side="left", fill="x", expand=True)

    def _set_status(self, text):
        self.status_var.set(text)

    # ---------------- 파이프라인 실행 ----------------
    def on_run_pipeline(self):
        cid = self.vars["naver_client_id"].get().strip()
        csec = self.vars["naver_client_secret"].get().strip()
        if not cid or not csec:
            messagebox.showwarning("API 키 필요", "먼저 네이버 검색 API Client ID/Secret을 입력하고 저장하세요.")
            return

        self.run_btn.config(state="disabled")
        self.tree.delete(*self.tree.get_children())
        self._set_status("Discovery 단계: 뉴스 수집 중...")

        thread = threading.Thread(target=self._pipeline_worker, daemon=True)
        thread.start()

    def _pipeline_worker(self):
        try:
            open_api = self._make_open_api()
            ad_api = self._make_ad_api()

            def progress_cb(msg):
                self.after(0, self._set_status, msg)

            filtered_40 = collector.collect_candidates(open_api, progress_cb=progress_cb)

            if not filtered_40:
                self.after(0, self._set_status, "수집된 후보가 없습니다.")
                self.after(0, lambda: self.run_btn.config(state="normal"))
                return

            verified = []
            for i, cand in enumerate(filtered_40, start=1):
                self.after(0, self._set_status,
                          f"Verification 단계: '{cand['keyword']}' 검증 중 ({i}/{len(filtered_40)})")
                try:
                    v = verify_keyword(cand["keyword"], open_api, ad_api)
                except Exception as e:
                    write_debug_log(f"[verify_keyword] '{cand['keyword']}' 예외: {e}")
                    v = {"doc_count": None, "spike_ratio": 1.0, "trend_status": "미검증",
                        "search_volume": None, "comp_label": None, "comp_idx": 0}
                verified.append({**cand, **v})

            self.after(0, self._set_status, "Scoring 단계: 점수 계산 및 위험 판정 중...")
            scored = scorer.compute_scores(verified)

            self.results = scored
            self.after(0, self._render_results)
            self.after(0, self._update_summary_cards)
            self.after(0, self._set_status, f"완료 - 검증 {len(filtered_40)}건 분석됨")
        except Exception as e:
            write_debug_log(f"[pipeline] 전체 실패: {e}")
            self.after(0, lambda: messagebox.showerror("오류", f"분석 중 오류가 발생했습니다: {e}"))
        finally:
            self.after(0, lambda: self.run_btn.config(state="normal"))

    def _update_summary_cards(self):
        total = len(self.results)
        top5 = sum(1 for r in self.results if r["bucket"] == "TOP5")
        top10 = sum(1 for r in self.results if r["bucket"] == "TOP10")
        steady = sum(1 for r in self.results if r["bucket"] == "상시추천")
        risk = sum(1 for r in self.results if r["bucket"] == "위험")

        self.card_total.set_value(total)
        self.card_top5.set_value(top5)
        self.card_top10.set_value(top10)
        self.card_steady.set_value(steady)
        self.card_risk.set_value(risk)
        self.card_updated.set_value(datetime.now().strftime("%H:%M:%S"))

    def _render_results(self):
        self.tree.delete(*self.tree.get_children())
        ordered = sorted(self.results, key=lambda r: (
            BUCKET_ORDER.get(r["bucket"], 9), -r["final_score"]
        ))
        for idx, r in enumerate(ordered[:DISPLAY_TOP_N]):
            rank_txt = r["rank"] if r["rank"] is not None else "-"
            doc_txt = f"{r['doc_count']:,}" if r.get("doc_count") is not None else "-"
            vol_txt = f"{r['search_volume']:,}" if r.get("search_volume") else "-"
            eff_txt = r.get("efficiency_label", "-")
            trend_txt = f"{r.get('trend_status', '-')} x{r.get('spike_ratio', 1.0):.2f}"

            iid = f"row{idx}"
            self.tree.insert("", "end", iid=iid, values=(
                rank_txt, r["keyword"], r["bucket"], r["final_score"],
                r["stars"], vol_txt, doc_txt, eff_txt, trend_txt,
            ), tags=(r["bucket"],))
            self._row_keyword_map = getattr(self, "_row_keyword_map", {})
            self._row_keyword_map[iid] = r["keyword"]

    def on_select_row(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        keyword = self._row_keyword_map.get(iid)
        r = next((x for x in self.results if x["keyword"] == keyword), None)
        if r is None:
            return
        self._show_detail(r)

    def _show_detail(self, r):
        lines = []
        rank_txt = f"{r['rank']}위" if r["rank"] is not None else "순위 제외(위험)"
        lines.append(f"[{rank_txt}] {r['keyword']}")
        lines.append(f"{r['stars']}  (최종점수 {r['final_score']})")
        lines.append(f"등급: {r['bucket']}")
        lines.append(f"카테고리: {r.get('category', '-')} / 예상 수익성: {r.get('profit_label', '-')}")
        lines.append("")
        lines.append("[추천/판정 이유]")
        for tag in r.get("reason_tags", []):
            prefix = "  ⚠ " if tag.startswith("[위험]") else "  - "
            lines.append(f"{prefix}{tag}")
        lines.append("")
        lines.append("[점수 구성]")
        lines.append(f"  - IssueScore(시급성): {r.get('issue_score')}")
        lines.append(f"  - OpportunityScore(검색량÷문서수 효율): {r.get('opportunity_score')}")
        lines.append(f"  - CategoryWeight: {r.get('category_weight')}")
        lines.append("")
        lines.append("[검증 원본 데이터]")
        lines.append(f"  - 문서수: {r.get('doc_count', '-')}")
        lines.append(f"  - 검색량(월): {r.get('search_volume', '-')}")
        lines.append(f"  - DataLab: {r.get('trend_status', '-')} (x{r.get('spike_ratio', 1.0):.2f})")
        lines.append(f"  - 경쟁도: {r.get('comp_label', '-')}")
        lines.append("")
        lines.append("[참고 기사]")
        for a in r.get("articles", [])[:3]:
            lines.append(f"  - {a['title']}")
            lines.append(f"    {a['link']}")

        self.detail_text.config(state="normal")
        self.detail_text.delete("1.0", "end")
        self.detail_text.insert("1.0", "\n".join(lines))
        self.detail_text.config(state="disabled")


if __name__ == "__main__":
    app = KeywordApp()
    app.mainloop()
