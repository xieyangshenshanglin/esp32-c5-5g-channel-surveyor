#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chan5g_show.py —— 一条命令看结果。

    从设备取回两个 CSV → 生成 HTML 报告 → 自动用浏览器打开

用法:
    python chan5g_show.py                  # 取数据 + 生成 + 打开
    python chan5g_show.py --no-open        # 只生成不开浏览器
    python chan5g_show.py -o 三楼.html      # 指定输出文件名
    python chan5g_show.py --port COM9      # 指定串口
    python chan5g_show.py --local raw.csv  # 不吃串口，直接拿本地 CSV 出图
"""

import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DUMP_JS = bp.find_asset("dump.js")
DUMP_DEV = "/scripts/dump.js"
REPORT_PY = os.path.join(HERE, "chan5g_report.py")


def fetch(port=None, quiet=6.0, total=100.0):
    """从设备上把 CSV 拉回来。返回串口原始输出（含 BEGIN/END 标记）。"""
    import bruce_push as bp

    p = port or bp.find_port()
    if not p:
        sys.exit("没找到串口，确认板子插好了、驱动装了")
    print("串口 {}".format(p))
    s = bp.open_port(p)
    try:
        print("上传取数脚本 ...")
        bp.do_push(s, DUMP_JS, DUMP_DEV)
        print("读设备上的 CSV ...")
        return bp.do_cmd(s, "js " + DUMP_DEV, wait=60, quiet=quiet, total=total)
    finally:
        try:
            s.close()
        except Exception:
            pass


def parse(out):
    files = {}
    for m in re.finditer(r"<<<BEGIN ([^>\r\n]+)\r?\n(.*?)>>>END", out, re.S):
        files[m.group(1).strip()] = m.group(2)
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="chan5g_report.html")
    ap.add_argument("--port", default=None)
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--local", default=None, help="直接用本地 raw.csv，跳过串口")
    ap.add_argument("--rounds", default="")
    ap.add_argument("--quiet", type=float, default=6.0)
    args = ap.parse_args()

    if args.local:
        raw_path = args.local
        print("用本地文件 {}".format(raw_path))
    else:
        out = fetch(args.port, quiet=args.quiet)
        if "<<<BEGIN" not in out:
            print("设备没有回数据。原始输出尾巴：")
            print(out[-600:])
            return 1
        files = parse(out)
        raw = files.get("/chan5_raw.csv")
        if not raw or "ms," not in raw:
            print("设备上没有 /chan5_raw.csv。先在板子上跑一次普查：")
            print('  python bruce_push.py cmd "js /scripts/chan5g.js" --wait 100')
            return 1
        raw_path = os.path.join(HERE, "raw.csv")
        with open(raw_path, "w", encoding="utf-8", newline="") as f:
            f.write(raw.rstrip("\r\n") + "\n")
        n = sum(1 for ln in raw.splitlines() if ln.count(",") >= 6)
        print("取到 {} 条 AP 记录".format(n))
        if "/chan5_sum.csv" in files:
            with open(os.path.join(HERE, "sum.csv"), "w", encoding="utf-8", newline="") as f:
                f.write(files["/chan5_sum.csv"].rstrip("\r\n") + "\n")

    cmd = [sys.executable, REPORT_PY, raw_path, "-o", os.path.join(HERE, args.out)]
    if args.rounds:
        cmd += ["--rounds", args.rounds]
    r = subprocess.run(cmd, cwd=HERE)
    if r.returncode != 0:
        return r.returncode

    full = os.path.join(HERE, args.out)
    print("报告: {}".format(full))
    if not args.no_open:
        try:
            os.startfile(full)      # 只对 Windows 有效，够了
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
