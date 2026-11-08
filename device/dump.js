// dump.js —— 把设备上的文本文件原样打到串口，供电脑端抓取。
// storage read / cat 都有大小限制（ERR: File is too big），
// 走脚本的 serial.println 没有这个限制，几 KB 的 CSV 一次吐完。
var storage = require('storage');
var serial = require('serial');

var FILES = ['/chan5_raw.csv', '/chan5_sum.csv'];

for (var i = 0; i < FILES.length; i++) {
    var p = FILES[i];
    var txt = null;
    try {
        txt = storage.read({ fs: 'littlefs', path: p });
    } catch (e) {
        txt = null;
    }
    serial.println('<<<BEGIN ' + p);
    if (txt === null) {
        serial.println('<<<MISSING');
    } else {
        serial.println(txt);
    }
    serial.println('>>>END');
}
