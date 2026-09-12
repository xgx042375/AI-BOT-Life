/* QQ AI 启动器 · 简洁控制台主页逻辑（generic 皮肤样板实现，launcher-skin-v1）。
 * 本文件是皮肤包的最小可用参考实现：vanilla JS、零外部资源、零图片素材。
 * 与启动器（Launcher.ps1）的完整接口契约见：docs/皮肤包接口规范-v1.md，要点：
 *   1) 1s 轮询同目录 state.json?t=<毫秒>（__push/op{active,text}/page/cards/dialog/portrait/svc/life 字段）；
 *   2) 必须以同名全局函数提供 window.showWebBlock(text) / window.hideWebBlock()（PS 侧 ExecuteScriptAsync 直呼）；
 *   3) 命令通道 window.chrome.webview.postMessage({cmd:'..', data:'..'})，
 *      setpersona 的 data = 卡键字符串（对齐 Handle-WebCmd 实码）。 */
(function () {
	'use strict';

	/* ---------- 小工具 ---------- */
	function $(id) { return document.getElementById(id); }
	function esc(s) {
		return String(s == null ? '' : s).replace(/[&<>"']/g, function (ch) {
			return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
		});
	}
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

	/* ---------- PS 主动推送（PostWebMessageAsString：{__push:true, dialog:'..'}）即时反馈，1s 轮询兜底 ---------- */
	try {
		window.chrome.webview.addEventListener('message', function (ev) {
			try {
				var d = (typeof ev.data === 'string') ? JSON.parse(ev.data) : ev.data;
				if (d && d.__push && d.dialog !== undefined) { setText('dialog', d.dialog); }
			} catch (e) { }
		});
	} catch (e) { }

	/* ---------- 顶部时钟（页内本地秒级时钟，不用 state 时间防跳变） ---------- */
	function tickClock() {
		var d = new Date();
		$('clock').textContent =
			('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2) + ':' + ('0' + d.getSeconds()).slice(-2);
	}
	setInterval(tickClock, 1000);
	tickClock();

	/* ---------- 控制区按钮（启动反馈与 ark 样板一致：先 showWebBlock 再发命令） ---------- */
	$('btn-start').addEventListener('click', function () { window.showWebBlock('正在启动全部…'); send('start'); });
	$('btn-stop').addEventListener('click', function () { window.showWebBlock('正在停止全部…'); send('stop'); });
	$('btn-stopbot').addEventListener('click', function () { window.showWebBlock('正在停止机器人…'); send('stopbot'); });
	$('btn-exit').addEventListener('click', function () { window.showWebBlock('正在退出…'); send('exit'); });
	/* 管理 行：切到 WPF 原生页（Handle-WebCmd 的 cfg/plugins/log/state 命令，Show-Page 藏 web 视图） */
	$('btn-cfg').addEventListener('click', function () { send('cfg'); });
	$('btn-plugins').addEventListener('click', function () { send('plugins'); });
	$('btn-log').addEventListener('click', function () { send('log'); });
	$('btn-state').addEventListener('click', function () { send('state'); });
	/* GAL 页：必须 WebView2 原地导航（ark 样板同款 location.href）——window.open 会被 WebView2
	 * 甩到系统默认浏览器（"独立外置"），且外置浏览器不解析 app.local 虚拟主机，GAL 页"返回启动器"随之失效。
	 * ent 参数告知 gal.js 返回哪个皮肤入口（goLauncher 据此拼 app.local 地址）。 */
	$('btn-gal').addEventListener('click', function () {
		try { location.href = 'http://127.0.0.1:8080/gal?ent=generic'; } catch (e) { }
	});

	/* ---------- 人设卡网格 + 详情弹层 ---------- */
	var CARDS = [];
	var currentDetailKey = '';

	function renderGrid(cards) {
		var g = $('card-grid');
		if (!g) { return; }
		g.innerHTML = '';
		cards.forEach(function (c) {
			var d = document.createElement('div');
			d.className = 'pcard';
			d.innerHTML =
				'<img class="bust" src="' + esc(c.img) + '" loading="lazy" decoding="async" alt="">' +
				(c.rar ? '<img class="rar" src="' + esc(c.rar) + '" alt="">' : '') +
				'<span class="cls-chip-pos">' + esc(c.cls) + '</span>' +
				'<span class="pname">' + esc(c.name) + '</span>';
			/* generic 皮肤 rar 为空串（皮肤感知，P3-2）：判空跳过不生成 img；其余皮肤挂载失败仍 onerror 隐藏 */
			var rar = d.querySelector('img.rar');
			if (rar) { rar.addEventListener('error', function () { rar.style.display = 'none'; }); }
			d.addEventListener('click', function () { openDetail(c.key); });
			g.appendChild(d);
		});
		var hint = $('grid-hint');
		if (hint) { hint.hidden = cards.length > 0; }
	}

	function findCard(key) {
		for (var i = 0; i < CARDS.length; i++) { if (CARDS[i].key === key) { return CARDS[i]; } }
		return null;
	}

	function openDetail(key) {
		var c = findCard(key);
		if (!c) { return; }
		currentDetailKey = key;
		$('detail-uni').textContent = c.uni || '';
		$('detail-name').textContent = c.name || '';
		$('detail-stars').textContent = c.starText || '';
		$('detail-cls').textContent = c.cls || '';
		$('detail-desc').textContent = c.desc || '';
		$('detail-msg').textContent = '';
		var im = $('detail-img');
		im.style.display = '';
		im.onerror = function () { im.style.display = 'none'; }; /* 无立绘素材时隐藏图框，不破版式 */
		im.src = c.big || c.img || '';
		$('detail-layer').hidden = false;
	}

	function closeDetail() { $('detail-layer').hidden = true; currentDetailKey = ''; }
	$('detail-close').addEventListener('click', closeDetail);
	$('detail-layer').addEventListener('click', function (ev) { if (ev.target === this) { closeDetail(); } });

	/* 设为启动人设：契约形状 = { cmd:'setpersona', data:'<卡键>' }（PS Handle-WebCmd 取 [string]$m.data 作卡键） */
	$('detail-set').addEventListener('click', function () {
		if (!currentDetailKey) { return; }
		$('detail-msg').textContent = '已发送切换请求：' + currentDetailKey + '（bot 在线切换，或重启后生效）';
		send('setpersona', currentDetailKey);
	});

	/* ---------- page 字段映射（单页布局）：opera/detail → 滚动人设区；
	 * detail 额外尝试打开"当前人设"的详情（按 idtext 前缀匹配卡名）。
	 * cfg/log/state 为 WPF 原生页（web 被整页遮盖），home 无需动作。 ---------- */
	function applyPage(p) {
		if (p === 'opera' || p === 'detail') {
			try { $('sec-persona').scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (e) { }
			if (p === 'detail') {
				var idt = (window.__lastState && window.__lastState.idtext) || '';
				var nm = String(idt).split('·')[0].trim();
				for (var i = 0; i < CARDS.length; i++) {
					if (CARDS[i].name === nm) { openDetail(CARDS[i].key); break; }
				}
			}
		}
	}

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
		setText('life-doing', sentenceCut(d.doing, 48));		setText('life-mood', d.mood);
		setText('life-plan', d.plan);
		setText('life-wake', d.wake);
		setText('life-persona', d.idtext);
		if (d.dialog !== undefined) { setText('dialog', sentenceCut(d.dialog, 96)); } /* 96≈两行容量：截断落在句读上，CSS clamp 不再兜底拦腰 */
		var po = $('portrait');
		if (po) {
			if (d.portrait) { po.hidden = false; if (po.getAttribute('src') !== d.portrait) { po.src = d.portrait; } }
			else { po.hidden = true; }
		}
		/* 人设卡 */
		if (d.cards && d.cards.length) {
			var sig = JSON.stringify(d.cards);
			if (sig !== window.__cardsSig) {
				window.__cardsSig = sig;
				CARDS = d.cards;
				renderGrid(CARDS);
			}
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
