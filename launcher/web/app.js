/* QQ AI 启动器 · 简洁控制台主页逻辑（generic 皮肤样板实现，launcher-skin-v1）。
 * 本文件是皮肤包的最小可用参考实现：vanilla JS、零外部资源、零图片素材。
 * 与启动器（Launcher.ps1）的完整接口契约见：docs/皮肤包接口规范-v1.md，要点：
 *   1) 1s 轮询同目录 state.json?t=<毫秒>（__push/op{active,text}/page/cards/dialog/portrait/svc 字段）；
 *   2) 必须以同名全局函数提供 window.showWebBlock(text) / window.hideWebBlock()（PS 侧 ExecuteScriptAsync 直呼）；
 *   3) 命令通道 window.chrome.webview.postMessage({cmd:'..', data:'..'})。
 *
 * 2026-09-12 分层去重（用户实测报告）：本页不再自绘"第二套导航/第二套控制/第二套人设面"。
 *   删掉的东西与理由：启动/停止/仅停机器人（顶栏 ▶ ■ 与「运行状态」页已有，两处入口状态会不同步）、
 *   设置/内容包/日志/状态（顶栏导航已有）、人设卡网格 + 详情弹层 + setpersona（框架「干员」页 = 唯一数据面）。
 *   留下：只读总览 + 三个框架没有的入口（干员页 / GAL 页 / 退出）。
 *   配套的框架侧改动：Handle-WebCmd 新增 `operators` 命令（皮肤由此能打开框架干员页——原先没有这个通道，
 *   所以皮肤只能自绘一套，这才是"两个不一样的干员页"的根因）。 */
