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

/* ---------- 运行时语言（中 ↔ 英）----------
 * 【i18n 约定】（皮肤作者照抄即可）：
 *   1) HTML 侧只有一个标记：data-i18n="<key>"——必须贴在**承载文字的元素本身**上
 *      （<title data-i18n="pageTitle"> 是唯一特例：key 一样，但写入 document.title 而不是 textContent）。
 *      被标记元素的**既有文本保留原样** = 中文原文，既是未启用 i18n 时的兜底，也是人肉对照表。
 *   2) 需要翻译**属性**时用 data-i18n-attr="attr:key"，多个用逗号分隔：data-i18n-attr="title:t,placeholder:p"。
 *      本页暂无属性文本，此通道保留给后续皮肤（标题是文本节点，走 data-i18n 即可）。
 *   3) 表里查不到的 key 静默回落中文，再查不到就保留 HTML 原文——i18n 永不让页面空掉或抛错。
 *   4) 语言来源：state.json 的 lang 字段（"zh"/"en"）。**字段缺失 = 中文**（旧启动器/旧皮肤升级路径不能炸）。
 *      首次成功读到 state.json 才应用；之后 lang 变化即时重刷。localStorage 只用来记住上次语言以消掉
 *      "中文闪一下"，不是权威来源——state.json 一到就以它为准。
 *   5) 本页自身的用户可见字符串 **一律** 进本表，不许在渲染代码里硬写中文（注释与日志除外）。
 *   6) 跳到别的页要带语言：GAL 页地址追加 &lang=<lang>（GAL 侧读 ?lang=）。 */
var STR = {
	zh: {
		pageTitle: 'QQ AI 启动器 · 简洁控制台',
		opBusy: '正在执行操作…',
		secStatus: '运行状态',
		secEntry: '入口',
		lampEngine: '推理引擎',
		lampEmbed: '记忆服务',
		lampBot: '机器人',
		stUnknown: '未知',
		stOn: '运行中',
		stOff: '已关闭',
		lifeScene: '当前场景',
		lifeDoing: '正在',
		lifeMood: '心情',
		lifePlan: '计划',
		lifeWake: '心跳',
		lifePersona: '当前人设',
		dialogIdle: '等待指令…',
		btnOpe: '干员一览',
		btnGal: '进入 GAL 页',
		btnExit: '退出启动器',
		hintStatus: '只读总览：服务灯与生活状态每秒自动刷新。服务详情与心跳时间线见顶部「运行状态」页。',
		hintEntry: '启动 / 停止在窗口顶部工具栏；设置、内容包、干员、运行状态、日志、组件、关于见顶部导航。 ' +
			'框架已提供的一等公民操作，皮肤不再重复摆一份——同一个动作两处入口，状态迟早对不上。',
		exitBusy: '正在退出…',
		galNoBot: '机器人未启动：GAL 页要先让 bot 跑起来（顶栏「▶ 启动」，约 30-60 秒）。'
	},
	en: {
		pageTitle: 'QQ AI Launcher · Minimal Console',
		opBusy: 'Working…',
		secStatus: 'Runtime',
		secEntry: 'Entry Points',
		lampEngine: 'Inference engine',
		lampEmbed: 'Memory service',
		lampBot: 'Bot',
		stUnknown: 'Unknown',
		stOn: 'Running',
		stOff: 'Stopped',
		lifeScene: 'Scene',
		lifeDoing: 'Doing',
		lifeMood: 'Mood',
		lifePlan: 'Plan',
		lifeWake: 'Heartbeat',
		lifePersona: 'Persona',
		dialogIdle: 'Awaiting command…',
		btnOpe: 'Operators',
		btnGal: 'Open GAL page',
		btnExit: 'Quit launcher',
		hintStatus: 'Read-only overview: service lamps and life status refresh every second. ' +
			'Service details and the heartbeat timeline live on the 「Runtime」 page in the top bar.',
		hintEntry: 'Start / stop live in the toolbar at the top of the window; Settings, Content packs, Operators, ' +
			'Runtime, Logs, Components and About are in the top navigation. ' +
			'Operations the framework already offers are not duplicated by the skin — the same action in two ' +
			'places will drift out of sync sooner or later.',
		exitBusy: 'Quitting…',
		galNoBot: 'Bot is not running: the GAL page needs the bot up first (click 「▶ Start」 in the top bar, ' +
			'about 30-60 seconds).'
	}
};

