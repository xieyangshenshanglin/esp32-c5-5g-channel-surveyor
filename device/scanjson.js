// scanjson.js —— 扫一次，每个 AP 一行「SSID\tMAC\tRSSI\tchannel」打到串口，
// 供电脑端的 chan5g_gui.py 实时解析。比 chan5g.js（三轮 + 排版）快得多，
// 单轮大约 7 秒，适合界面里反复刷新。
var wifi = require('wifi');
var serial = require('serial');

var aps = wifi.scan();
serial.println('<<<SCAN ' + (aps ? aps.length : 0));
if (aps) {
    for (var i = 0; i < aps.length; i++) {
        var a = aps[i];
        serial.println((a.SSID ? a.SSID : '') + '\t' + a.MAC + '\t'
                       + a.RSSI + '\t' + a.channel);
    }
}
serial.println('>>>END');
