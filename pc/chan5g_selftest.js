// 端到端沙箱测试：把 chan5g.js 丢进 vm，mock 掉 wifi/serial/storage
// 跑两遍，验证第二次不会重复写表头（真实设备上 storage.read 对不存在的文件会抛异常）
const fs = require('fs');
const vm = require('vm');
const SRC = fs.readFileSync('G:\\ep32-c3\\chan5g.js', 'utf8');

function mkAp(ch, rssi, ssid, enc) {
  return { SSID: ssid || '', MAC: 'de:ad:00:00:00:' + (ch < 10 ? '0' + ch : ch), RSSI: rssi, channel: ch, encryptionType: enc || 'WPA2_PSK' };
}

const scans = [
  [mkAp(1, -55, 'TP-LINK_2G'), mkAp(6, -62, 'ChinaNet'),
   mkAp(36, -45, 'MiWiFi-5G'), mkAp(36, -58, 'CMCC-5G'), mkAp(36, -70, 'HUAWEI-5G'),
   mkAp(44, -66, 'TP-LINK_A5G'), mkAp(149, -83, ''), mkAp(52, -55, 'Office-DFS')],
  [mkAp(1, -55, 'TP-LINK_2G'), mkAp(36, -45, 'MiWiFi-5G'), mkAp(149, -52, 'Neighbor-5G'),
   mkAp(149, -64, 'Neighbor-5G-2'), mkAp(153, -71, 'X'), mkAp(100, -60, 'DFS-AP')],
  [mkAp(1, -55, 'TP-LINK_2G'), mkAp(36, -45, 'MiWiFi-5G'), mkAp(40, -48, 'a'), mkAp(44, -50, 'b'),
   mkAp(48, -55, 'c'), mkAp(149, -50, 'd'), mkAp(153, -52, 'e'), mkAp(157, -55, 'f'),
   mkAp(161, -58, 'g'), mkAp(100, -70, 'h')]
];

function runOnce(files, label) {
  const out = [];
  let tick = 0;
  let scanIdx = 0;
  const wifi = { scan: function () { return scans[scanIdx < scans.length ? scanIdx++ : scans.length - 1]; } };
  const serial = {
    print: function (s) { out.push(String(s)); },
    println: function (s) { out.push(s === undefined ? '' : String(s)); }
  };
  const storage = {
    // 真实固件行为：文件不存在 -> JS_ThrowTypeError
    read: function (p) {
      const k = (typeof p === 'string') ? p : p.path;
      if (files[k] === undefined) throw new Error('storageRead: File: ' + k + ' does not exist');
      return files[k];
    },
    write: function (p, data, mode) {
      const k = (typeof p === 'string') ? p : p.path;
      if (mode === 'write') files[k] = String(data);
      else files[k] = (files[k] === undefined ? '' : files[k]) + String(data);
      return true;
    },
    readdir: function (p, opt) {
      const k = (typeof p === 'string') ? p : p.path;
      const pref = (k === '/' || k === '') ? '' : k + '/';
      const res = [];
      for (const name of Object.keys(files)) {
        if (name.startsWith(pref) && name.slice(pref.length).indexOf('/') < 0) {
          const nm = name.slice(pref.length);
          res.push(opt && opt.withFileTypes
            ? { name: nm, size: Buffer.byteLength(files[name]), isDirectory: false }
            : nm);
        }
      }
      return res;
    },
    spaceLittleFS: function () { return { total: 27264, used: 120, free: 27144 }; }
  };
  const sandbox = {
    require: function (n) {
      if (n === 'wifi') return wifi;
      if (n === 'serial') return serial;
      if (n === 'storage') return storage;
      return {};
    },
    console: { log: function (s) { out.push('[log] ' + s); } },
    Math: Math, String: String, Number: Number, parseInt: parseInt,
    now: function () { return tick; },
    delay: function (ms) { tick += (ms || 0); }
  };
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox, { filename: 'chan5g.js' });
  return out;
}

const files = {};
const out1 = runOnce(files, '第一次');
console.log(out1.join('\n'));

// 第二次运行：文件已存在，表头不该重复
Object.keys(files).forEach(k => { files[k] = files[k]; });
const out2 = runOnce(files, '第二次');

console.log('\n\n========== 文件内容 ==========');
for (const k of Object.keys(files)) {
  const head = files[k].split('\n');
  console.log('\n### ' + k + '  (' + Buffer.byteLength(files[k]) + ' bytes, ' + head.length + ' 行)');
  console.log(head.slice(0, 4).join('\n') + (head.length > 4 ? '\n  ...' : ''));
  const hdr = head[0];
  const dup = head.filter(l => l === hdr).length;
  console.log('  表头出现次数 = ' + dup + (dup === 1 ? '  OK' : '  !! 有重复'));
}