(function () {
	'use strict';

	/* ---------- 小工具 ---------- */
	function $(id) { return document.getElementById(id); }
	function setText(id, v) { var e = $(id); if (e) { e.textContent = (v == null || v === '') ? '—' : String(v); } }

	/* ---------- i18n 应用器（见文件头约定） ---------- */
	var LANG_KEY = 'dsh.skin.lang';	/* 只记"上次用什么语言"，权威来源仍是 state.json */
	var lang = 'zh';				/* 缺省中文：旧启动器没有 lang 字段时的行为 */

	function t(key) {
		var tbl = STR[lang] || STR.zh;
		var s = tbl[key];
		if (s == null || s === '') { s = STR.zh[key]; }
		if (s == null) { s = key; }	/* 表里没有：不要把页面弄空 */
		return s;
	}

	/* data-i18n="<key>"（元素文本）/ data-i18n-attr="attr:key[,attr:key]"（属性） */
	function applyI18n() {
		var nodes = document.querySelectorAll('[data-i18n]');
		for (var i = 0; i < nodes.length; i++) {
			var key = nodes[i].getAttribute('data-i18n');
			var s = t(key);
			if (nodes[i].tagName === 'TITLE') { document.title = s; }
			else { nodes[i].textContent = s; }
		}
		var attrs = document.querySelectorAll('[data-i18n-attr]');
		for (var j = 0; j < attrs.length; j++) {
			var spec = String(attrs[j].getAttribute('data-i18n-attr')).split(',');
			for (var k = 0; k < spec.length; k++) {
				var p = spec[k].split(':');
				if (p.length === 2 && p[0].trim() && p[1].trim()) { attrs[j].setAttribute(p[0].trim(), t(p[1].trim())); }
			}
		}
		try { document.documentElement.lang = (lang === 'en') ? 'en' : 'zh-CN'; } catch (e) { }
	}

	function setLang(next, remember) {
		next = (next === 'en') ? 'en' : 'zh';
		lang = next;
		if (remember) { try { localStorage.setItem(LANG_KEY, next); } catch (e) { } }
		applyI18n();
		refreshBusy();	/* 遮罩横幅的文字也要跟着换语言 */
	}

	function readLang(v) { return (v === 'en') ? 'en' : 'zh'; }	/* 缺失/未知/大小写不符 → 中文 */

	/* 首屏先用上次记住的语言，避免"中文闪一下"；state.json 一到即以它为准 */
	try {
		var cached = localStorage.getItem(LANG_KEY);
		if (cached === 'en' || cached === 'zh') { lang = cached; }
	} catch (e) { }

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
	bind('btn-exit', function () { window.showWebBlock(t('exitBusy')); send('exit'); });
	/* GAL 页：必须 WebView2 原地导航（ark 样板同款 location.href）——window.open 会被 WebView2
	 * 甩到系统默认浏览器（"独立外置"），且外置浏览器不解析 app.local 虚拟主机，GAL 页"返回启动器"随之失效。
	 * ent 参数告知 gal.js 返回哪个皮肤入口（goLauncher 据此拼 app.local 地址）；
	 * lang 参数告知 gal.js 当前语言（GAL 页读 ?lang=，与 state.json 的 lang 同源）。
	 * 前置判据（2026-09-12 用户实测）：bot 没跑时 GAL 页**必然打不开**，跳过去只会得到一张
	 * WebView2 的错误页（它不走 state.json、也不认主页键 → 用户卡在那儿）。所以先在本地拦一句，
	 * 把"为什么打不开、怎么开"直接写在对话框里。框架侧另有一道 NavigationStarting 兜底（换皮肤也拦得住）。 */
	bind('btn-gal', function () {
		var st = window.__lastState || {};
		if (st.svc && st.svc.bot === false) {
			setText('dialog', t('galNoBot'));
			return;
		}
		try { location.href = 'http://127.0.0.1:8080/gal?ent=generic&lang=' + encodeURIComponent(lang); } catch (e) { }
	});

	/* ---------- 操作遮罩（PS 直呼 + op.active 轮询双驱动） ---------- */
	/* op.text 由 PS 现给（中文，PS 侧文本不在本表范围内）；给了就用给的，没给或给了空串用当前语言兜底。 */
	function refreshBusy() {
		var st = window.__lastState;
		if (st && st.op && st.op.active) { window.showWebBlock(st.op.text); }
	}

	window.showWebBlock = function (text) {
		var wb = $('web-blocker');
		var tip = document.querySelector('#web-warnbar .wb-text');
		if (tip) {
			var s = (text && String(text).trim() !== '') ? String(text) : '';
			if (s) { s = s.replace(/^⚠\s*/, '').replace(/^[(（]\s*/, '').replace(/\s*[)）]$/, '').trim(); }
			tip.textContent = s || t('opBusy');
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
		/* lang：新字段。缺失 = 中文（旧启动器兼容）；变了才重刷，防重放抖动 */
		var next = readLang(d.lang);
		if (next !== lang) { setLang(next, true); }
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
		if (on) { e.classList.add('on'); if (st) { st.textContent = t('stOn'); } }
		else { e.classList.remove('on'); if (st) { st.textContent = t('stOff'); } }
	}

	/* ---------- 启动：先按当前语言刷一遍静态串（HTML 里的中文原文 = 兜底） ---------- */
	if (document.readyState === 'loading') {
		document.addEventListener('DOMContentLoaded', function () { try { applyI18n(); refreshBusy(); } catch (e) { } });
	} else {
		try { applyI18n(); refreshBusy(); } catch (e) { }
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
