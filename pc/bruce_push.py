#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bruce_push.py —— 把文件塞进 Bruce 固件（ESP32-C5 USB 串口 CLI），或把文件读回来。

设备侧协议（源码 src/core/serial_commands/helpers.cpp::_readFileFromSerial）：
  - 先发命令 `storage write <设备路径> <缓冲字节数>`
  - 设备回 "Reading input data from serial buffer until EOF"
  - 然后把内容按行发过去，每行以 \\n 结尾
  - 最后发一行 EOF 结束
  - 5 秒没有新数据设备会自己超时

用法:
  python bruce_push.py push chan5g.js /scripts/chan5g.js
  python bruce_push.py run  /scripts/chan5g.js
  python bruce_push.py pushrun chan5g.js /scripts/chan5g.js
  python bruce_push.py read /chan5_sum.csv out.csv
  python bruce_push.py cmd "storage free littlefs"
  python bruce_push.py ports

默认串口自动找 Espressif USB 设备（VID 303A），找不到就显式传 --port。
"""

import argparse
import os
import sys
import time

try:
    import serial
    import serial.tools.list_ports as list_ports
except ImportError:
    sys.exit("缺 pyserial：pip install pyserial")

VID_ESPRESSIF = 0x303A

_HERE = os.path.dirname(os.path.abspath(__file__))


def find_asset(name):
    """按顺序在几个常见位置找随包分发的设备端脚本。

    单目录开发时文件就在旁边；发行包里 pc/ 和 device/ 是分开的，
    所以要往上一级找 device/。
    """
    cands = [
        os.path.join(_HERE, name),
        os.path.join(_HERE, "..", "device", name),
        os.path.join(_HERE, "..", "device-scripts", name),
        os.path.join(_HERE, "..", "scripts", name),
    ]
    for c in cands:
        if os.path.exists(c):
            return os.path.abspath(c)
    return os.path.abspath(cands[0])


def find_port():
    for p in list_ports.comports():
        if p.vid == VID_ESPRESSIF:
            return p.device
    return None


def open_port(port, baud=115200):
    # ESP32 的自动复位电路由 RTS/DTR 驱动：pyserial 打开串口时默认 rts=True，
    # 会拉出一个复位脉冲，导致每连一次板子就重启一次（启动日志刷屏、上传被打断）。
    # 关键：必须在 open() 之前把这两个状态记成 False，open 时才会按低电平应用；
    # open 之后再设会在 Windows 驱动上偶发阻塞。
    s = serial.Serial()
    s.port = port
    s.baudrate = baud
    s.timeout = 0.2
    s.write_timeout = 5
    s.dsrdtr = False
    s.rtscts = False
    try:
        s.dtr = False
        s.rts = False
    except Exception:
        pass
    s.open()
    time.sleep(2.0)          # 万一还是重启了，等它把启动日志吐完
    s.reset_input_buffer()
    return s


def drain(s, quiet=0.6, total=3.0):
    """读到连续 quiet 秒没数据为止"""
    buf = b""
    t0 = time.time()
    last = time.time()
    while True:
        d = s.read(4096)
        if d:
            buf += d
            last = time.time()
        else:
            if buf and time.time() - last > quiet:
                break
            if time.time() - t0 > total:
                break
        time.sleep(0.02)
    return buf.decode("utf-8", "replace")


def send_line(s, line):
    s.write((line + "\n").encode("utf-8"))
    s.flush()


def do_cmd(s, cmd, wait=2.0, quiet=0.6, total=None, until=None):
    """发一条命令并边跑边收输出。

    不能用 time.sleep(wait) 再一次性读：脚本跑几十秒、输出十几 KB，
    而 USB CDC 接收缓冲只有几 KB，睡完再读只能拿到尾巴。

    until：收到这段字节就立刻返回。脚本扫描期间会静默好几秒，
    光靠 quiet 超时判断会把扫描误当成结束。
    """
    s.reset_input_buffer()
    send_line(s, cmd)
    buf = b""
    t0 = time.time()
    lim = total if total else max(3.0, wait)
    last = time.time()
    while time.time() - t0 < lim:
        d = s.read(4096)
        if d:
            buf += d
            last = time.time()
            if until and until in buf:
                time.sleep(0.06)
                buf += s.read(4096)
                break
            continue
        if time.time() - last > quiet and not until:
            # 指定了 until 就只用总时长兜底：脚本扫描期间会长达几秒没有任何输出，
            # 用静默超时判断会把"正在扫"误当成"扫完了"。
            break
        time.sleep(0.02)
    return buf.decode("utf-8", "replace")


def do_push(s, local_path, dev_path, buf_size=None):
    with open(local_path, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8")
    # 统一换行，设备按 \n 切行
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.endswith("\n"):
        text += "\n"
    size = buf_size if buf_size else len(text.encode("utf-8")) + 4096

    cmd = "storage write {} {}".format(dev_path, size)
    s.reset_input_buffer()
    send_line(s, cmd)
    time.sleep(0.5)
    head = drain(s, quiet=0.5, total=4.0)
    if "Reading input data" not in head:
        print("!! 设备没有进入接收状态，回显如下：")
        print(head)
        return False
    print("设备已就绪，开始发送 {} 字节…".format(len(text.encode("utf-8"))))

    # 按行发送：设备端 helpers.cpp::_readFileFromSerial 就是
    # readStringUntil('\n') 一行行攒的。一次性灌 512 字节裸块会在 USB CDC 上丢字节
    # （实测 11517 字节只落盘 7762），逐行发才能保证每行都被完整切出来。
    for line in text.split("\n"):
        s.write((line + "\n").encode("utf-8"))
        s.flush()
        time.sleep(0.004)

    time.sleep(0.2)
    send_line(s, "EOF")
    time.sleep(0.8)
    tail = drain(s, quiet=0.8, total=5.0)
    if tail.strip():
        print(tail.strip())
    print("已发送 {}".format(dev_path))

    # 校验
    time.sleep(0.3)
    out = do_cmd(s, "storage stat {}".format(dev_path), wait=1.0)
    print(out.strip() if out.strip() else "(无 stat 输出)")
    return True


def do_read(s, dev_path, local_path=None, max_wait=8.0):
    s.reset_input_buffer()
    send_line(s, "storage read {}".format(dev_path))
    time.sleep(0.4)
    body = drain(s, quiet=1.0, total=max_wait)
    if local_path:
        with open(local_path, "w", encoding="utf-8") as f:
            f.write(body)
        print("已写入 {} （{} 字节，含 CLI 回显）".format(local_path, len(body)))
    else:
        print(body)


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("action", choices=["push", "run", "pushrun", "read", "cmd", "ports"])
    ap.add_argument("a", nargs="?")
    ap.add_argument("b", nargs="?")
    ap.add_argument("--port", default=None)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--wait", type=float, default=0.0,
                    help="cmd 用：发命令后最多等多少秒再把输出读干净")
    args = ap.parse_args()

    if args.action == "ports":
        for p in list_ports.comports():
            mark = "  <-- Espressif" if p.vid == VID_ESPRESSIF else ""
            print("{:<8} {:04X}:{:04X}  {}{}".format(p.device, p.vid or 0, p.pid or 0, p.description, mark))
        return

    port = args.port or find_port()
    if not port:
        sys.exit("没找到 Espressif 串口，用 --port COMx 指定（先跑 python bruce_push.py ports 看）")

    s = open_port(port, args.baud)
    print("串口 {}".format(port))
    try:
        if args.action == "push":
            ok = do_push(s, args.a, args.b)
            sys.exit(0 if ok else 1)
        elif args.action == "run":
            print(do_cmd(s, "js {}".format(args.a), wait=1.5))
        elif args.action == "pushrun":
            ok = do_push(s, args.a, args.b)
            if not ok:
                sys.exit(1)
            print(do_cmd(s, "js {}".format(args.b), wait=2.0))
            # 脚本会自己跑完，继续读输出
            print(drain(s, quiet=2.0, total=120.0))
        elif args.action == "read":
            do_read(s, args.a, args.b)
        elif args.action == "cmd":
            # 静默窗口要大于脚本轮次之间的间隔（chan5g.js 的 GAP_MS=4000），
            # 否则每轮之间会被误判成"跑完了"而提前断开。
            print(do_cmd(s, args.a, wait=args.wait or 2.0,
                         quiet=20.0 if args.wait else 0.6))
    finally:
        s.close()


if __name__ == "__main__":
    main()
