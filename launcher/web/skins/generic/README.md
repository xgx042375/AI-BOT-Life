# generic 皮肤（简洁控制台）

通用默认皮肤，亦是自制皮肤的最小样板：零图片素材、零外部资源、零第三方 IP。

## 目录结构

```
skins/generic/
├─ manifest.json   皮肤清单（launcher-skin-v1，字段规范见 docs/皮肤包接口规范-v1.md）
├─ palette.css     调色板：--bg / --fg / --accent / --muted 四个 CSS 变量
└─ README.md       本文件
```

generic 的页面本体不在本目录：`entry: "../../index.html"` 指向 `launcher/web/index.html`
（配 `home.css` + `app.js`），运行时状态文件 `statePath: "../../state.json"` 同理回指 web 根。

## 最小可用 manifest

```json
{ "spec": "launcher-skin-v1", "name": "my-skin", "title": "我的皮肤",
  "entry": "index.html", "palette": { "bg": "#0f1216", "fg": "#e8eaed", "accent": "#4da3ff", "muted": "#8a919c" },
  "statePath": "state.json", "author": "", "license": "MIT", "note": "" }
```

## 引用 state.json

页面脚本每 1 秒轮询同目录相对路径 `state.json?t=<时间戳>`（启动器原子写，字段契约见
`docs/皮肤包接口规范-v1.md`）；`window.showWebBlock(text)` / `window.hideWebBlock()`
两个全局函数必须提供（启动器会直呼），命令用 `window.chrome.webview.postMessage({cmd:'..'})` 发送。
