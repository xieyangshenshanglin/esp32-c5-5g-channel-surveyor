#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chan5g_report.py —— 把 chan5g.js 产出的 CSV 渲染成离线 HTML 报告。

不用联网、不用装库，生成的 HTML 双击就能看，也能直接发给客户。

用法:
    python chan5g_report.py raw.csv                        # 输出 chan5g_report.html
    python chan5g_report.py raw.csv -o 三楼会议室.html      # 指定输出名
    python chan5g_report.py raw.csv --rounds 1,3           # 只分析第 1 和第 3 轮
"""

import argparse
import csv
import html
import math
import os
import sys
from datetime import datetime

# ---------------------------------------------------------------- 常量

CH5 = [36, 40, 44, 48, 52, 56, 60, 64, 100, 104, 108, 112, 116, 120,
       124, 128, 132, 136, 140, 144, 149, 153, 157, 161, 165]
CH2 = list(range(1, 14))
DFS = {52, 56, 60, 64, 100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 144}
CLEAN_BLOCKS = [[149, 153, 157, 161], [36, 40, 44, 48]]
DFS_BLOCKS = [[100, 104, 108, 112], [116, 120, 124, 128], [132, 136, 140, 144]]
CLEAN_CH = [149, 153, 157, 161, 165, 36, 40, 44, 48]
NOISE_FLOOR = -75          # 强于这个值的邻居才算真碍事
PRETTY_SSID = True         # 表里显示 SSID，False 则只显示 MAC


# ---------------------------------------------------------------- 判定逻辑（与 chan5g.js 同一把尺子）


def apw(rssi):
    """单个 AP 对信道选择的干扰权重：越强越碍事，太弱的当没有。"""
    if rssi >= -60:
        return 3.0
    if rssi >= -70:
        return 2.0
    if rssi >= -80:
        return 1.0
    if rssi >= -88:
        return 0.3
    return 0.1


def score_of(e):
    """信道总干扰分：加权和 + 有个能打的邻居再加 1。与 chan5g.js 的 chScore 一致。"""
    return e["w"] + (1.0 if e["best"] >= -70 else 0.0)


def is_free(e):
    """这个信道能不能当空地用：没 AP，或者最强的那个还没到 -75dBm。

    这条是整个判定的关键。老版本要求"块内一个 AP 都没有"才敢推荐，
    结果 -85dBm 隔着几堵墙的邻居也能否掉整段 80MHz 频谱，太亏。
    """
    return e is None or e["n"] == 0 or e["best"] < NOISE_FLOOR


def clean_block(stats, blk):
    return all(is_free(stats.get(c)) for c in blk)


def advise(stats):
    """四阶段判定：整块 80MHz → 相邻 40MHz → 单信道 20MHz → 才退 DFS。"""
    # 1) 非 DFS 段整块 80MHz
    for blk in CLEAN_BLOCKS:
        if clean_block(stats, blk):
            note = "{} 整块没有像样的邻居（>-75dBm 的都没有），可以开满 80MHz".format(
                "/".join(str(c) for c in blk))
            if blk is CLEAN_BLOCKS[0]:
                note += "；149 段功率上限比 36 段高，首选这一段"
            return blk[0], 80, note, False

    # 2) 相邻两信道空 → 40MHz
    for blk in CLEAN_BLOCKS:
        for i in range(len(blk) - 1):
            a, b = blk[i], blk[i + 1]
            if is_free(stats.get(a)) and is_free(stats.get(b)):
                return a, 40, "{} 和 {} 都没有强邻居，40MHz 够稳".format(a, b), False

    # 3) 单信道挑最轻
    best, best_s = None, 1e9
    for ch in CLEAN_CH:
        s = score_of(stats[ch])
        if s < best_s:
            best, best_s = ch, s
    if best_s < 2.5:
        note = "非 DFS 段里只剩 {} 相对干净（分值 {:.1f}），先按 20MHz 保稳".format(best, best_s)
        if best == 165:
            note += "；165 是单信道，部分网卡扫不到，能接受 40MHz 就用 157"
        return best, 20, note, False

    # 4) 非 DFS 全废，退到 DFS
    for blk in DFS_BLOCKS:
        if clean_block(stats, blk):
            return blk[0], 80, "非 DFS 段全被占了，只能退 DFS（雷达优先，可能被强制跳开）", True

    best, best_s = None, 1e9
    for ch in CH5:
        s = score_of(stats[ch])
        if s < best_s:
            best, best_s = ch, s
    return best, 20, "整段 5G 都挤满了，{} 相对最轻（分值 {:.1f}），也可以干脆不动".format(best, best_s), True


# ---------------------------------------------------------------- 数据

def load(path):
    """按列顺序从右往左切：格式是 ms,ssid,mac,rssi,channel,band,enc。

    ssid 里允许有逗号（现场就有 "TP-LINK,5G" 这种），所以不能简单 split 后
    按下标取，而是钉住右边 5 个定长字段，中间剩下的整体当 ssid。
    空行、表头行、串口残留的杂行一律跳过。
    """
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for ln in f:
            ln = ln.strip()
            if not ln or ln.lower().startswith("ms,"):
                continue
            parts = ln.split(",")
            if len(parts) < 7:
                continue
            try:
                rows.append({
                    "ms": int(parts[0]),
                    "ssid": ",".join(parts[1:-5]).strip(),
                    "mac": parts[-5].strip(),
                    "rssi": int(parts[-4]),
                    "ch": int(parts[-3]),
                    "band": parts[-2].strip(),
                    "enc": parts[-1].strip(),
                })
            except (ValueError, TypeError):
                continue
    return rows


def split_rounds(rows):
    buckets = {}
    for r in rows:
        buckets.setdefault(r["ms"], []).append(r)
    return sorted(buckets.items())


def aggregate(rows, channels):
    st = {c: {"n": 0, "best": -999, "sum": 0, "w": 0.0, "aps": []} for c in channels}
    for r in rows:
        if r["ch"] not in st:
            continue
        e = st[r["ch"]]
        e["n"] += 1
        e["best"] = max(e["best"], r["rssi"])
        e["sum"] += r["rssi"]
        e["w"] += apw(r["rssi"])
        e["aps"].append(r)
    for e in st.values():
        e["avg"] = e["sum"] / e["n"] if e["n"] else None
    return st


def name_of(r):
    if PRETTY_SSID and r["ssid"]:
        return r["ssid"]
    return r["mac"]


# ---------------------------------------------------------------- SVG

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


def svg_bars(stats, channels, title, want_ch=None, height=300):
    W = 980
    ml, mr, mt, mb = 46, 14, 34, 52
    plot_w = W - ml - mr
    plot_h = height - mt - mb
    n = len(channels)
    slot = plot_w / n
    bw = max(6.0, slot * 0.62)

    max_s = max((score_of(stats[c]) for c in channels), default=0.0)
    # 上限取整齐的数，刻度才是 0/1/2/3/4，而不是 0/2.6/5.2/7.7
    if max_s <= 5:
        top = max(1.0, float(math.ceil(max_s)))
    elif max_s <= 10:
        top = float(math.ceil(max_s / 2) * 2)
    else:
        top = float(math.ceil(max_s / 4) * 4)

    def y_of(v):
        return mt + plot_h - (min(v, top) / top) * plot_h

    parts = ['<svg viewBox="0 0 {} {}" class="chart" xmlns="http://www.w3.org/2000/svg">'.format(W, height)]
    parts.append('<text x="{}" y="20" class="ctitle">{}</text>'.format(ml, html.escape(title)))

    steps = 4
    for i in range(steps + 1):
        v = top * i / steps
        y = y_of(v)
        parts.append('<line x1="{:.1f}" y1="{:.1f}" x2="{:.1f}" y2="{:.1f}" class="grid"/>'.format(ml, y, W - mr, y))
        lbl = "{:g}".format(v)
        parts.append('<text x="{:.1f}" y="{:.1f}" class="yticks" text-anchor="end">{}</text>'.format(ml - 6, y + 4, lbl))

    parts.append('<line x1="{:.1f}" y1="{:.1f}" x2="{:.1f}" y2="{:.1f}" class="axis"/>'.format(
        ml, mt + plot_h, W - mr, mt + plot_h))

    for i, ch in enumerate(channels):
        e = stats[ch]
        s = score_of(e)
        x = ml + slot * i + (slot - bw) / 2
        is_dfs = ch in DFS

        tip = ["信道 {}{}".format(ch, "（DFS）" if is_dfs else ""),
               "AP 数：{}".format(e["n"])]
        if e["n"]:
            tip.append("最强：{} dBm".format(e["best"]))
            tip.append("平均：{:.0f} dBm".format(e["avg"]))
            tip.append("干扰分：{:.1f}".format(s))
            top_ap = max(e["aps"], key=lambda r: r["rssi"])
            if top_ap["ssid"]:
                tip.append("最强 SSID：{}".format(top_ap["ssid"]))
        tipstr = "\n".join(tip)

        if e["n"] == 0:
            parts.append('<rect x="{:.1f}" y="{:.1f}" width="{:.1f}" height="3" fill="#eef1f5" rx="1.5">'
                         '<title>{}</title></rect>'.format(x, mt + plot_h - 3, bw, html.escape(tipstr)))
        else:
            h = (mt + plot_h) - y_of(s)
            col = bar_color(s, is_dfs)
            parts.append('<rect x="{:.1f}" y="{:.1f}" width="{:.1f}" height="{:.1f}" fill="{}" rx="2">'
                         '<title>{}</title></rect>'.format(x, y_of(s), bw, h, col, html.escape(tipstr)))
            parts.append('<text x="{:.1f}" y="{:.1f}" class="cnt" text-anchor="middle">{}</text>'.format(
                x + bw / 2, y_of(s) - 5, e["n"]))

        lbl = str(ch) + ("*" if is_dfs else "")
        parts.append('<text x="{:.1f}" y="{:.1f}" class="xticks" text-anchor="middle">{}</text>'.format(
            x + bw / 2, mt + plot_h + 18, lbl))

        if want_ch is not None and ch == want_ch:
            parts.append('<rect x="{:.1f}" y="{:.1f}" width="{:.1f}" height="{:.1f}" '
                         'fill="none" stroke="#0969da" stroke-width="2" rx="3"/>'.format(
                             x - 3, mt + 2, bw + 6, plot_h - 1))

    parts.append("</svg>")
    return "\n".join(parts)


def svg_rssi_hist(rows, title, height=210):
    W, ml, mr, mt, mb = 980, 46, 14, 34, 44
    plot_w, plot_h = W - ml - mr, height - mt - mb
    edges = [-100, -90, -85, -80, -75, -70, -65, -60, -55, -40]
    labels = ["≤-90", "-90~-85", "-85~-80", "-80~-75", "-75~-70",
              "-70~-65", "-65~-60", "-60~-55", ">-55"]
    counts = [0] * len(labels)
    for r in rows:
        v = r["rssi"]
        for i in range(len(edges) - 1):
            if edges[i] <= v < edges[i + 1]:
                counts[i] += 1
                break
    mx = max(counts) or 1
    slot = plot_w / len(labels)
    bw = slot * 0.6
    parts = ['<svg viewBox="0 0 {} {}" class="chart" xmlns="http://www.w3.org/2000/svg">'.format(W, height)]
    parts.append('<text x="{}" y="20" class="ctitle">{}</text>'.format(ml, html.escape(title)))
    for i in range(5):
        y = mt + plot_h - plot_h * i / 4
        parts.append('<line x1="{:.1f}" y1="{:.1f}" x2="{:.1f}" y2="{:.1f}" class="grid"/>'.format(ml, y, W - mr, y))
    for i, lb in enumerate(labels):
        c = counts[i]
        h = plot_h * c / mx if mx else 0
        x = ml + slot * i + (slot - bw) / 2
        col = "#da3633" if edges[i] >= -70 else ("#d29922" if edges[i] >= -80 else "#2ea44f")
        if c:
            parts.append('<rect x="{:.1f}" y="{:.1f}" width="{:.1f}" height="{:.1f}" fill="{}" rx="2">'
                         '<title>{} dBm : {} 个</title></rect>'.format(
                             x, mt + plot_h - h, bw, h, col, lb, c))
            parts.append('<text x="{:.1f}" y="{:.1f}" class="cnt" text-anchor="middle">{}</text>'.format(
                x + bw / 2, mt + plot_h - h - 5, c))
        parts.append('<text x="{:.1f}" y="{:.1f}" class="xticks" text-anchor="middle">{}</text>'.format(
            x + bw / 2, mt + plot_h + 18, lb))
    parts.append("</svg>")
    return "\n".join(parts)


# ---------------------------------------------------------------- HTML

CSS = """
:root{
  --bg:#f6f8fa; --card:#fff; --line:#d8dee4; --ink:#1f2328; --dim:#656d76;
  --green:#2ea44f; --yellow:#d29922; --red:#da3633; --blue:#0969da; --grey:#9aa4b2;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.65 -apple-system,"Segoe UI","Microsoft YaHei",system-ui,sans-serif;}
.wrap{max-width:1040px;margin:0 auto;padding:32px 20px 64px}
h1{font-size:26px;margin:0 0 6px;letter-spacing:.3px}
h2{font-size:18px;margin:0 0 14px;padding-left:10px;border-left:3px solid var(--blue)}
.meta{color:var(--dim);font-size:13px;margin-bottom:24px}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;
  padding:20px 22px;margin-bottom:18px;box-shadow:0 1px 2px rgba(31,35,40,.04)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.card .k{font-size:12px;color:var(--dim);letter-spacing:.5px}
.card .v{font-size:24px;font-weight:600;margin-top:4px;font-variant-numeric:tabular-nums}
.card .s{font-size:12px;color:var(--dim);margin-top:2px}
.chart{width:100%;height:auto;display:block}
.ctitle{font-size:13px;fill:var(--dim)}
.grid{stroke:#eaeef2;stroke-width:1}
.axis{stroke:#d0d7de;stroke-width:1}
.yticks{font-size:10px;fill:#8c959f}
.xticks{font-size:10.5px;fill:var(--dim)}
.cnt{font-size:10px;fill:var(--dim);font-weight:600}
.legend{display:flex;gap:18px;flex-wrap:wrap;font-size:12.5px;color:var(--dim);margin-top:10px}
.legend i{display:inline-block;width:11px;height:11px;border-radius:2px;margin-right:5px;
  vertical-align:-1px}
.verdict{display:grid;grid-template-columns:auto 1fr;gap:10px 18px;font-size:14.5px;margin:0}
.verdict dt{color:var(--dim);white-space:nowrap}
.verdict dd{margin:0;font-weight:500}
.big{font-size:19px;font-weight:700;color:var(--blue)}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid #eaeef2}
th{color:var(--dim);font-weight:600;font-size:12.5px;letter-spacing:.3px}
tbody tr:hover{background:#f6f8fa}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.bar{display:inline-block;height:9px;border-radius:2px;vertical-align:0}
.pill{display:inline-block;padding:1px 7px;border-radius:20px;font-size:11.5px;font-weight:600}
.pill.g{background:#dafbe1;color:#1a7f37}
.pill.y{background:#fff8c5;color:#9a6700}
.pill.r{background:#ffebe9;color:#cf222e}
.pill.d{background:#eaeef2;color:#57606a}
.warn{background:#fff8c5;border:1px solid #d4a72c;border-radius:8px;padding:12px 14px;
  font-size:13.5px;margin-top:14px}
.note{color:var(--dim);font-size:12.5px;margin-top:12px}
footer{color:var(--dim);font-size:12px;text-align:center;margin-top:26px}
"""


def fmt_dbm(v):
    return "{:.0f}".format(v) if v is not None else "-"


def chip_for(s):
    if s >= 5:
        return '<span class="pill r">拥挤</span>'
    if s >= 2:
        return '<span class="pill y">一般</span>'
    if s > 0:
        return '<span class="pill g">空闲</span>'
    return '<span class="pill d">全空</span>'


def build_html(rows, src_name, args):
    rounds = split_rounds(rows)
    if not rounds:
        sys.exit("CSV 里没有可用的数据行")

    if args.rounds:
        want = set(args.rounds)
        rounds = [r for i, r in enumerate(rounds, 1) if i in want] or rounds

    last_ms, last_rows = rounds[-1]
    st5 = aggregate(last_rows, CH5)
    st2 = aggregate(last_rows, CH2)
    ch, width, note, dfs_warn = advise(st5)

    ap5 = sorted([r for r in last_rows if r["band"] == "5G"], key=lambda r: -r["rssi"])
    self_ap = ap5[0] if ap5 else None

    n5 = sum(1 for r in last_rows if r["band"] == "5G")
    n2 = sum(1 for r in last_rows if r["band"] == "2.4G")

    busiest5 = max(CH5, key=lambda c: (score_of(st5[c]), st5[c]["n"]))
    be = st5[busiest5]
    busiest2 = max(CH2, key=lambda c: (score_of(st2[c]), st2[c]["n"]))
    be2 = st2[busiest2]

    cards = """
    <div class="cards">
      <div class="card"><div class="k">AP 总数</div><div class="v">{tot}</div>
        <div class="s">2.4G {n2} / 5G {n5}</div></div>
      <div class="card"><div class="k">建议信道</div><div class="v">{ch}</div>
        <div class="s">带宽 {w}MHz</div></div>
      <div class="card"><div class="k">5G 最挤</div><div class="v">{busy}</div>
        <div class="s">{bn} 个 AP，最强 {bb}dBm</div></div>
      <div class="card"><div class="k">2.4G 最挤</div><div class="v">{busy2}</div>
        <div class="s">{bn2} 个 AP，最强 {bb2}dBm</div></div>
    </div>
    """.format(tot=len(last_rows), n2=n2, n5=n5, ch=ch, w=width,
               busy=busiest5, bn=be["n"], bb=fmt_dbm(be["best"]),
               busy2=busiest2, bn2=be2["n"], bb2=fmt_dbm(be2["best"]))

    verdict = """
    <dl class="verdict">
      <dt>建议</dt><dd><span class="big">{ch} 信道 / {w}MHz</span></dd>
      <dt>依据</dt><dd>{note}</dd>
      <dt>怎么改</dt><dd>路由器后台 → 5G 无线设置 → 信道 <b>{ch}</b> / 带宽 <b>{w}MHz</b></dd>
    </dl>
    """.format(ch=ch, w=width, note=html.escape(note))

    if dfs_warn:
        verdict += ('<div class="warn">⚠ 推荐的信道落在 DFS 段（52–144）。'
                    '这段是雷达优先频段，路由器检测到雷达信号会被强制跳开，'
                    '稳定性不如 149 段。</div>')

    if self_ap is not None:
        verdict += ('<div class="note">你最强的 5G AP 是 <b>{}</b>，在 <b>{}</b> 信道（{} dBm）。'
                    '如果推荐的正好是它，说明不用动；如果不是，把它改到推荐信道上。'
                    '</div>').format(html.escape(name_of(self_ap)), self_ap["ch"],
                                     fmt_dbm(self_ap["rssi"]))

    legend = """
    <div class="legend">
      <span><i style="background:#2ea44f"></i>空闲（分值 &lt; 2）</span>
      <span><i style="background:#d29922"></i>一般（2 ~ 5）</span>
      <span><i style="background:#da3633"></i>拥挤（≥ 5）</span>
      <span><i style="background:#9aa4b2"></i>DFS 信道（雷达优先）</span>
      <span><i style="background:#eef1f5;border:1px solid #d8dee4"></i>无 AP</span>
      <span>柱上数字 = 该信道 AP 个数；蓝框 = 推荐信道</span>
    </div>
    """

    chart5 = svg_bars(st5, CH5, "5GHz 各信道干扰权重（分值越高越该躲开）", want_ch=ch)
    chart2 = svg_bars(st2, CH2, "2.4GHz 各信道干扰分布")
    hist = svg_rssi_hist(last_rows, "全部 AP 的信号强度分布（右侧越强，越构成实际干扰）")

    trs = []
    for chn in CH5:
        e = st5[chn]
        if e["n"] == 0:
            continue
        s = score_of(e)
        top_ap = max(e["aps"], key=lambda r: r["rssi"])
        pct = min(100.0, s / 12.0 * 100.0)
        col = bar_color(s, chn in DFS)
        trs.append(
            "<tr><td>{ch}{dfs}</td><td class='num'>{n}</td>"
            "<td class='num'>{best}</td><td class='num'>{avg}</td>"
            "<td><span class='bar' style='width:{w}px;background:{col};margin-right:7px'></span>"
            "<span class='num'>{s:.1f}</span></td><td>{chip}</td>"
            "<td>{ssid}</td><td class='num'>{rssi}</td></tr>".format(
                ch=chn, dfs="*" if chn in DFS else "", n=e["n"],
                best=fmt_dbm(e["best"]), avg=fmt_dbm(e["avg"]),
                w=max(6.0, pct * 1.1), col=col, s=s, chip=chip_for(s),
                ssid=html.escape(name_of(top_ap)), rssi=fmt_dbm(top_ap["rssi"])))

    table = """
    <table><thead><tr>
      <th>信道</th><th class="num">AP 数</th><th class="num">最强</th>
      <th class="num">平均</th><th>干扰</th><th>状态</th>
      <th>最强 AP</th><th class="num">RSSI</th>
    </tr></thead><tbody>{rows}</tbody></table>
    """.format(rows="\n".join(trs))

    trend = ""
    if len(rounds) > 1:
        trs2 = []
        for i, (ms, rws) in enumerate(rounds, 1):
            s5 = aggregate(rws, CH5)
            c, w, nt, dw = advise(s5)
            b = max(CH5, key=lambda x: score_of(s5[x]))
            trs2.append(
                "<tr><td class='num'>{i}</td><td class='num'>{t:.1f}s</td>"
                "<td class='num'>{tot}</td><td class='num'>{n2}</td>"
                "<td class='num'>{n5}</td><td>{c} / {w}MHz</td><td class='num'>{b}</td>"
                "<td>{nt}</td></tr>".format(
                    i=i, t=ms / 1000.0, tot=len(rws),
                    n2=sum(1 for r in rws if r["band"] == "2.4G"),
                    n5=sum(1 for r in rws if r["band"] == "5G"),
                    c=c, w=w, b=b, nt=html.escape(nt)))
        trend = """
        <section><h2>多轮对比</h2>
        <table><thead><tr><th class="num">轮</th><th class="num">时刻</th>
          <th class="num">AP</th><th class="num">2.4G</th><th class="num">5G</th>
          <th>建议</th><th class="num">5G 最挤</th><th>依据</th>
        </tr></thead><tbody>{rows}</tbody></table>
        <div class="note">三轮反复推荐同一个信道，说明结论稳定；来回跳说明环境在变
        （有人在用微波炉、或者邻居的 AP 在自动选信道），这种环境别选死信道。</div>
        </section>""".format(rows="\n".join(trs2))

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    doc = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>5GHz 信道普查报告</title>
<style>{css}</style></head><body><div class="wrap">
<h1>5GHz 信道普查报告</h1>
<div class="meta">数据源 {src} · 生成于 {now} · 共 {nr} 轮 {nrow} 条 AP 记录</div>
{cards}
<section><h2>结论</h2>{verdict}</section>
<section><h2>5GHz 信道占用</h2>{chart5}{legend}</section>
<section><h2>2.4GHz 信道占用</h2>{chart2}</section>
<section><h2>信号强度分布</h2>{hist}
  <div class="note">-70dBm 以内的 AP 才真正干扰你；-85dBm 以下的隔着几堵墙，
  可以不理会。"强邻"这条线取在 -75dBm，跟脚本里的判定是同一个数。</div></section>
<section><h2>5GHz 信道明细</h2>{table}</section>
{trend}
<footer>chan5g.js / chan5g_report.py — ESP32-C5 无线现场勘查</footer>
</div></body></html>""".format(
        css=CSS, src=html.escape(os.path.basename(src_name)), now=now, nr=len(rounds),
        nrow=len(rows), cards=cards, verdict=verdict, chart5=chart5,
        chart2=chart2, hist=hist, legend=legend, table=table, trend=trend)
    return doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", help="chan5_raw.csv")
    ap.add_argument("-o", "--out", default="chan5g_report.html")
    ap.add_argument("--rounds", default="",
                    help="只分析第几轮，逗号分隔，如 1,3；默认分析最后一轮")
    args = ap.parse_args()
    args.rounds = [int(x) for x in args.rounds.split(",") if x.strip().isdigit()]

    rows = load(args.csv)
    if not rows:
        sys.exit("{} 里没有解析出数据".format(args.csv))
    doc = build_html(rows, args.csv, args)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(doc)
    print("已生成 {} （{} 条记录）".format(args.out, len(rows)))


if __name__ == "__main__":
    main()
