// chan5g.js —— ESP32-C5 5GHz 信道普查仪（无屏幕 / 串口输出）
// 运行: js /scripts/chan5g.js
// 输出: USB 串口文本 + LittleFS 两个 CSV
// 注意: wifi.scan() 会把 WiFi 切到 STA 模式，热点会断开，跑完用 "wifi on" 恢复

var wifi = require('wifi');
var serial = require('serial');
var storage = require('storage');

// ================= 配置区 =================
var ROUNDS = 3;      // 扫描轮数（每轮 8-20 秒）
var GAP_MS = 4000;   // 轮间隔
var MAXAP = 10;      // 每轮列出最强 5G AP 条数
var SUM = { fs: 'littlefs', path: '/chan5_sum.csv' };
var RAW = { fs: 'littlefs', path: '/chan5_raw.csv' };
// =========================================

var CH5 = [36, 40, 44, 48, 52, 56, 60, 64, 100, 104, 108, 112, 116, 120,
           124, 128, 132, 136, 140, 144, 149, 153, 157, 161, 165];
var LOW = [36, 40, 44, 48];                  // UNII-1，室内低功率
var HIGHB = [149, 153, 157, 161];            // UNII-3，功率高，首选
var CLEAN_BLOCKS = [[149, 153, 157, 161], [36, 40, 44, 48]];
var DFS_BLOCKS = [[100, 104, 108, 112], [116, 120, 124, 128], [132, 136, 140, 144]];
var CLEAN_CH = [149, 153, 157, 161, 165, 36, 40, 44, 48];
var DFS = [52, 56, 60, 64, 100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 144];

function pad(s, n) { s = '' + s; while (s.length < n) s = ' ' + s; return s; }
function rpad(s, n) { s = '' + s; while (s.length < n) s = s + ' '; return s; }
function rep(c, n) { var s = ''; for (var i = 0; i < n; i++) s = s + c; return s; }
function z2(v) { return v < 10 ? '0' + v : '' + v; }
function isDfs(ch) { for (var i = 0; i < DFS.length; i++) if (DFS[i] == ch) return true; return false; }
function clean(s) { s = '' + s; return s.split(',').join(' ').split('\n').join(' ').split('\r').join(' '); }
function mmss(ms) {
  var t = Math.floor(ms / 1000);
  var m = Math.floor(t / 60);
  var s = t - m * 60;
  return z2(m) + ':' + z2(s);
}

// 一个 AP 对信道选择的干扰权重：越强越碍事，太弱的当没有
function apW(rssi) {
  if (rssi >= -60) return 3;
  if (rssi >= -70) return 2;
  if (rssi >= -80) return 1;
  if (rssi >= -88) return 0.3;
  return 0.1;
}

// ---------- 扫描一轮 ----------
function scanOnce() {
  var aps = wifi.scan();
  if (!aps || aps.length == 0) return null;

  var res = { total: aps.length, n2: 0, n5: 0, ch: {}, ap5: [], apAll: [], at: now() };
  for (var i = 0; i < aps.length; i++) {
    var a = aps[i];
    var ch = a.channel;
    res.apAll.push(a);
    if (ch > 14) {
      res.n5++;
      var k = '' + ch;
      if (!res.ch[k]) res.ch[k] = { n: 0, best: -999, sum: 0, w: 0 };
      var e = res.ch[k];
      e.n++;
      e.sum += a.RSSI;
      e.w += apW(a.RSSI);
      if (a.RSSI > e.best) e.best = a.RSSI;
      res.ap5.push(a);
    } else {
      res.n2++;
    }
  }
  return res;
}

// ---------- 插入排序（mquickjs 不保证 sort） ----------
function sortByRssi(arr) {
  var out = [];
  for (var i = 0; i < arr.length; i++) {
    var v = arr[i];
    var j = out.length;
    out.push(v);
    while (j > 0 && out[j - 1].RSSI < v.RSSI) { out[j] = out[j - 1]; j--; }
    out[j] = v;
  }
  return out;
}

// ---------- 单信道干扰分 ----------
function chScore(res, ch) {
  var e = res.ch['' + ch];
  if (!e) return 0;
  var s = e.w;
  if (e.best >= -70) s += 1;
  return s;
}
// 一个信道算不算"被占"：不是有 AP 就算，得强到 -75dBm 以内才算真碍事。
// 隔着几堵墙的 -85dBm 邻居在 80MHz 一段里根本够不到你，为它放弃整段频谱不值。
function isEmpty(res, ch) {
  var e = res.ch['' + ch];
  if (!e) return true;
  return e.best < -75;
}

function joinCh(blk) {
  var s = '';
  for (var i = 0; i < blk.length; i++) s = s + (i ? '/' : '') + blk[i];
  return s;
}

