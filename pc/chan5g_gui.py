#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chan5g_gui.py —— 5GHz 信道普查仪图形界面。

插上板子，点「扫描」，柱状图和 AP 列表当场刷新。不用生成文件、不用开浏览器。

依赖：Python 标准库 tkinter + pyserial（都已就位，不装任何第三方包）

用法:
    python chan5g_gui.py                 # 打开界面
    python chan5g_gui.py --port COM9     # 指定串口
    python chan5g_gui.py --test          # 不开窗，跑一次扫描验证链路
"""

import argparse
import os
import queue
import sys
import threading
import time
import tkinter as tk
import types
from tkinter import ttk

import bruce_push as bp
import chan5g_report as rep

HERE = os.path.dirname(os.path.abspath(__file__))
SCAN_JS = bp.find_asset("scanjson.js")
SCAN_DEV = "/scripts/scanjson.js"

FONT = ("Microsoft YaHei UI", 10)
FONT_B = ("Microsoft YaHei UI", 10, "bold")
FONT_S = ("Microsoft YaHei UI", 8)
FONT_XS = ("Microsoft YaHei UI", 7)
FONT_BIG = ("Microsoft YaHei UI", 22, "bold")
FONT_MID = ("Microsoft YaHei UI", 13, "bold")

BG = "#f6f8fa"
CARD = "#ffffff"
LINE = "#d8dee4"
INK = "#1f2328"
DIM = "#656d76"
BLUE = "#0969da"


def nice_top(v):
    if v <= 5:
        return max(1.0, float(int(v) + (0 if v == int(v) else 1)))
    if v <= 10:
        return float(int(v / 2 + 0.999) * 2)
    return float(int(v / 4 + 0.999) * 4)


def bar_color(s, is_dfs):
    if is_dfs:
        return "#9aa4b2"
    if s >= 5:
        return "#da3633"
    if s >= 2:
        return "#d29922"
    if s > 0:
        return "#2ea44f"
    return "#e6e9ef"


def parse_scan(out):
    """把 <<<SCAN / >>>END 之间的行解析成 AP 字典列表。"""
    rows, started = [], False
    for ln in out.splitlines():
        ln = ln.strip("\r\n ")
        if ln.startswith("<<<SCAN"):
            started = True
            continue
        if ln.startswith(">>>END"):
            break
        if not started or not ln:
            continue
        parts = ln.split("\t")
        if len(parts) < 4:
            continue
        try:
            ch = int(parts[-1])
            rssi = int(parts[-2])
            mac = parts[-3]
        except ValueError:
            continue
        ssid = "\t".join(parts[:-3]).strip()
        rows.append({
            "ms": int(time.time() * 1000),
            "ssid": ssid, "mac": mac, "rssi": rssi, "ch": ch,
            "band": "5G" if ch > 14 else "2.4G", "enc": "",
        })
    return rows


# ---------------------------------------------------------------- 界面

class App:
    def __init__(self, root, port=None, demo=False):
        self.root = root
        self.s = None
        self.cur_port = None
        self.q = queue.Queue()
        self.busy = False
        self.last_rows = []
        self.mine_mac = None      # 用户标记的「自己的 AP」，统计时排除，避免自己占着自己的信道
        self.mine_info = None
        self.baseline = None      # 改路由器之前存一份快照，改完扫一次就能算改善
        self._demo = demo

        root.title("5GHz 信道普查仪 — ESP32-C5")
        root.configure(bg=BG)
        root.geometry("1120x780")
        root.minsize(900, 620)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_top()
        self._build_verdict()
        self._build_charts()
        self._build_table()

        self.port_var.set(port or "")
        self.refresh_ports()
        self.root.after(120, self.poll)
        self.root.after(300, self.auto_tick)
        if port:
            self.root.after(500, self.scan)

    # ---------- 顶部工具条 ----------
    def _build_top(self):
        bar = tk.Frame(self.root, bg=CARD, highlightthickness=1,
                       highlightbackground=LINE)
        bar.pack(fill="x", side="top")

        pad = {"padx": 8, "pady": 8}

        tk.Label(bar, text="串口", bg=CARD, fg=DIM, font=FONT).pack(side="left", padx=(14, 4), pady=8)
        self.port_var = tk.StringVar()
        self.port_box = ttk.Combobox(bar, textvariable=self.port_var, width=10,
                                     state="readonly", font=FONT)
        self.port_box.pack(side="left", pady=8)

        self.btn_scan = tk.Button(bar, text="扫描", command=self.scan,
                                  font=FONT_B, bg=BLUE, fg="white",
                                  activebackground="#0757ba", activeforeground="white",
                                  relief="flat", padx=18, pady=3, cursor="hand2")
        self.btn_scan.pack(side="left", padx=(12, 6), pady=8)

        self.auto_var = tk.BooleanVar(value=False)
        tk.Checkbutton(bar, text="连续", variable=self.auto_var, bg=CARD,
                       fg=INK, font=FONT, activebackground=CARD,
                       selectcolor=CARD).pack(side="left", padx=(6, 2))

        self.gap_var = tk.StringVar(value="6")
        ttk.Combobox(bar, textvariable=self.gap_var, width=4, font=FONT,
                     values=["3", "5", "6", "10", "15", "30"],
                     state="readonly").pack(side="left")
        tk.Label(bar, text="秒", bg=CARD, fg=DIM, font=FONT).pack(side="left", padx=(3, 12))

        self.btn_base = tk.Button(bar, text="存基线", command=self.save_baseline,
                                  font=FONT, bg="#f6f8fa", fg=INK, relief="flat",
                                  highlightthickness=1, highlightbackground=LINE,
                                  padx=10, pady=2, cursor="hand2")
        self.btn_base.pack(side="left", padx=(0, 6))

        self.btn_clr = tk.Button(bar, text="清空标记", command=self.clear_marks,
                                 font=FONT, bg="#f6f8fa", fg=DIM, relief="flat",
                                 highlightthickness=1, highlightbackground=LINE,
                                 padx=10, pady=2, cursor="hand2")
        self.btn_clr.pack(side="left", padx=(0, 6))

        self.btn_rep = tk.Button(bar, text="导出报告", command=self.export_report,
                                 font=FONT, bg="#f6f8fa", fg=INK, relief="flat",
                                 highlightthickness=1, highlightbackground=LINE,
                                 padx=10, pady=2, cursor="hand2")
        self.btn_rep.pack(side="left", padx=(0, 12))

        self.status = tk.Label(bar, text="就绪", bg=CARD, fg=DIM, font=FONT)
        self.status.pack(side="right", padx=14)

    # ---------- 结论区 ----------
    def _build_verdict(self):
        wrap = tk.Frame(self.root, bg=BG)
        wrap.pack(fill="x", padx=14, pady=(10, 0))

        card = tk.Frame(wrap, bg=CARD, highlightthickness=1, highlightbackground=LINE)
        card.pack(fill="x")

        left = tk.Frame(card, bg=CARD)
        left.pack(side="left", padx=(18, 30), pady=12)
        tk.Label(left, text="建议信道", bg=CARD, fg=DIM, font=FONT_S).pack(anchor="w")
        self.lbl_ch = tk.Label(left, text="—", bg=CARD, fg=BLUE, font=FONT_BIG)
        self.lbl_ch.pack(anchor="w")

        mid = tk.Frame(card, bg=CARD)
        mid.pack(side="left", pady=12, fill="x", expand=True)
        self.lbl_note = tk.Label(mid, text="点「扫描」开始", bg=CARD, fg=INK,
                                 font=FONT, justify="left", anchor="w", wraplength=620)
        self.lbl_note.pack(anchor="w", fill="x")
        mid.bind("<Configure>", lambda e: self._reflow(e.width))
        self.lbl_meta = tk.Label(mid, text="", bg=CARD, fg=DIM, font=FONT_S,
                                 justify="left", anchor="w")
        self.lbl_meta.pack(anchor="w", fill="x", pady=(4, 0))

        self.lbl_mine = tk.Label(mid, text="双击下方表格里的那一行，把它认成「你的 AP」——之后它不再算进干扰，只看邻居。",
                                 bg=CARD, fg=DIM, font=FONT_S, justify="left", anchor="w")
        self.lbl_mine.pack(anchor="w", fill="x", pady=(3, 0))

        self.lbl_cmp = tk.Label(mid, text="", bg=CARD, fg="#1a7f37", font=FONT_B,
                                justify="left", anchor="w")
        self.lbl_cmp.pack(anchor="w", fill="x", pady=(2, 0))

        right = tk.Frame(card, bg=CARD)
        right.pack(side="right", padx=(20, 18), pady=12)
        self.lbl_busy = tk.Label(right, text="", bg=CARD, fg="#da3633", font=FONT_MID)
        self.lbl_busy.pack(anchor="e")

    # ---------- 柱状图 ----------
    def _build_charts(self):
        wrap = tk.Frame(self.root, bg=BG)
        wrap.pack(fill="both", expand=True, padx=14, pady=(10, 0))
        wrap.columnconfigure(0, weight=65)
        wrap.columnconfigure(1, weight=35)
        wrap.rowconfigure(0, weight=1)

        self.cv5 = tk.Canvas(wrap, bg=CARD, highlightthickness=1,
                             highlightbackground=LINE, height=250, width=1)
        self.cv5.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.cv5.bind("<Configure>", lambda e: self.redraw())

        self.cv2 = tk.Canvas(wrap, bg=CARD, highlightthickness=1,
                             highlightbackground=LINE, height=250, width=1)
        self.cv2.grid(row=0, column=1, sticky="nsew")
        self.cv2.bind("<Configure>", lambda e: self.redraw())

        legend = tk.Frame(self.root, bg=BG)
        legend.pack(fill="x", padx=18, pady=(4, 0))
        for color, text in [("#2ea44f", "空闲"), ("#d29922", "一般"),
                            ("#da3633", "拥挤"), ("#9aa4b2", "DFS"),
                            ("#eef1f5", "无 AP")]:
            f = tk.Frame(legend, bg=BG)
            f.pack(side="left", padx=(0, 16))
            tk.Label(f, text="  ", bg=color, font=FONT_XS,
                     borderwidth=0).pack(side="left", padx=(0, 4))
            tk.Label(f, text=text, bg=BG, fg=DIM, font=FONT_S).pack(side="left")
        tk.Label(legend, text="柱上数字 = AP 个数    蓝框 = 推荐信道",
                 bg=BG, fg=DIM, font=FONT_S).pack(side="right")

    # ---------- AP 列表 ----------
    def _build_table(self):
        wrap = tk.Frame(self.root, bg=BG)
        wrap.pack(fill="both", expand=True, padx=14, pady=(8, 14))

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", font=FONT_S, rowheight=24,
                        background=CARD, fieldbackground=CARD)
        style.configure("Treeview.Heading", font=FONT_S)

        cols = ("ssid", "rssi", "ch", "band", "mac")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", height=7)
        for c, t, w, a in [("ssid", "SSID", 380, "w"), ("rssi", "RSSI", 90, "e"),
                           ("ch", "信道", 70, "e"), ("band", "频段", 80, "e"),
                           ("mac", "MAC", 190, "w")]:
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor=a)

        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.tree.tag_configure("strong", foreground="#cf222e")
        self.tree.tag_configure("mid", foreground="#9a6700")
        self.tree.tag_configure("weak", foreground="#57606a")
        self.tree.tag_configure("mine", background="#fff8c5")
        self.tree.bind("<Double-1>", self.on_pick)

    # ---------- 串口 ----------
    def refresh_ports(self):
        ports = self._scan_ports()
        self.port_box["values"] = ports or ["(没找到串口)"]
        cur = self.port_var.get()
        if cur not in ports:
            auto = bp.find_port()
            if auto:
                self.port_var.set(auto)
            elif ports:
                self.port_var.set(ports[0])

    @staticmethod
    def _scan_ports():
        try:
            from serial.tools import list_ports as lp
            return [p.device for p in lp.comports()]
        except Exception:
            return []

    def ensure_open(self):
        port = self.port_var.get()
        if self.s is not None and self.cur_port == port:
            return True
        if self.s is not None:
            try:
                self.s.close()
            except Exception:
                pass
            self.s = None
        self.s = bp.open_port(port)
        self.cur_port = port
        bp.do_push(self.s, SCAN_JS, SCAN_DEV)
        return self.s is not None

    # ---------- 扫描 ----------
    def scan(self):
        if self.busy:
            return
        self.busy = True
        self.btn_scan.configure(state="disabled", bg="#8c959f")
        self.status.configure(text="扫描中 ...")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        try:
            self.ensure_open()
            out = bp.do_cmd(self.s, "js " + SCAN_DEV, quiet=2.0,
                            total=45, until=b">>>END")
            rows = parse_scan(out)
            if not rows:
                self.q.put(("err", out[-400:]))
            else:
                self.q.put(("data", rows))
        except Exception as e:
            self.q.put(("err", "{}: {}".format(type(e).__name__, e)))

    def poll(self):
        while not self.q.empty():
            kind, payload = self.q.get()
            if kind == "data":
                self.apply(payload)
            else:
                self.status.configure(text="失败：" + payload.replace("\n", " ")[:90])
            self.busy = False
            self.btn_scan.configure(state="normal", bg=BLUE)
        self.root.after(100, self.poll)

    def auto_tick(self):
        if self.auto_var.get() and not self.busy:
            self.scan()
        try:
            gap = float(self.gap_var.get())
        except ValueError:
            gap = 6.0
        self.root.after(int(gap * 1000), self.auto_tick)

    # ---------- 应用数据 ----------
    def apply(self, rows):
        self.last_rows = rows

        # 先把自己家里的 AP 摘出去：它自己占着自己那个信道，
        # 不排除的话「改到 149 之后 149 反而变拥挤」，结论会自相矛盾。
        mine = None
        if self.mine_mac:
            for r in rows:
                if r["mac"] == self.mine_mac:
                    mine = r
                    break
        others = [r for r in rows if r["mac"] != self.mine_mac] if self.mine_mac else rows

        st5 = rep.aggregate(others, rep.CH5)
        st2 = rep.aggregate(others, rep.CH2)
        ch, width, note, dfs_warn = rep.advise(st5)
        self.plan = (ch, width)

        self.lbl_ch.configure(text="{} / {}MHz".format(ch, width),
                              fg="#9a6700" if dfs_warn else BLUE)
        self.lbl_note.configure(text=note)

        n5 = sum(1 for r in rows if r["band"] == "5G")
        n2 = len(rows) - n5
        busy5 = max(rep.CH5, key=lambda c: (rep.score_of(st5[c]), st5[c]["n"]))
        busy2 = max(rep.CH2, key=lambda c: (rep.score_of(st2[c]), st2[c]["n"]))
        self.lbl_meta.configure(
            text="AP {} 个（2.4G {} / 5G {}，不含你自己）    邻居最挤：5G {} 信道 / 2.4G {} 信道".format(
                len(others), n2, n5, busy5, busy2))
        self.lbl_busy.configure(text="")

        self._update_mine(mine)
        self.st5, self.st2 = st5, st2
        self.redraw()
        self.fill_table(rows)
        self.update_cmp()
        self.status.configure(text="上次扫描 {}".format(time.strftime("%H:%M:%S")))

        # 演示用：第一次扫完自动把最强的那个认成「你的 AP」并存基线
        if self._demo and rows:
            self._demo = False
            self.mine_mac = sorted(rows, key=lambda r: -r["rssi"])[0]["mac"]
            self.apply(rows)
            self.save_baseline()

    def _update_mine(self, mine):
        if mine is None:
            self.mine_info = None
            if self.mine_mac:
                self.lbl_mine.configure(
                    text="标记的 AP 这次没扫到（关机了，或者换了随机 MAC）。双击别的行重新认。",
                    fg="#9a6700")
            else:
                self.lbl_mine.configure(
                    text="双击下方表格里的那一行，把它认成「你的 AP」——之后它不再算进干扰，只看邻居。",
                    fg=DIM)
            return
        mch = mine["ch"]
        table = self.st5 if mch > 14 else self.st2
        cell = table.get(mch) or {"w": 0.0, "best": -100, "n": 0}
        ms = rep.score_of(cell)
        band = "5G" if mch > 14 else "2.4G"
        if ms >= 2.5:
            lvl, col = "邻居很挤", "#cf222e"
        elif ms >= 1.0:
            lvl, col = "邻居一般", "#9a6700"
        else:
            lvl, col = "邻居干净", "#1a7f37"
        self.lbl_mine.configure(
            text="你的 AP：{}  {}dBm @ ch{}（{}）   同信道邻居干扰 {:.1f} — {}".format(
                mine["ssid"] or "(隐藏)", mine["rssi"], mch, band, ms, lvl),
            fg=col)
        self.mine_info = (mch, ms, band)

    def update_cmp(self):
        if self.baseline is None:
            self.lbl_cmp.configure(text="")
            return
        if self.mine_info is None:
            self.lbl_cmp.configure(
                text="基线 {} 存于 ch{}；现在没认到你的 AP，没法比。".format(
                    self.baseline["t"], self.baseline["mine"][0]), fg=DIM)
            return
        bch, bs = self.baseline["mine"][0], self.baseline["mine"][1]
        cch, cs = self.mine_info[0], self.mine_info[1]
        if cch == bch:
            self.lbl_cmp.configure(
                text="基线 {}：还在 ch{}，同信道邻居干扰 {:.1f} → {:.1f}（信道没搬）".format(
                    self.baseline["t"], cch, bs, cs), fg=DIM)
        elif cs < bs:
            pct = (bs - cs) / bs * 100.0 if bs > 0 else 0.0
            self.lbl_cmp.configure(
                text="基线 {}：ch{} 干扰 {:.1f} → 现在 ch{} 干扰 {:.1f}    改善 {:.0f}%".format(
                    self.baseline["t"], bch, bs, cch, cs, max(0.0, pct)), fg="#1a7f37")
        else:
            self.lbl_cmp.configure(
                text="基线 {}：ch{} 干扰 {:.1f} → 现在 ch{} 干扰 {:.1f}    {:.1f} 更差，这个信道别待".format(
                    self.baseline["t"], bch, bs, cch, cs, cs - bs), fg="#cf222e")

    # ---------- 标记与基线 ----------
    def on_pick(self, ev):
        iid = self.tree.identify_row(ev.y)
        if not iid:
            return
        vals = self.tree.item(iid, "values")
        if len(vals) < 5:
            return
        mac = vals[4]
        self.mine_mac = None if self.mine_mac == mac else mac
        if self.last_rows:
            self.apply(self.last_rows)
        self.status.configure(text="已标记你的 AP" if self.mine_mac else "已取消标记")

    def save_baseline(self):
        if self.mine_info is None:
            self.status.configure(text="先双击表格里的那一行，把它认成「你的 AP」，才能存基线")
            return
        self.baseline = {"t": time.strftime("%H:%M:%S"),
                         "mine": (self.mine_info[0], self.mine_info[1]),
                         "n": len(self.last_rows)}
        self.lbl_cmp.configure(
            text="已存基线 {}：ch{} 邻居干扰 {:.1f} ——去改路由器，改完回来点扫描".format(
                self.baseline["t"], self.baseline["mine"][0], self.baseline["mine"][1]),
            fg=BLUE)
        self.status.configure(text="基线已保存")

    def clear_marks(self):
        self.mine_mac = None
        self.baseline = None
        self.mine_info = None
        self.lbl_cmp.configure(text="")
        self.status.configure(text="标记已清空")
        if self.last_rows:
            self.apply(self.last_rows)

    def export_report(self):
        """把当前这次扫描直接渲染成单文件 HTML 报告并打开。"""
        if not self.last_rows:
            self.status.configure(text="先点一次「扫描」，有数据才能出报告")
            return
        try:
            out = time.strftime("chan5g_report_%Y%m%d_%H%M%S.html")
            doc = rep.build_html(self.last_rows, "界面实时扫描", types.SimpleNamespace(rounds=[]))
            path = os.path.join(HERE, out)
            with open(path, "w", encoding="utf-8") as f:
                f.write(doc)
            os.startfile(path)
            self.status.configure(text="报告已生成：" + out)
        except Exception as e:
            self.status.configure(text="出报告失败：{}: {}".format(type(e).__name__, e))

    def _reflow(self, w):
        wl = max(240, w - 16)
        for name in ("lbl_note", "lbl_mine", "lbl_cmp"):
            lb = getattr(self, name, None)
            if lb is None:
                continue
            try:
                lb.configure(wraplength=wl)
            except tk.TclError:
                pass

    def redraw(self):
        if not hasattr(self, "st5"):
            return
        self.root.update_idletasks()
        self.draw_bars(self.cv5, self.st5, rep.CH5, self.plan[0], "5GHz 信道干扰")
        self.draw_bars(self.cv2, self.st2, rep.CH2, None, "2.4GHz 信道干扰")

    def draw_bars(self, cv, stats, channels, want, title):
        cv.delete("all")
        W = max(cv.winfo_width(), 240)
        H = max(cv.winfo_height(), 140)
        ml, mr, mt, mb = 34, 10, 24, 24
        pw, ph = W - ml - mr, H - mt - mb
        n = len(channels)
        slot = pw / n
        bw = max(4.0, slot * 0.62)

        cv.create_text(ml - 4, 12, text=title, anchor="w", fill=DIM, font=FONT_S)

        max_s = max((rep.score_of(stats[c]) for c in channels), default=0.0)
        top = nice_top(max_s)
        for i in range(5):
            y = mt + ph - ph * i / 4
            cv.create_line(ml, y, W - mr, y, fill="#eaeef2")
            cv.create_text(ml - 5, y, text="{:g}".format(top * i / 4),
                           anchor="e", fill="#8c959f", font=FONT_XS)

        cv.create_line(ml, mt + ph, W - mr, mt + ph, fill="#d0d7de")

        for i, ch in enumerate(channels):
            e = stats[ch]
            s = rep.score_of(e)
            x = ml + slot * i + (slot - bw) / 2
            if e["n"]:
                h = ph * min(s, top) / top
                cv.create_rectangle(x, mt + ph - h, x + bw, mt + ph,
                                    fill=bar_color(s, ch in rep.DFS), outline="")
                cv.create_text(x + bw / 2, mt + ph - h - 7, text=str(e["n"]),
                               fill=DIM, font=FONT_XS)
            else:
                cv.create_rectangle(x, mt + ph - 2, x + bw, mt + ph,
                                    fill="#eef1f5", outline="")
            cv.create_text(x + bw / 2, mt + ph + 9,
                           text=str(ch) + ("*" if ch in rep.DFS else ""),
                           fill=DIM, font=FONT_XS)
            if want == ch:
                cv.create_rectangle(x - 2, mt, x + bw + 2, mt + ph,
                                    outline=BLUE, width=2)

    def fill_table(self, rows):
        self.tree.delete(*self.tree.get_children())
        for r in sorted(rows, key=lambda r: -r["rssi"]):
            tag = "strong" if r["rssi"] >= -60 else ("mid" if r["rssi"] >= -75 else "weak")
            mine = r["mac"] == self.mine_mac
            self.tree.insert("", "end", tags=(tag, "mine") if mine else (tag,),
                             values=("★ " + (r["ssid"] or "(隐藏)") if mine else (r["ssid"] or "(隐藏)"),
                                     r["rssi"], r["ch"], r["band"], r["mac"]))

    def on_close(self):
        try:
            if self.s is not None:
                self.s.close()
        except Exception:
            pass
        self.root.destroy()


# ---------------------------------------------------------------- 入口

def run_test(port):
    print("串口", port or "(自动)")
    s = bp.open_port(port or bp.find_port())
    try:
        bp.do_push(s, SCAN_JS, SCAN_DEV)
        out = bp.do_cmd(s, "js " + SCAN_DEV, quiet=2.0, total=45, until=b">>>END")
    finally:
        s.close()
    rows = parse_scan(out)
    print("解析到 {} 个 AP".format(len(rows)))
    if not rows:
        print("没数据，输出尾巴：")
        print(out[-500:])
        return 1
    st5 = rep.aggregate(rows, rep.CH5)
    st2 = rep.aggregate(rows, rep.CH2)
    ch, w, note, dfs = rep.advise(st5)
    print("建议 {} / {}MHz   {}".format(ch, w, note))
    print("2.4G {} 个 / 5G {} 个".format(
        sum(1 for r in rows if r["band"] == "2.4G"),
        sum(1 for r in rows if r["band"] == "5G")))
    for r in sorted(rows, key=lambda r: -r["rssi"])[:6]:
        print("  {:<28} {:>4}dBm  ch{:<4} {}".format(
            r["ssid"] or "(隐藏)", r["rssi"], r["ch"], r["band"]))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=None)
    ap.add_argument("--test", action="store_true", help="不开窗，跑一次验证链路")
    ap.add_argument("--demo", action="store_true",
                    help="演示：扫完自动把最强 AP 认成「你的 AP」并存基线")
    args = ap.parse_args()

    if args.test:
        return run_test(args.port)

    root = tk.Tk()
    App(root, args.port, demo=args.demo)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