(function () {
	'use strict';

	/* ---------- 小工具 ---------- */
	function $(id) { return document.getElementById(id); }
	function setText(id, v) { var e = $(id); if (e) { e.textContent = (v == null || v === '') ? '—' : String(v); } }

	/* 状态描述截断（2026-09-11 用户裁决）：主页空间有限要截断，但不许拦腰切句——
	 * 预算内取最后一个句读收尾加 …；预算内无句读则延伸到首个句读；完全无标点才硬切。 */
	function sentenceCut(s, max) {
		s = String(s == null ? '' : s).trim();
		if (s.length <= max) { return s; }
		var marks = '。！？；!?;…';
		var head = s.slice(0, max);
		for (var k = head.length - 1; k >= 0; k--) {
			if (marks.indexOf(head[k]) >= 0) { return head.slice(0, k + 1) + '…'; }
		}
		for (var j = max; j < s.length; j++) {
			if (marks.indexOf(s[j]) >= 0) { return s.slice(0, j + 1) + '…'; }
		}
		return head + '…';
	}

	/* ---------- PS 命令通道（唯一可靠通道，对齐 ark 样板 inline onclick 写法） ---------- */
	function send(cmd, data) {
		try { window.chrome.webview.postMessage({ cmd: cmd, data: data || '' }); } catch (e) { }
	}

	/* 绑定按钮：**先判存在再绑定**。历史故障模式：HTML 里删了按钮、JS 还 addEventListener →
	 * 顶层抛 TypeError → 整个 IIFE 中断 → 后面的绑定/轮询全不生效（页面看着"活着"，其实全死）。
	 * 所以缺元素一律显式报出去（cmd:'diag' 是保留字，PS 侧整条消息落 web_diag.log），不静默跳过。 */
	function bind(id, fn) {
		var e = $(id);
		if (!e) { try { console.error('skin: missing element #' + id); } catch (x) { } send('diag', 'missing element: #' + id); return; }
		e.addEventListener('click', fn);
	}

	/* ---------- PS 主动推送（PostWebMessageAsString：{__push:true, dialog:'..'}）即时反馈，1s 轮询兜底 ---------- */
	try {
		window.chrome.webview.addEventListener('message', function (ev) {
			try {
				var d = (typeof ev.data === 'string') ? JSON.parse(ev.data) : ev.data;
				if (d && d.__push && d.dialog !== undefined) { setText('dialog', d.dialog); }
			} catch (e) { }
		});
	} catch (e) { }

	/* ---------- 入口按钮 ---------- */
	/* 干员页 = 框架原生页（唯一数据面：卡片图 / 详情 / 设为启动人设）。皮肤只负责"带路"。 */
	bind('btn-ope', function () { send('operators'); });
	bind('btn-exit', function () { window.showWebBlock('正在退出…'); send('exit'); });
	/* GAL 页：必须 WebView2 原地导航（ark 样板同款 location.href）——window.open 会被 WebView2
	 * 甩到系统默认浏览器（"独立外置"），且外置浏览器不解析 app.local 虚拟主机，GAL 页"返回启动器"随之失效。
	 * ent 参数告知 gal.js 返回哪个皮肤入口（goLauncher 据此拼 app.local 地址）。 */
	bind('btn-gal', function () {
		try { location.href = 'http://127.0.0.1:8080/gal?ent=generic'; } catch (e) { }
	});

	/* ---------- 操作遮罩（PS 直呼 + op.active 轮询双驱动） ---------- */
	window.showWebBlock = function (text) {
		var wb = $('web-blocker');
		var tip = document.querySelector('#web-warnbar .wb-text');
		if (tip) {
			var t = (text && String(text).trim() !== '') ? String(text) : '';
			if (t) { t = t.replace(/^⚠\s*/, '').replace(/^[(（]\s*/, '').replace(/\s*[)）]$/, '').trim(); }
			tip.textContent = t || '正在执行操作…';
		}
		if (wb) { wb.style.display = 'block'; }
	};
	window.hideWebBlock = function () {
		var wb = $('web-blocker');
		if (wb) { wb.style.display = 'none'; }
	};

	/* ---------- page 字段（§4.3）：本页没有自绘的人设面，opera/detail 一律**转交框架干员页**。
	 * 这是"只读 + 跳转"原则的落地：皮肤不假装自己有第二个数据面。
	 * 只应用一次（page 变化才动作），防推送重放抖动；转交命令只发一次，不会自激。 ---------- */
	function applyPage(p) {
		if (p === 'opera' || p === 'detail') { send('operators'); }
	}

	/* ---------- state.json 应用 ---------- */
	window.__lastState = null;

	function apply(d) {
		window.__lastState = d;
		/* op：操作中 → 遮罩+横幅；空闲 → 收起（与 PS ExecuteScriptAsync 直呼互为冗余） */
		if (d.op) {
			if (d.op.active) { window.showWebBlock(d.op.text); } else { window.hideWebBlock(); }
		}
		/* page：变化才应用一次（防重放抖动） */
		if (d.page && d.page !== window.__lastPage) {
			window.__lastPage = d.page;
			applyPage(d.page);
		}
		/* 四服务灯（svc 字段；旧版 state.json 无此字段时保持灰灯） */
		if (d.svc) {
			setLamp('lamp-engine', d.svc.engine);
			setLamp('lamp-embed', d.svc.embed);
			setLamp('lamp-napcat', d.svc.napcat);
			setLamp('lamp-bot', d.svc.bot);
		}
		/* 生活状态（正在：主页单行空间有限，按句读边界截断——不拦腰切句） */
		setText('life-scene', d.scene);
		setText('life-doing', sentenceCut(d.doing, 48));
		setText('life-mood', d.mood);
		setText('life-plan', d.plan);
		setText('life-wake', d.wake);
		setText('life-persona', d.idtext);
		if (d.dialog !== undefined) { setText('dialog', sentenceCut(d.dialog, 96)); } /* 96≈两行容量：截断落在句读上，CSS clamp 不再兜底拦腰 */
		var po = $('portrait');
		if (po) {
			if (d.portrait) { po.hidden = false; if (po.getAttribute('src') !== d.portrait) { po.src = d.portrait; } }
			else { po.hidden = true; }
		}
	}

	function setLamp(id, on) {
		var e = $(id);
		if (!e) { return; }
		var st = e.querySelector('.lamp-state');
		if (on) { e.classList.add('on'); if (st) { st.textContent = '运行中'; } }
		else { e.classList.remove('on'); if (st) { st.textContent = '已关闭'; } }
	}

	/* ---------- 1s 轮询同目录 state.json（?t= 防缓存，契约不可改） ---------- */
	setInterval(function () {
		try {
			var x = new XMLHttpRequest();
			x.open('GET', 'state.json?t=' + Date.now(), true);
			x.onreadystatechange = function () {
				if (x.readyState === 4 && x.status === 200) {
					try {
						var d = JSON.parse(x.responseText);
						if (d && d.__push) { apply(d); }
					} catch (e) { }
				}
			};
			x.send();
		} catch (e) { }
	}, 1000);
})();