// ---------- 结论 ----------
function advise(res) {
  var i, b;

  // 1) 非 DFS 段找整块 80MHz 全空
  for (b = 0; b < CLEAN_BLOCKS.length; b++) {
    var blk = CLEAN_BLOCKS[b];
    var empty = true;
    for (i = 0; i < blk.length; i++) if (!isEmpty(res, blk[i])) empty = false;
    if (empty) {
      return { ch: blk[0], width: 80, dfs: false,
               note: joinCh(blk) + ' 整块没有像样的邻居（>-75dBm 的都没有），放心开满 80MHz' };
    }
  }

  // 2) 非 DFS 段相邻两信道空 → 40MHz
  for (i = 0; i < CLEAN_CH.length - 1; i++) {
    if (CLEAN_CH[i + 1] - CLEAN_CH[i] == 4 &&
        isEmpty(res, CLEAN_CH[i]) && isEmpty(res, CLEAN_CH[i + 1])) {
      return { ch: CLEAN_CH[i], width: 40, dfs: false,
               note: CLEAN_CH[i] + ' 和 ' + CLEAN_CH[i + 1] + ' 都没有强邻居，40MHz 够稳' };
    }
  }

  // 3) 非 DFS 单信道挑最轻
  var bestCh = -1, bestSc = 999999;
  for (i = 0; i < CLEAN_CH.length; i++) {
    var s1 = chScore(res, CLEAN_CH[i]);
    if (s1 < bestSc) { bestSc = s1; bestCh = CLEAN_CH[i]; }
  }
  if (bestSc < 2.5) {
    var tail = '';
    if (bestCh == 165) tail = '；165 是单信道，部分网卡挑不到，能接受 40MHz 就用 157';
    return { ch: bestCh, width: 20, dfs: false,
             note: '非 DFS 段里只剩 ' + bestCh + ' 相对干净，20MHz 保稳' + tail };
  }

  // 4) 非 DFS 全废，退到 DFS
  for (b = 0; b < DFS_BLOCKS.length; b++) {
    var blk2 = DFS_BLOCKS[b];
    var empty2 = true;
    for (i = 0; i < blk2.length; i++) if (!isEmpty(res, blk2[i])) empty2 = false;
    if (empty2) {
      return { ch: blk2[0], width: 80, dfs: true,
               note: joinCh(blk2) + ' 整块干净，但属 DFS 段' };
    }
  }

  var dfsCh = -1, dfsSc = 999999;
  for (i = 0; i < DFS.length; i++) {
    var s2 = chScore(res, DFS[i]);
    if (s2 < dfsSc) { dfsSc = s2; dfsCh = DFS[i]; }
  }
  return { ch: dfsCh, width: 20, dfs: true,
           note: '整段 5G 都挤满了，只能在 DFS 里挑个最轻的；也可以干脆留在原信道' };
}

// ---------- 最挤的信道 ----------
function worstOf(res) {
  var worstCh = -1, worstSc = -1, worstE = null;
  for (var i = 0; i < CH5.length; i++) {
    var ch = CH5[i];
    var e = res.ch['' + ch];
    if (!e) continue;
    var s = chScore(res, ch);
    if (s > worstSc) { worstSc = s; worstCh = ch; worstE = e; }
  }
  return { ch: worstCh, e: worstE };
}

// ---------- 打印一轮 ----------
function fmtRow(res, ch, maxSc) {
  var e = res.ch['' + ch];
  var sc = chScore(res, ch);
  var len = Math.round(sc * 10 / maxSc);
  var dfs = isDfs(ch) ? '*' : ' ';
  if (e) {
    return pad(ch, 4) + dfs + pad(e.n, 3) + pad(e.best, 6) + pad(Math.round(e.sum / e.n), 6) + '  ' + rep('#', len);
  }
  return pad(ch, 4) + dfs + pad(0, 3) + pad('-', 6) + pad('-', 6) + '  .';
}

function printRound(res, r) {
  var i;
  serial.println('');
  serial.println('---- 第 ' + r + '/' + ROUNDS + ' 轮  t+' + mmss(res.at) + ' ----');
  serial.println('AP 共 ' + res.total + ' 个   (2.4G ' + res.n2 + ' / 5G ' + res.n5 + ')');

  if (res.n5 == 0) {
    serial.println('!! 一个 5GHz AP 都没扫到');
    serial.println('!! 附近确实没 5G / 路由器只开了 2.4G / 这块板子被地区码限制');
  }

  var maxSc = 1;
  for (i = 0; i < CH5.length; i++) {
    var s = chScore(res, CH5[i]);
    if (s > maxSc) maxSc = s;
  }

  serial.println('');
  serial.println('信道  AP  最强  平均  干扰');

  // 36-48
  for (i = 0; i < LOW.length; i++) serial.println(fmtRow(res, LOW[i], maxSc));

  // DFS 段：全空就折叠成一行
  var dfsTotal = 0;
  for (i = 0; i < DFS.length; i++) {
    var ed = res.ch['' + DFS[i]];
    if (ed) dfsTotal += ed.n;
  }
  if (dfsTotal == 0) {
    serial.println(' 52*~144*  0     -     -  .            <- DFS 段全空');
  } else {
    for (i = 0; i < DFS.length; i++) serial.println(fmtRow(res, DFS[i], maxSc));
  }

  // 149-165
  for (i = 0; i < HIGHB.length; i++) serial.println(fmtRow(res, HIGHB[i], maxSc));
  serial.println(fmtRow(res, 165, maxSc));
  serial.println('* = DFS 信道，检测到雷达就得让路，没事别选');

  if (res.ap5.length > 0) {
    var top = sortByRssi(res.ap5);
    var n = top.length < MAXAP ? top.length : MAXAP;
    serial.println('');
    serial.println('信号最强的 5G AP:');
    for (i = 0; i < n; i++) {
      var a = top[i];
      var nm = a.SSID ? a.SSID : '(隐藏)';
      if (nm.length > 18) nm = nm.substring(0, 18);
      var mark = (i == 0) ? '  <- 最可能就是你自己的' : '';
      serial.println('  ' + pad(a.RSSI, 5) + 'dBm  ch' + pad(a.channel, 4) + ' ' + rpad(nm, 19) + a.MAC + mark);
    }
  }
}

// ---------- CSV ----------
// 注意：storage.read 在文件不存在时会抛异常，且把已有的大 CSV 读进 100KB 堆会爆，
// 所以这里只用 readdir 拿文件大小，绝对不读文件内容。
function fileSize(dirPath, name) {
  try {
    var list = storage.readdir(dirPath, { withFileTypes: true });
    for (var i = 0; i < list.length; i++) {
      if (list[i].name == name) return list[i].isDirectory ? -1 : list[i].size;
    }
  } catch (e) {
    return 0;
  }
  return 0;
}

function initCsv() {
  if (fileSize('/', 'chan5_sum.csv') < 4) {
    storage.write(SUM, 'ms,total,ap2g,ap5g,best_ch,width,worst_ch,note\n', 'write');
  }
  if (fileSize('/', 'chan5_raw.csv') < 4) {
    storage.write(RAW, 'ms,ssid,mac,rssi,channel,band,enc\n', 'write');
  }
}

function saveRound(res, plan, worst) {
  try {
    var line = '' + res.at + ',' + res.total + ',' + res.n2 + ',' + res.n5 + ','
               + plan.ch + ',' + plan.width + ',' + worst.ch + ',' + clean(plan.note) + '\n';
    storage.write(SUM, line, 'append');

    var buf = '';
    for (var i = 0; i < res.apAll.length; i++) {
      var a = res.apAll[i];
      buf = buf + res.at + ',' + clean(a.SSID) + ',' + a.MAC + ',' + a.RSSI + ','
                + a.channel + ',' + (a.channel > 14 ? '5G' : '2.4G') + ','
                + a.encryptionType + '\n';
    }
    if (buf.length > 0) storage.write(RAW, buf, 'append');
  } catch (e) {
    serial.println('!! 写 CSV 失败: ' + e);
  }
}

// ================= 主流程 =================
serial.println('');
serial.println('================================');
serial.println('  5GHz 信道普查仪  chan5g.js');
serial.println('  轮数 ' + ROUNDS + '   间隔 ' + (GAP_MS / 1000) + 's');
serial.println('================================');

var sp = storage.spaceLittleFS();
if (sp && sp.total) serial.println('LittleFS 已用 ' + sp.used + 'KB / ' + sp.total + 'KB');

initCsv();

var fails = 0;

for (var r = 1; r <= ROUNDS; r++) {
  serial.println('');
  serial.println('>> 扫描中...（会切到 STA，热点断开，属正常）');
  var res = scanOnce();

  if (res == null) {
    fails++;
    if (fails >= 3) {
      serial.println('!! 连续 3 次扫不到，放弃。检查 WiFi 是否被禁用，或换地方再试');
      break;
    }
    serial.println('!! 这次没扫到东西，10 秒后重试');
    delay(10000);
    r = r - 1;
    continue;
  }
  fails = 0;

  var plan = advise(res);
  var worst = worstOf(res);
  printRound(res, r);

  serial.println('');
  serial.println('【结论】');
  serial.println('  信道 -> ' + plan.ch + '，带宽 -> ' + plan.width + 'MHz');
  serial.println('  依据    ' + plan.note);
  if (worst.ch > 0 && worst.e) {
    serial.println('  最挤    ' + worst.ch + ' 信道（' + worst.e.n + ' 个 AP，最强 ' + worst.e.best + 'dBm）');
  }
  if (plan.dfs) serial.println('  提醒    选在 DFS 段，路由器可能不让选，或自己跳走');
  serial.println('  怎么改  路由器后台 5G 无线设置 -> 信道 ' + plan.ch + ' / 带宽 ' + plan.width + 'MHz');

  saveRound(res, plan, worst);
  if (r < ROUNDS) delay(GAP_MS);
}

serial.println('');
serial.println('================================');
serial.println('  汇总 ' + SUM.path + '  (每轮一行)');
serial.println('  明细 ' + RAW.path + '  (每个 AP 一行)');
serial.println('  恢复热点: wifi on');
serial.println('================================');
