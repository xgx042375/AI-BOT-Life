/* GAL 前端客户端（网页端，批次1'）。
 * 页面由 bot serve：http://127.0.0.1:8080/gal（协议见 docs/archive/计划表-GAL前端客户端-2026-09-09.md §3.2，
 * mode 三态 gal|qq|chat 按 §〇之二 用户裁决 v2）。
 * 素材替换口：下方 ASSETS（背景 / 角色色 / 立绘 baseURL / scene→背景映射），后续换素材只改这一处。
 * 零外部资源（无 CDN / web font）；vanilla JS，WebView2（现代 Chromium）可直接运行。 */
'use strict';

window.__GAL_BOOTED__ = true; /* gal.html 页尾脚本据此判断是否回退加载同目录相对资源 */

/* ============================== 素材配置口 ============================== */
var ASSETS = {
	/* 默认背景：走 WebView2 虚拟主机映射（app.local -> launcher/web）的绝对地址。
	 * 相对 img/ 路径在 bot serve 下不可用；外部浏览器或图挂掉时 onerror 自动降级为 CSS 渐变（#bg-fallback）。
	 * 2026-09-10 Phase2 素材分离：方舟背景图迁入 gitignore 皮肤包，路径改为 skins/arknights/img/（图挂时 onerror 渐变兜底仍在）。 */
	bgDefault: 'https://app.local/skins/arknights/img/avg_2_1.png',
	bgAlt: 'https://app.local/skins/arknights/img/UI_HOME_FRONT_BKG.png', /* 默认图失败时再试一次 */
	/* scene -> 背景 URL 映射（批次2 挂点，暂留空） */
	bgByScene: {},
	/* 姓名牌 / 主按钮 / bot 姓名色（服务端帧暂无颜色字段，取此默认值） */
	charColor: '#7ec9ff',
	/* 服务端 persona 只给卡键（非完整 URL）时的拼接口 */
	bustBase: 'https://cards.local/bust4w/',
	/* 情绪(基调)词→立绘差分素材词映射：性格立绘等用户标注词与 bot 基调词不必同名；未列出的基调词直接用原词找 <键>_<词>.webp */
	spriteMap: {
		amiya:     { '战斗': 'firm', '讥讽': 'anger', '温柔': 'Smile', '娇嗔': 'shy', '疑问': 'doubt', '无语': 'nausea', '纯H': 'embarrass', '高冷': 'darken', '傲娇': 'Smile' },
		priestess: { '温柔': 'smile', '高冷': 'default', '疑问': 'doubt', '无语': 'gaze_shifting', '傲娇': 'embarrass' }
	},
	fullBase: 'https://cards.local/fullw/'
};

/* ============================== 台词配置口（Phase 3 已外置） ============================== */
/* 聊天软件模式头部副行：随机经典 galgame / 视觉小说名台词（中文短句，SFW；
 * 出处为作品风格标注，允许意译）。每轮进入 chat 模式 / 每次 reply_end 后随机换一条，
 * 同一轮内不变；置空数组 [] 时该行整体不渲染。 */
/* robot-pack-v1（Phase 3，2026-09-10）：**台词数据已整体外置**，本文件不再内联任何台词文本——
 *   数据源 = GET /gal/content.json（core.packs.content_index：内容包 quotes 打底 → data/quotes.json 覆盖；
 *   data/ 层文件不随发行版带出，台词文本只在用户数据侧维护）。
 * 下方两个空容器仅作启动拉取前的缺省（空=台词行隐藏，rollQuote 已处理空数组）与拉取结果的落点。 */
var QUOTES_BY_CARD = {};

/* 分卡台词激活链：ASSETS.quotesByCard ← QUOTES_BY_CARD ←（启动时）/gal/content.json 覆盖 */
ASSETS.quotesByCard = QUOTES_BY_CARD;

/* 皮肤包钩子（launcher-skin-v1 约定，见 docs/皮肤包接口规范-v1.md §7）：
 * 在本文件加载前定义 window.GAL_SKIN = { assets: {…与 ASSETS 同形…} } 即可覆盖素材口；未定义则用内置默认。 */
if (window.GAL_SKIN && window.GAL_SKIN.assets) { Object.assign(ASSETS, window.GAL_SKIN.assets); }

/* 兜底台词：同样由 /gal/content.json 的 quoteFallback 提供（data/quotes.json 的 fallback 段；
 * 内容包不供兜底——兜底是全局层）。空数组=该行整体不渲染（拉取失败时的稳定缺省）。 */
var QUOTES_FALLBACK = [];
/* 皮肤覆盖（2026-09-12 · 遗留任务 A10 落地）：上面那句 Object.assign 只覆盖 ASSETS，
 * 而 quoteFallback 的读点是**模块变量**（rollQuote：`ASSETS.quotesByCard[键] || QUOTES_FALLBACK`），
 * 所以皮肤写 GAL_SKIN.assets.quoteFallback 原先**静默无效**。这里单列一行采纳皮肤值；
 * /gal/content.json 是异步后到，仍会覆盖它——与规范 §7 记的先后关系一致。 */
if (window.GAL_SKIN && window.GAL_SKIN.assets && Array.isArray(window.GAL_SKIN.assets.quoteFallback)) {
	QUOTES_FALLBACK = window.GAL_SKIN.assets.quoteFallback;
}

/* ============================== 连接地址 ============================== */
var HTTP_BASE = (location.protocol === 'http:' || location.protocol === 'https:')
	? location.protocol + '//' + location.host
	: 'http://127.0.0.1:8080'; /* file:// 直开兜底 */
var WS_URL = (location.protocol === 'https:' ? 'wss' : 'ws') + '://' +
	(location.host || '127.0.0.1:8080') + '/gal/ws';

/* 内容包索引拉取（robot-pack-v1 Phase 3）：启动时 fetch 一次 /gal/content.json 并落位——
 * quotesByCard/quoteFallback 供 rollQuote，spriteMap 并入 ASSETS.spriteMap（包 sprites/manifest.json
 * 的 map + 额外情绪 mod 的 sprite_word；内置默认打底，包映射覆盖同名键）。
 * 拉取失败保持空缺省 = 台词行隐藏、差分映射走内置默认（不弹错不重试；下次刷新页面自然重取）。 */
(function loadPackContent() {
	/* file:// 直开时该 fetch 必失败 → catch 保持空缺省（行为同“服务未起”）。 */
	fetch(HTTP_BASE + '/gal/content.json').then(function (r) {
		if (!r.ok) throw new Error('HTTP ' + r.status);
		return r.json();
	}).then(function (j) {
		if (!j || typeof j !== 'object') return;
		if (j.quotesByCard && typeof j.quotesByCard === 'object' && !Array.isArray(j.quotesByCard)) {
			ASSETS.quotesByCard = Object.assign({}, ASSETS.quotesByCard, j.quotesByCard);
		}
		if (Array.isArray(j.quoteFallback)) QUOTES_FALLBACK = j.quoteFallback;
		if (j.spriteMap && typeof j.spriteMap === 'object' && !Array.isArray(j.spriteMap)) {
			ASSETS.spriteMap = Object.assign({}, ASSETS.spriteMap, j.spriteMap);
		}
		/* 舞台（robot-pack-v1 type=gal，2026-09-12 T14）：背景与场景映射。
		 * url 由 bot 侧解析并核实过文件存在，这里只做「相对路径 → 绝对地址」的拼接；
		 * 值是空串的一律不采纳——那意味着"没有这张图"，采纳反而会让背景变成 404 空块。
		 * 拉取完成后立即 setBg()：本函数晚于首次渲染返回，不重设就看不见舞台。 */
		if (j.stage && typeof j.stage === 'object' && !Array.isArray(j.stage)) {
			var stg = j.stage;
			if (stg.default_bg) ASSETS.bgDefault = HTTP_BASE + stg.default_bg;
			if (stg.bg_alt) ASSETS.bgAlt = HTTP_BASE + stg.bg_alt;
			if (stg.bg_by_scene && typeof stg.bg_by_scene === 'object') {
				for (var sk in stg.bg_by_scene) {
					if (sk && stg.bg_by_scene[sk]) ASSETS.bgByScene[sk] = HTTP_BASE + stg.bg_by_scene[sk];
				}
			}
			setBg();
		}
	}).catch(function () { /* 保持空缺省：台词行隐藏（rollQuote 已处理空数组） */ });
})();

/* ============================== Live2D / 外部渲染器约定（robot-pack-v1 预留接口，Phase 3 立桩） ==============================
 * 若页面在本文件加载前定义 window.GAL_RENDERER = { init(stage), onFrame(seg), onStop() }，
 * 则 gal 模式的 seg 呈现委托给该渲染器：
 *   init(stage)  —— 启动时调用一次（stage=#stage DOM；渲染器自建图层/加载模型）；
 *   onFrame(seg) —— 每个 seg（{kind:'text'|'action'|'slice', text}）到达时调用；
 *                   返回 false（或抛异常）= 渲染器不处理该段 → 回落内置打字机/页队列（保守回退）；
 *   onStop()     —— 换轮/清队（vnReset，含切离 gal 模式）时调用（渲染器停帧/复位）。
 * renderer 缺省（未定义 window.GAL_RENDERER）= 全部走原路径，行为与历史版本完全一致。 */
var GALR = window.GAL_RENDERER || null;
function galRendererActive() { return !!(GALR && typeof GALR.onFrame === 'function'); }
function galRendererFrame(seg) {
	if (!galRendererActive()) return false;
	try { return GALR.onFrame(seg) !== false; }
	catch (e) { return false; } /* 渲染器异常：本段回落内置渲染，绝不卡轮 */
}
function galRendererStop() {
	if (!(GALR && typeof GALR.onStop === 'function')) return;
	try { GALR.onStop(); } catch (e) {}
}

var TYPING_MS = 28; /* 打字机 ≈35 CPS */

/* ============================== VN 呈现配置口 ============================== */
/* gal 模式标准视觉小说交互：一段对话框一次只呈现一种内容（（动作）页 / 台词页）；
 * 回复逐句暂存为页队列，点击推进（打字中点击=补完本页，已完再点=翻页）；
 * 只有台词页触发语音（动作页静默），翻页即停并作废在飞请求。 */
var VN_ADVANCE = true;         /* 等待点击推进时显示右下角“▼”续读指示 */
var VN_ADVANCE_CHAR = '▼';    /* 续读指示字符 */
var VN_SPLIT_LEN = 36;         /* 台词行超过此字数时按句末标点细切成多页 */
var VN_MERGE_LEN = 4;          /* 细切产生的短碎片并入本行上一页的阈值（字） */
var VN_TTS_TIMEOUT_MS = 45000; /* 单页语音请求超时（超时只跳过本页语音，不阻塞翻页） */

/* ============================== 全局状态 ============================== */
var S = {
	token: '',
	mode: 'qq',
	persona: null, scene: '', doing: '', mood: '', states: [],
	botAvatar: '',
	busy: false, roundEnded: false,
	ws: null, retryDelay: 3000, retryTimer: 0, pingTimer: 0,
	history: [],   /* {who, kind:'text'|'action'|'slice'|'user', text}，上限 200 */
	q: [],         /* chat 段渲染队列（即时气泡串行兜底；gal 段不走此队列） */
	pumping: false,
	tw: null,      /* 当前打字机任务 */
	/* 立绘情绪差分（WS sprite 帧，一轮 reply_start 后、文本 seg 前至多一帧） */
	spriteEmotion: '', /* 本轮情绪词（bot 自标任意词；换卡清空回默认半身） */
	spriteSeen: false, /* 本轮已收到 sprite 帧（整轮只认第一帧，输出期间不再切换） */
	spriteUrl: '',     /* 当前已成功显示的立绘 URL（去重与回退止步判断） */
	spriteFailed: '',  /* 本轮已探明 404 的差分 URL（state 帧重复应用不重复探测） */
	spriteGen: 0       /* 立绘探测代际：新切换发起后旧探测回调作废 */
};

/* ============================== 小工具 ============================== */
function el(id) { return document.getElementById(id); }

function toast(msg) {
	var box = el('toast-box');
	var t = document.createElement('div');
	t.className = 'toast';
	t.textContent = msg;
	box.appendChild(t);
	while (box.children.length > 4) box.removeChild(box.firstChild);
	setTimeout(function () {
		t.style.opacity = '0';
		setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 350);
	}, 3200);
}

/* 文本解析：全角（…）块 → 独立样式段。
 * 兼容两种口径：text 段内嵌（动作）块（服务端不拆）/ kind=action 独立段。 */
var RE_ACTION = /（[^（）]*）/g;
function parseText(raw) {
	var parts = [], last = 0, m;
	RE_ACTION.lastIndex = 0;
	while ((m = RE_ACTION.exec(raw))) {
		if (m.index > last) parts.push({ text: raw.slice(last, m.index), cls: '' });
		parts.push({ text: m[0], cls: 'act' });
		last = m.index + m[0].length;
	}
	if (last < raw.length) parts.push({ text: raw.slice(last), cls: '' });
	return parts;
}
function stripActions(raw) { return raw.replace(RE_ACTION, '').trim(); }
function normalizeAction(raw) {
	var t = (raw || '').trim();
	if (t && !/^[（(]/.test(t)) t = '（' + t + '）'; /* 服务端不带括号时补全角括号 */
	return t;
}

function personaName() { return (S.persona && (S.persona.name || S.persona.key)) || ''; }

function pushHistory(who, kind, text) {
	S.history.push({ who: who, kind: kind, text: text });
	if (S.history.length > 200) S.history.shift();
}

/* ============================== 状态应用 ============================== */
function applyState(d) {
	if (!d) return;
	removeVeil();
	if (d.mode) applyMode(d.mode);
	if (d.persona) setPersona(d.persona);
	S.scene = d.scene || '';
	S.doing = d.doing || '';
	S.mood = d.mood || '';
	S.states = Array.isArray(d.states) ? d.states : [];
	S.botAvatar = d.bot_avatar || '';
	renderStatus();
	setBg();
}

function renderStatus() {
	var strip = el('status-strip');
	strip.textContent = '';
	function badge(text, cls) {
		if (!text) return;
		var s = document.createElement('span');
		s.className = 'badge' + (cls ? ' ' + cls : '');
		s.textContent = text;
		strip.appendChild(s);
	}
	if (S.persona && S.persona.name) badge(S.persona.name);
	badge(S.scene);
	badge(S.doing);
	badge(S.mood ? '心情·' + S.mood : '', 'mood');
	S.states.forEach(function (t) { badge(t, 'st'); });
	el('chat-head-name').textContent = personaName();
	renderChatHeadAvatar();
}

/* ---------- chat 头部：圆形头像 + 随机经典台词（生活轨迹信息不再进聊天头部） ---------- */
function renderChatHeadAvatar() {
	var img = el('chat-head-avatar');
	var fb = el('chat-head-ava-fb');
	fb.textContent = Array.from(personaName() || '?')[0] || '?';
	var url = S.botAvatar || '';
	if (!url) { /* 空串：无图，姓名首字圆形占位 */
		img.onload = null; img.onerror = null;
		img.style.display = 'none';
		img.removeAttribute('src');
		fb.style.display = 'flex';
		return;
	}
	img.onload = function () {
		img.onload = null;
		img.style.display = 'block';
		fb.style.display = 'none';
	};
	img.onerror = function () { /* 加载失败：退回首字占位 */
		img.onerror = null;
		img.style.display = 'none';
		fb.style.display = 'flex';
	};
	if (img.getAttribute('src') !== url) img.src = url;
	else if (img.complete && img.naturalWidth > 0) img.onload();
}

function rollQuote() {
	var line = el('chat-head-quote');
	var baseKey = plainKey(S.persona && S.persona.bust) || plainKey(S.persona && S.persona.key) || '';
	var list = (ASSETS.quotesByCard && ASSETS.quotesByCard[baseKey]) || QUOTES_FALLBACK;
	if (!list.length) { line.style.display = 'none'; return; }
	line.style.display = 'block'; /* CSS 默认 none：须显式打开（''会回落到 CSS 的 none） */
	var pick = list[Math.floor(Math.random() * list.length)];
	line.textContent = '「' + pick.q + '」' + (pick.by ? ' ——' + pick.by : '');
}

/* ---------- 三态模式视图切换 ---------- */
function applyMode(m) {
	var prev = S.mode;
	S.mode = m;
	el('stage').style.display = (m === 'gal') ? 'block' : 'none';
	el('chat-wrap').style.display = (m === 'chat') ? 'flex' : 'none';
	el('qq-panel').style.display = (m === 'qq') ? 'flex' : 'none';
	el('status-strip').style.display = (m === 'gal') ? '' : 'none'; /* 聊天软件不出现其他信息（2026-09-10 裁决）：状态条仅 gal 模式显示 */
	var btns = document.querySelectorAll('#mode-switch .seg-btn');
	for (var i = 0; i < btns.length; i++) {
		btns[i].classList.toggle('cur', btns[i].getAttribute('data-mode') === m);
	}
	if (m !== prev) {
		S.q.length = 0;    /* 丢弃跨模式的未渲染段 */
		vnReset();         /* 立即清空 VN 页队列、续读指示与语音（切离 gal 同样生效） */
		if (S.tw && S.tw.running) S.tw.finish(); /* 当前打字机立即补全（作废回调不再进等点击态） */
		if (S.roundEnded) setBusy(false); /* 本轮服务端已收尾：未翻完页随切换丢弃，直接解禁输入 */
		if (m === 'chat') rollQuote(); /* 每轮进入聊天模式：随机换一条头部台词 */
	}
	hideTyping();
	if (S.busy) showTyping();
}

function setMode(m) {
	applyMode(m); /* 乐观更新；服务端持久化后回 state 帧再校正 */
	sendWs({ type: 'mode', mode: m });
}

/* ---------- 立绘（gal 规格为半身：bust 优先、full 兜底，都无则姓名首字占位。
 * bot 自标情绪差分帧再优先一档：bust4w/<卡键>_<情绪词>.webp；差分文件不存在是常态
 * （用户按需做图），预载探测逐级回退，全失败保持当前立绘不白屏。
 * 切点仅两处：sprite 帧（一轮一次）与 persona 变化；文本页/翻页/语音不触建立绘切换 ---------- */
function resolveCharUrl(u, base) {
	if (!u) return '';
	if (/^(https?:)?\/\//i.test(u) || u.charAt(0) === '/') return u;
	var ext = /\.(webp|png|jpe?g)$/i.test(u) ? '' : '.webp'; /* 扩展名判断用原始键 */
	/* 卡键 encodeURIComponent：防御今后中文卡键拼 URL 的边界问题 */
	return (base || '') + encodeURIComponent(u) + ext;
}

function plainKey(u) { /* 纯卡键（非完整 URL）才可拼情绪差分键 */
	return (u && !/^(https?:)?\/\//i.test(u) && u.charAt(0) !== '/') ? u : '';
}

/* 组装当前立绘候选链并切换：差分 → 默认半身 → 全身（gal 半身规格：bust→full） */
function applyPersonaSprite(allowPlaceholder) {
	var p = S.persona || {};
	var name = p.name || p.key || '';
	var emo = S.spriteEmotion || '';
	var chain = [];
	var baseKey = plainKey(p.bust) || plainKey(p.key);
	if (emo && baseKey) {
		/* 候选差分：映射词优先（性格立绘），原基调词兜底（用户直接用中文命名也能命中） */
		var map = (ASSETS.spriteMap || {})[baseKey] || {};
		var words = [];
		if (map[emo]) words.push(map[emo]);
		if (words.indexOf(emo) < 0) words.push(emo);
		for (var wi = 0; wi < words.length; wi++) {
			var diff = resolveCharUrl(baseKey + '_' + words[wi], ASSETS.bustBase);
			if (diff !== S.spriteFailed && chain.indexOf(diff) < 0) chain.push(diff); /* 本轮已探明 404 的差分不重复探测 */
		}
	}
	if (p.bust) chain.push(resolveCharUrl(p.bust, ASSETS.bustBase));
	if (p.full) chain.push(resolveCharUrl(p.full, ASSETS.fullBase));
	loadSpriteChain(chain, name, allowPlaceholder);
}

/* 按候选链 new Image() 预载探测：首个可用项交叉淡入；链中命中当前已显示 URL 即止
 * （不重探不闪，差分 404 时自然保持当前半身/全身）；全部失败——换卡场景退首字占位，
 * 其余保持当前立绘。gen 作废交叠探测（state 帧与 sprite 帧并发时旧回调失效）。 */
function loadSpriteChain(chain, name, allowPlaceholder) {
	var fb = el('chara-fallback');
	var gen = ++S.spriteGen;
	var i = 0;
	fb.textContent = Array.from(name || '?')[0] || '?';
	if (!chain.length) { fb.style.display = 'block'; S.spriteUrl = ''; return; }
	(function walk() {
		if (gen !== S.spriteGen) return; /* 已有更新的切换发起：本次探测作废 */
		if (i >= chain.length) {
			if (allowPlaceholder) { fb.style.display = 'block'; S.spriteUrl = ''; }
			return; /* 同卡重应用/差分回退全失败：保持当前立绘 */
		}
		var url = chain[i++];
		if (url === S.spriteUrl) return; /* 候选即当前图：不重复切换 */
		var probe = new Image();
		probe.onload = function () {
			if (gen !== S.spriteGen) return;
			S.spriteUrl = url;
			swapSprite(url, name, true);
		};
		probe.onerror = function () {
			if (gen !== S.spriteGen) return;
			if (i === 1 && S.spriteEmotion) S.spriteFailed = url; /* 仅差分（链首）记 404 备忘 */
			walk();
		};
		probe.src = url;
	})();
}

function setPersona(p) {
	var name = p.name || p.key || '';
	var keyChanged = !S.persona || S.persona.key !== p.key;
	S.persona = p;
	if (keyChanged) { /* 换卡：清情绪差分与 404 备忘，回默认半身 */
		S.spriteEmotion = '';
		S.spriteFailed = '';
		S.spriteUrl = '';
	}
	el('nameplate').textContent = name;
	applyPersonaSprite(keyChanged); /* keyChanged 兼作“允许退首字占位”标记 */
	renderStatus();
}

function swapSprite(src, name, crossfade) {
	var layer = el('chara-layer');
	var fb = el('chara-fallback');
	fb.textContent = Array.from(name || '?')[0] || '?';
	if (!src) { /* 无图：姓名首字大字占位 */
		fb.style.display = 'block';
		return;
	}
	fb.style.display = 'none';
	var cur = layer.querySelector('.cimg.cur') || el('chara-a');
	var nxt = (cur === el('chara-a')) ? el('chara-b') : el('chara-a');
	nxt.onload = function () {
		nxt.classList.add('cur');       /* 新图 0.25s 淡入盖旧图 */
		cur.classList.remove('cur');
		layer.classList.add('show');    /* 首次进场 0.35s 淡入 */
	};
	nxt.onerror = function () {
		nxt.classList.remove('cur');
		fb.style.display = 'block';     /* 图挂了退回首字占位 */
	};
	nxt.src = src;
	if (crossfade === false && nxt.complete && nxt.naturalWidth > 0) nxt.onload();
}

function bounceSprite() { /* 新台词段：立绘小弹跳 0→6px→0，0.25s */
	var inner = el('chara-inner');
	inner.classList.remove('bounce');
	void inner.offsetWidth;
	inner.classList.add('bounce');
}

/* ---------- 背景（scene 映射 → 默认图 → 备选图 → CSS 渐变） ---------- */
var bgTried = 0;
/* scene → 背景：**三级匹配**（2026-09-12 T14）。
 * 为什么必须三级：S.scene 来自 lifesim，是**模型自由生成的文本**（如「天台上看星星」），
 * 而舞台包的 bg_by_scene 键是人写的短词（「天台」）。只做等值匹配等于没做——命中率接近 0。
 * ① 精确撞上 → 用它；② 否则取**最长的**被包含键（键越长越具体，「天台」胜过「天」）；
 * ③ 都没有 → 返回空串，交回调用方走默认图（bgDefault）→ 备选图（bgAlt）→ CSS 渐变。 */
function sceneBg(scene) {
	if (!scene || !ASSETS.bgByScene) return '';
	if (ASSETS.bgByScene[scene]) return ASSETS.bgByScene[scene];
	var best = '', bestLen = 0;
	for (var k in ASSETS.bgByScene) {
		if (!k || !ASSETS.bgByScene[k]) continue;
		if (scene.indexOf(k) >= 0 && k.length > bestLen) { best = ASSETS.bgByScene[k]; bestLen = k.length; }
	}
	return best;
}
function setBg() {
	bgTried = 0;
	var url = sceneBg(S.scene) || ASSETS.bgDefault || '';
	applyBg(url);
}
function applyBg(url) {
	var img = el('bg-img');
	var fb = el('bg-fallback');
	if (!url) { img.style.display = 'none'; fb.style.display = 'block'; return; }
	img.style.display = 'block';
	fb.style.display = 'none';
	img.onerror = function () {
		img.onerror = null;
		bgTried += 1;
		if (bgTried === 1 && ASSETS.bgAlt) applyBg(ASSETS.bgAlt);
		else { img.style.display = 'none'; fb.style.display = 'block'; }
	};
	if (img.getAttribute('src') !== url) img.src = url;
}

/* ---------- 打字机（逐字 28ms；点击两段式：未显完=立即补全） ---------- */
function typeTokens(parts, done) {
	hideTyping();
	var box = el('dialog-text');
	box.textContent = '';
	var flat = [];
	parts.forEach(function (p) {
		if (!p.text) return;
		var sp = document.createElement('span');
		if (p.cls) sp.className = p.cls;
		box.appendChild(sp);
		Array.from(p.text).forEach(function (ch) { flat.push({ sp: sp, ch: ch }); });
	});
	var task = { running: true, finish: function () {} };
	var i = 0;
	var timer = 0;
	var finished = false;
	function stop() {
		if (finished) return;
		finished = true;
		clearInterval(timer);
		task.running = false;
		if (S.tw === task) S.tw = null;
		var cb = done;
		done = null;
		if (cb) cb();
	}
	function step() {
		if (i >= flat.length) { stop(); return; }
		var f = flat[i++];
		f.sp.textContent += f.ch;
		box.scrollTop = box.scrollHeight;
	}
	task.finish = function () {
		while (i < flat.length) {
			var f = flat[i++];
			f.sp.textContent += f.ch;
		}
		stop();
	};
	S.tw = task;
	if (!flat.length) { stop(); return; }
	timer = setInterval(step, TYPING_MS);
}

/* ============================== VN 呈现（gal 模式：逐页点击推进） ============================== */
/* 单元：{kind:'action'|'dialogue', text}。服务端 seg 到达即切分入队（逐句暂存），
 * 同一时刻对话框只显示一页：action 页=斜体灰整行（不触发语音），dialogue 页=正常台词（触发语音）。
 * 页状态机：typing（打字机进行中）→ waiting（打完等点击，显示“▼”）→ 点击推进下一页；
 * 最后一页推进完且 reply_end 已到 → 本轮呈现结束，解禁输入框（期间输入保持禁用）。 */
var VN = {
	units: [],  /* 本轮页队列（含未翻到的暂存页） */
	idx: 0,     /* 当前页指针 */
	waiting: false, /* 当前页打字完成、等待点击推进 */
	voiceWait: null, /* 声画同步等待窗：等本页语音合成（热修十三 A1：showTyping 守卫据它放行；回调沿 epoch/gen 自弃） */
	epoch: 0,   /* 队列代际：换轮/切模式清队后，旧页的打字机/语音回调据此作废 */
	ind: null,  /* “▼”续读指示元素（懒创建） */
	voices: {}  /* 页下标→wav b64：reply_end 后预取全部台词语音（2026-09-10 用户裁决：gal 模式必定触发语音）；作废沿 VN.epoch */
};

function vnEnqueue(units) {
	if (!units || !units.length) return;
	VN.units.push.apply(VN.units, units);
	kickPrefetch(); /* seg 一到即预取：不等 reply_end，读第 1 页时后续页已在合成 */
	vnKick();
}

function vnKick() {
	/* 队列驱动入口：空闲（无打字、无等待）才翻下一页；暂无页则试收尾 */
	if (S.mode !== 'gal') return;
	if (S.tw && S.tw.running) return; /* 当前页打字中：新页排队，打完等点击 */
	if (VN.waiting) return;           /* 当前页等点击：新页追加队尾，点击后自然到达 */
	if (VN.idx < VN.units.length) { vnShowPage(); return; }
	vnSettle();
}

function vnShowPage() {
	var u = VN.units[VN.idx];
	var tok = VN.epoch; /* 页代际：清队/换轮后旧回调不再生效 */
	hideTyping();
	vnShowIndicator(false);
	bounceSprite();
	var parts = [u.kind === 'action' ? { text: u.text, cls: 'act' } : { text: u.text }];
	var done = function () {
		if (tok !== VN.epoch) { vnKick(); return; } /* 页已作废：接力驱动新队列，防卡死 */
		VN.waiting = true;
		vnShowIndicator(true);
	};
	if (u.kind !== 'dialogue') { typeTokens(parts, done); return; }
	/* 台词页声画同步（2026-09-11 用户裁决：延长整体等待，等语音就绪再出字，文字与语音同步呈现）：
	 * 缓存命中=即播即出；未命中=先等合成（requestTts 45s 上限，期间以"…"指示；点击=跳过语音直接出字），
	 * 语音开播与打字机同帧启动。 */
	if (VN.voices[VN.idx] !== undefined) {
		typeTokens(parts, done);
		playWav(VN.voices[VN.idx], function () {});
		return;
	}
	/* 热修十三 A1：先置等待窗再 showTyping——守卫以 voiceWait 为放行依据，合成等待期“…”可见 */
	var myGen = ++TT.gen;
	VN.voiceWait = { gen: myGen, parts: parts, tok: tok, idx: VN.idx };
	showTyping(); /* 合成等待指示（…”） */
	ensureVoice(VN.idx, u.text, function (res) {
		if (VN.voiceWait && VN.voiceWait.gen === myGen) VN.voiceWait = null; /* 只清自己的等待窗（防旧代回调误清新页等待窗） */
		if (tok !== VN.epoch || myGen !== TT.gen) return; /* 已翻页/换轮/点击跳过：丢弃 */
		hideTyping();
		if (res.ok) { VN.voices[VN.idx] = res.b64; playWav(res.b64, function () {}); }
		else { toastVoiceSkip('语音跳过：' + res.err); }
		typeTokens(parts, done);
	});
}

/* 点击推进（三段式）：等语音合成=跳过等待直接出字（本页放弃语音）；打字中=立即补完本页；已完=翻下一页 */
function vnAdvance() {
	if (VN.voiceWait) {
		TT.gen += 1; /* 作废等语音回调（其返回后也不再出字/出声） */
		var vw = VN.voiceWait;
		VN.voiceWait = null;
		hideTyping();
		vnStopVoice();
		typeTokens(vw.parts, function () {
			if (vw.tok !== VN.epoch) { vnKick(); return; }
			VN.waiting = true;
			vnShowIndicator(true);
		});
		return;
	}
	if (S.tw && S.tw.running) { S.tw.finish(); return; }
	if (VN.waiting) vnNext();
}

function vnNext() {
	vnStopVoice();
	VN.waiting = false;
	vnShowIndicator(false);
	VN.idx += 1;
	if (VN.idx < VN.units.length) { vnShowPage(); return; }
	vnSettle(); /* 暂存页已翻完：reply_end 未到则等后续 seg 入队，到了即收尾 */
}

/* 呈现收尾判定：队列空、无打字、无语音等待、无等待点击且 reply_end 已到 → 结束本轮呈现并解禁输入框 */
function vnSettle() {
	if (S.mode !== 'gal') return;
	if (S.tw && S.tw.running) return;
	if (VN.voiceWait) return; /* 等语音合成期间不解禁输入（声画同步等待窗） */
	if (VN.waiting) return;
	if (VN.idx < VN.units.length) return;
	if (!S.roundEnded) return;
	setBusy(false);
}

/* 换轮/切模式：清空页队列与语音、”▼”隐藏，epoch+1 作废旧页回调（旧打字机打完自行接力退出） */
function vnReset() {
	galRendererStop(); /* 外部渲染器停帧/复位（缺省=no-op） */
	VN.epoch += 1;
	VN.units.length = 0;
	VN.idx = 0;
	VN.waiting = false;
	VN.voiceWait = null; /* 声画同步等待窗作废（其回调沿 epoch/gen 双重自弃） */
	VN.voices = {};  /* 换轮：上轮预取缓存作废 */
	pfInFlight = {};  /* 在飞表清零 */
	pfWaiters = {};   /* 等待者列表清零（热修十三 A2：旧代等待者随队列一并作废） */
	pfFails = 0;      /* 连败计数清零 */
	vnStopVoice();
	vnShowIndicator(false);
	el('dialog-text').textContent = ''; /* 清上轮残留文本：防旧末页与新一轮“…”/首页叠加 */
}

function vnShowIndicator(on) {
	if (!VN.ind) {
		var n = document.createElement('span');
		n.id = 'vn-next';
		n.textContent = VN_ADVANCE_CHAR;
		el('dialog-layer').appendChild(n);
		VN.ind = n;
	}
	VN.ind.style.display = (VN_ADVANCE && on) ? 'block' : 'none';
}

/* ---------- seg → VN 单元切分 ---------- */
/* 正文段：每个（…）块 = 一个 action 页（保留全角括号）；相邻台词按“一行一页”切成
 * dialogue 页，超长行再按句末标点细切。边界：连续动作块各成一页；动作后无台词 /
 * 纯台词 / 空白段自然成立（空单元丢弃）；空（…）块丢弃。 */
var VN_SENTENCE_RE = /[^。！？…!?~]*[。！？…!?~]+|[^。！？…!?~]+$/g;
function splitVnUnits(raw) {
	var units = [], last = 0, m;
	function pushDialogue(t) {
		splitVnSentences(t).forEach(function (s) { units.push({ kind: 'dialogue', text: s }); });
	}
	RE_ACTION.lastIndex = 0;
	while ((m = RE_ACTION.exec(raw))) {
		pushDialogue(raw.slice(last, m.index));
		if (m[0].replace(/[（）]/g, '').trim()) units.push({ kind: 'action', text: m[0] });
		last = m.index + m[0].length;
	}
	pushDialogue(raw.slice(last));
	return units;
}
function splitVnSentences(t) {
	var out = [];
	String(t || '').split(/\r?\n/).forEach(function (line) {
		line = line.trim();
		if (!line) return;
		var arr = [];
		var parts = line.length > VN_SPLIT_LEN ? (line.match(VN_SENTENCE_RE) || [line]) : [line];
		parts.forEach(function (p) {
			p = p.trim();
			if (!p) return;
			if (arr.length && p.length < VN_MERGE_LEN) arr[arr.length - 1] += p; /* 碎片并入本行上一页 */
			else arr.push(p);
		});
		out.push.apply(out, arr);
	});
	return out;
}

/* ---------- gal seg 入队（slice 窄条即时 / action·text 切分成页） ---------- */
function galSegToUnits(it) {
	if (it.kind === 'slice') { /* 催眠切片：独立窄条非阻塞即时显示，不占对话页（行为保持现状） */
		var txt = normalizeAction(it.text);
		el('slice-box').textContent = txt;
		el('slice-box').style.display = 'block';
		pushHistory(personaName(), 'slice', txt);
		return;
	}
	if (it.kind === 'action') { /* 独立（动作）段：单页斜体 */
		var atxt = normalizeAction(it.text);
		if (!atxt) return;
		pushHistory(personaName(), 'action', atxt);
		vnEnqueue([{ kind: 'action', text: atxt }]);
		return;
	}
	/* kind=text：正文（内嵌（动作）块）→ 交错切分为 action/dialogue 页 */
	if (!it.text) return;
	pushHistory(personaName(), 'text', it.text);
	vnEnqueue(splitVnUnits(it.text));
}

/* ---------- 段渲染队列（gal=切分入 VN 页队列 / chat=即时气泡） ---------- */
function enqueueSeg(item) {
	if (S.mode === 'gal') {
		/* Live2D 等外部渲染器接管点（GAL_RENDERER 约定，见文件顶部）：onFrame 返回 false=回落内置 */
		if (galRendererFrame(item)) return;
		galSegToUnits(item);
		return;
	}
	S.q.push(item);
	pumpQ();
}
function pumpQ() {
	if (S.pumping) return;
	var it = S.q.shift();
	if (!it) return;
	S.pumping = true;
	renderChatSeg(it); /* chat 即时渲染，串行仅兜底 */
	S.pumping = false;
	if (S.q.length) pumpQ();
}

function renderChatSeg(it) {
	/* chat 模式：动作/切片/语音服务端已剥，页面再兜底滤一次（…）块不显示 */
	if (it.kind !== 'text') return;
	var txt = stripActions(it.text);
	if (!txt) return;
	hideTyping();
	appendBubble('bot', txt);
	pushHistory(personaName(), 'text', it.text);
}

function appendBubble(role, text) {
	var log = el('chat-log');
	var b = document.createElement('div');
	b.className = 'bubble ' + role;
	b.textContent = text;
	log.appendChild(b);
	log.scrollTop = log.scrollHeight;
}

/* ---------- “输入中”指示（reply_start → 首段/回复结束） ---------- */
var typingLine = null;   /* gal：对话框内三点 */
var typingBubble = null; /* chat：独立气泡 */
function showTyping() {
	if (S.mode === 'chat') {
		var log = el('chat-log');
		if (!typingBubble) {
			typingBubble = document.createElement('div');
			typingBubble.className = 'bubble bot';
			typingBubble.innerHTML = '<span class="typing"><i></i><i></i><i></i></span>';
		}
		if (typingBubble.parentNode !== log) log.appendChild(typingBubble);
		log.scrollTop = log.scrollHeight;
	} else if (S.mode === 'gal') {
		/* 热修十三 A1：语音等待窗（VN.voiceWait 活跃）放行——否则合成等待期“…”被
		idx<units.length 恒真拦下、对话框全空；打字机中/续读等待仍不叠“…” */
		if (S.tw || VN.waiting || (VN.idx < VN.units.length && !VN.voiceWait)) return;
		var dt = el('dialog-text');
		if (!typingLine) {
			typingLine = document.createElement('span');
			typingLine.className = 'typing';
			typingLine.innerHTML = '<i></i><i></i><i></i>';
		}
		if (typingLine.parentNode !== dt) dt.appendChild(typingLine);
		dt.scrollTop = dt.scrollHeight;
	}
}
function hideTyping() {
	if (typingLine && typingLine.parentNode) typingLine.parentNode.removeChild(typingLine);
	if (typingBubble && typingBubble.parentNode) typingBubble.parentNode.removeChild(typingBubble);
}

/* ---------- 忙碌态（reply 期间输入禁用并显示“…”） ---------- */
function setBusy(b) {
	S.busy = b;
	el('msg-input').disabled = b;
	el('chat-input').disabled = b;
	el('btn-send').disabled = b;
	el('chat-send').disabled = b;
	el('msg-input').placeholder = b ? '…' : '输入…';
	el('chat-input').placeholder = b ? '…' : '输入消息…';
}

/* ---------- 发言 ---------- */
function sendMessage(inputId) {
	var inp = el(inputId);
	var text = (inp.value || '').trim();
	if (!text) return;
	if (S.busy) { toast('上一条回复还在生成中，请稍候'); return; }
	if (!sendWs({ type: 'msg', text: text })) return;
	inp.value = '';
	pushHistory('你', 'user', text);
	if (S.mode === 'chat') appendBubble('user', text);
}

/* ============================== WebSocket ============================== */
function sendWs(obj) {
	if (S.ws && S.ws.readyState === 1) {
		try { S.ws.send(JSON.stringify(obj)); return true; }
		catch (e) { toast('发送失败：' + e.message); return false; }
	}
	toast('未连接到服务');
	return false;
}

function connectWS() {
	clearTimeout(S.retryTimer);
	if (S.ws) {
		var old = S.ws;
		S.ws = null;
		old.onclose = null; old.onerror = null; old.onmessage = null;
		try { old.close(); } catch (e) {}
	}
	if (!S.token) return; /* token 未就绪，fetchState 成功后会再调 */
	var ws;
	try { ws = new WebSocket(WS_URL); } catch (e) { scheduleReconnect(); return; }
	S.ws = ws;
	ws.onopen = function () {
		try { ws.send(JSON.stringify({ type: 'auth', token: S.token })); } catch (e) {}
		startPing();
	};
	ws.onmessage = handleFrame;
	ws.onerror = function () {};
	ws.onclose = function () {
		stopPing();
		if (S.ws === ws) S.ws = null;
		scheduleReconnect(); /* 无限重连，退避 3s→10s 封顶 */
	};
}
function scheduleReconnect() {
	clearTimeout(S.retryTimer);
	S.retryTimer = setTimeout(connectWS, S.retryDelay);
	S.retryDelay = Math.min(10000, Math.round(S.retryDelay * 1.6));
}
function startPing() {
	stopPing();
	S.pingTimer = setInterval(function () {
		if (S.ws && S.ws.readyState === 1) {
			try { S.ws.send('{"type":"ping"}'); } catch (e) {}
		}
	}, 30000);
}
function stopPing() { clearInterval(S.pingTimer); S.pingTimer = 0; }

function handleFrame(ev) {
	var m;
	try { m = JSON.parse(ev.data); } catch (e) { return; }
	if (!m || typeof m.type !== 'string') return;
	switch (m.type) {
		case 'auth_ok':
			S.retryDelay = 3000;
			applyState(m); /* persona/scene/doing/mood/states/mode */
			setBusy(false); /* 盲审 P2-2：轮中断线重连后不残留 busy 锁（服务端轮任务已随断线收尾） */
			break;
		case 'auth_err':
			toast('认证失败：' + (m.reason || 'token 无效') + '，重新获取中…');
			S.token = '';
			fetchState(); /* 拿到 token 后 connectWS 会顶掉旧重连计时 */
			break;
		case 'reply_start':
			S.roundEnded = false;
			S.spriteSeen = false; /* 新一轮差分帧窗口：本轮只认第一帧 */
			var sb = el('slice-box');
			sb.textContent = '';
			sb.style.display = 'none';
			vnReset(); /* 新一轮：替换页队列从新轮第一页开始（上轮未翻完页一并丢弃），停旧语音 */
			setBusy(true);
			showTyping();
			break;
		case 'reply_end':
			hideTyping();
			rollQuote(); /* 每轮回复结束：随机换一条头部台词（同一轮内不变） */
			S.roundEnded = true;
			if (S.mode === 'gal') { kickPrefetch(); vnSettle(); } /* 页未翻完则输入保持禁用，翻完自动解禁；预取全部台词语音保证必定触发 */
			else setBusy(false);
			break;
		case 'seg':
			if (typeof m.text !== 'string' || !m.text) break;
			if (S.mode === 'qq') break; /* qq 模式无输入通道，段属异常，兜底丢弃 */
			enqueueSeg({ kind: (m.kind === 'action' || m.kind === 'slice') ? m.kind : 'text', text: m.text });
			break;
		case 'sprite':
			/* 情绪差分帧：输出开始时切换一次，整轮不再切换。有情绪词=切该差分
			   （映射→原词→默认半身 404 逐级回退）；无情绪词=回默认半身（防上轮差分残留） */
			if (S.spriteSeen) break;
			S.spriteSeen = true;
			var emo = (typeof m.emotion === 'string' ? m.emotion : '').trim();
			S.spriteEmotion = emo;
			S.spriteFailed = ''; /* 新一轮重探差分（用户期间可能已补图） */
			applyPersonaSprite(false);
			break;
		case 'state':
			applyState(m);
			break;
		case 'err':
			toast(m.reason === 'busy' ? '上一条回复还在进行中，请稍候再发' : ('出错：' + (m.reason || '未知')));
			break;
		case 'pong':
			break;
	}
}

/* ============================== 语音（gal 模式：仅台词页） ============================== */
/* 翻到 dialogue 页才请求该页 TTS 并播放；action 页不请求。
 * 生命周期：翻页/换轮/切模式 → gen 自增作废在飞请求与刚返回的音频，并立即停播。
 * 请求失败/超时只 toast 跳过本页语音，不阻塞点击翻页。 */
var TT = { gen: 0 };

/* P3 节流：无语音包卡每个台词页都会失败一次，同原因 5s 内只弹首个 toast，防刷屏 */
var ttsSkipMemo = { msg: '', ts: 0 };
function toastVoiceSkip(msg) {
	var now = Date.now();
	if (msg === ttsSkipMemo.msg && (now - ttsSkipMemo.ts) < 5000) return;
	ttsSkipMemo.msg = msg;
	ttsSkipMemo.ts = now;
	toast(msg);
}

/* （原 vnSpeakPage 已并入 vnShowPage 的声画同步路径：缓存命中即播即出，未命中先合成后出字。
 *  预取/缓存回填由 kickPrefetch 与 vnShowPage 的等语音路径共同回填 VN.voices。） */

/* 统一语音获取入口（热修十三 A2，2026-09-12）：缓存命中直取 → 在飞挂等待者 → 否则派发。
 * 修复首页双发：曾 vnShowPage 与 kickPrefetch 对同一句各发一次 requestTts（pfInFlight 不互通），
 * 后端 _tts_lock 串行下首页等待≈2×合成时长。epoch 作废语义保持：旧代回调只作废自身，
 * 不写缓存、不计数、不惊动新一代等待者；在飞位按 epoch 记名（vnReset 清空后同页重发时，
 * 旧回调不得清掉新一代的在飞位/等待者）。 */
var pfInFlight = {}; /* 页下标→epoch：在飞请求（vnShowPage 与预取共用，防重复派发） */
var pfWaiters = {};  /* 页下标→回调列表：在飞完成时一并回调（A2 等待者列表；vnReset 清零） */
var pfFails = 0;     /* 连败计数：≥2 短路本轮预取（无音色包等必败场景，盲审 P3-3）；vnReset 清零 */

function ensureVoice(idx, text, cb) {
	var ep = VN.epoch;
	if (VN.voices[idx] !== undefined) { cb({ ok: true, b64: VN.voices[idx] }); return; }
	if (pfInFlight[idx] !== undefined) {
		if (!pfWaiters[idx]) pfWaiters[idx] = [];
		pfWaiters[idx].push(cb);
		return;
	}
	pfInFlight[idx] = ep;
	pfWaiters[idx] = [cb];
	requestTts(text, function (idx, ep) {
		return function (res) {
			var ws = [];
			if (pfInFlight[idx] === ep) { /* 在飞位仍属本请求：清位并带走等待者 */
				delete pfInFlight[idx];
				ws = pfWaiters[idx] || [];
				delete pfWaiters[idx];
			} /* 否则=新一代请求已接管该页（vnReset 后同页重发）：本回调到此作废 */
			if (ep !== VN.epoch) return; /* 旧代回调：缓存不写、失败不计数、等待者不惊动 */
			if (res.ok) VN.voices[idx] = res.b64;
			else pfFails += 1;
			for (var k = 0; k < ws.length; k++) { try { ws[k](res); } catch (e) {} }
			if (pfFails < 2) kickPrefetch(); /* 失败也继续下一行；连败 2 次短路（无包场景） */
		};
	}(idx, ep));
}

function kickPrefetch() {
	/* 增量预取（2026-09-10 用户裁决“必定触发语音”）：seg 入队即开拉，早于 reply_end——
	 * 用户读第 1 页的时间里后续页已合成完毕，翻到即播。单在飞串行，沿 VN.epoch 作废。
	 * 热修十三 A2：改走 ensureVoice（等待者去重，与 vnShowPage 不再双发）；派发顺序当前页
	 * 优先——从 VN.idx 环形扫描（当前页最先合成，缓存命中页零等待）。 */
	if (S.mode !== 'gal') return;
	for (var k = 0; k < VN.units.length; k++) {
		var i = (VN.idx + k) % VN.units.length;
		if (VN.units[i].kind !== 'dialogue' || VN.voices[i] !== undefined || pfInFlight[i] !== undefined) continue;
		ensureVoice(i, VN.units[i].text, function (ep) {
			return function (res) {
				if (ep !== VN.epoch) return; /* 旧代回调：作废（在飞位/等待者清理在 ensureVoice 内已完成） */
				if (!res.ok) pfFails += 1;
				if (pfFails < 2) kickPrefetch(); /* 失败也继续下一行；连败 2 次短路（无包场景） */
			};
		}(VN.epoch));
		return; /* 单在飞：一次只派发一个，完成回调再续 */
	}
}

function vnStopVoice() {
	TT.gen += 1; /* 在飞请求与回调一并作废 */
	var au = el('tts-audio');
	au.onended = null;
	au.onerror = null;
	try { au.pause(); } catch (e) {}
	au.removeAttribute('src');
}

function playWav(b64, fin) {
	var au = el('tts-audio');
	au.onended = fin;
	au.onerror = fin;
	au.src = 'data:audio/wav;base64,' + b64;
	var p = au.play();
	if (p && p.catch) p.catch(function () {
		toast('浏览器拦截了语音自动播放，本行跳过');
		fin();
	});
}

function requestTts(text, cb) {
	var ctrl = (typeof AbortController === 'function') ? new AbortController() : null;
	var done = false;
	var timer = 0;
	function finish(res) {
		if (done) return;
		done = true;
		clearTimeout(timer);
		cb(res);
	}
	timer = setTimeout(function () {
		finish({ ok: false, err: '语音请求超时' });
		if (ctrl) { try { ctrl.abort(); } catch (e) {} }
	}, VN_TTS_TIMEOUT_MS);
	fetch(HTTP_BASE + '/gal/tts', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ text: text, tone: S.spriteEmotion || '' }), /* 热修十三 A3：声画情绪同源——语音参考音随本轮立绘基调词取（后端 /gal/tts 已收 tone，零后端改动） */
		signal: ctrl ? ctrl.signal : undefined
	}).then(function (r) { return r.json(); }).then(function (j) {
		if (j && j.wav_b64) finish({ ok: true, b64: j.wav_b64 });
		else finish({ ok: false, err: (j && j.err) || '空响应' });
	}).catch(function (e) {
		finish({ ok: false, err: (e && e.name === 'AbortError') ? '语音请求超时' : e.message });
	});
}

/* ============================== 覆盖层：回想 / 生活轨迹 ============================== */
function openOverlay(id) { el(id).classList.add('open'); }
function closeOverlays() {
	var list = document.querySelectorAll('.overlay.open');
	for (var i = 0; i < list.length; i++) list[i].classList.remove('open');
}
function isOverlayOpen() { return !!document.querySelector('.overlay.open'); }

/* 回想行渲染（原 openHistory 主体抽出的复用路径）：items=[{who,kind,text}]，kind 与
 * S.history 同形（'user'|'text'|'action'|'slice'）；打开定位到底部 */
function _renderHistory(items) {
	var box = el('hist-body');
	box.textContent = '';
	items.forEach(function (h) {
		var d = document.createElement('div');
		d.className = 'hist-item';
		var w = document.createElement('span');
		w.className = 'who ' + (h.kind === 'user' ? 'who-user' : 'who-bot');
		w.textContent = h.kind === 'user' ? '你' : (h.who || 'AI');
		d.appendChild(w);
		if (h.kind === 'text') { /* 保留（动作）斜体样式 */
			parseText(h.text).forEach(function (p) {
				var s = document.createElement('span');
				if (p.cls) s.className = p.cls;
				s.textContent = p.text;
				d.appendChild(s);
			});
		} else {
			var s2 = document.createElement('span');
			if (h.kind === 'action') s2.className = 'act';
			else if (h.kind === 'slice') s2.className = 'slice';
			s2.textContent = h.text;
			d.appendChild(s2);
		}
		box.appendChild(d);
	});
	box.scrollTop = box.scrollHeight; /* 打开定位到底部 */
}

/* 回想（Phase 2b 接库）：优先服务端 /gal/history（跨会话真相源），失败/非数组/空回落
 * 本会话内存 S.history（渲染同走 _renderHistory）。服务端拉取成功即纯服务端渲染——
 * 进行中未落盘轮不显示，下次打开可见（规格允许的简化取舍）；S.history 本会话记录逻辑不动。 */
function openHistory() {
	var box = el('hist-body');
	box.textContent = '';
	var loading = document.createElement('div');
	loading.className = 'ov-loading';
	loading.textContent = '读取中…';
	box.appendChild(loading);
	openOverlay('overlay-history');
	fetch(HTTP_BASE + '/gal/history?limit=100').then(function (r) {
		if (!r.ok) throw new Error('HTTP ' + r.status);
		return r.json();
	}).then(function (rounds) {
		if (!Array.isArray(rounds)) throw new Error('响应格式异常');
		var items = [];
		rounds.forEach(function (rd) {
			(rd && Array.isArray(rd.items) ? rd.items : []).forEach(function (it) {
				if (!it || typeof it.text !== 'string' || !it.text) return;
				/* 服务端 kind 映射：bot→text（parseText 保（动作）斜体）；user/action/slice 同名直过 */
				var kind = it.kind === 'user' ? 'user' : it.kind === 'action' ? 'action'
					: it.kind === 'slice' ? 'slice' : 'text';
				items.push({ who: it.who || '', kind: kind, text: it.text });
			});
		});
		if (!items.length && S.history.length) throw new Error('empty'); /* 服务端无记录：回落内存 */
		_renderHistory(items);
	}).catch(function () {
		_renderHistory(S.history); /* 失败/非数组/服务端空：回落原内存渲染路径 */
	});
}

function openLifelog() {
	openOverlay('overlay-lifelog');
	var body = el('lifelog-body');
	body.textContent = '';
	var loading = document.createElement('div');
	loading.className = 'ov-loading';
	loading.textContent = '读取中…';
	body.appendChild(loading);
	fetch(HTTP_BASE + '/gal/lifelog?limit=200').then(function (r) {
		if (!r.ok) throw new Error('HTTP ' + r.status);
		return r.json();
	}).then(function (arr) {
		if (!Array.isArray(arr)) throw new Error('响应格式异常');
		body.textContent = '';
		var list = arr.slice().sort(function (a, b) { /* 倒序：最新在上 */
			return (Number(b.ts) || 0) - (Number(a.ts) || 0); /* ts 为浮点秒，数值倒序 */
		});
		if (!list.length) {
			var empty = document.createElement('div');
			empty.className = 'ov-loading';
			empty.textContent = '暂无记录';
			body.appendChild(empty);
			return;
		}
		list.forEach(function (it) {
			var row = document.createElement('div');
			row.className = 'lifelog-item';
			var ts = document.createElement('span');
			ts.className = 'ts'; ts.textContent = it.iso || it.ts || ''; /* 优先 ISO 本地时间（服务端字段）；无则退原始值 */
			var doing = document.createElement('span');
			doing.className = 'doing'; doing.textContent = it.doing || '';
			var mood = document.createElement('span');
			mood.className = 'mood'; mood.textContent = it.mood || '—';
			row.appendChild(ts); row.appendChild(doing); row.appendChild(mood);
			body.appendChild(row);
		});
	}).catch(function (e) {
		body.textContent = '';
		var err = document.createElement('div');
		err.className = 'ov-loading';
		err.textContent = '读取失败：' + e.message;
		body.appendChild(err);
	});
}

/* ---------- 返回启动器 / 干员切换（仅 WebView2 虚拟主机内有效） ---------- */
function goLauncher(hash) {
	try {
		var inWebview = (location.protocol === 'http:' || location.protocol === 'https:') &&
			location.host === '127.0.0.1:8080';
		if (!inWebview) { toast('请从启动器内使用'); return; }
		/* 干员（#opera）：2026-09-12 订正——**皮肤自带干员页就用皮肤的**（ark 的 #view-opera/detail.html
		 * 是用户自己设计的界面），所以这里照旧做 URL 导航回皮肤入口的 #opera 锚点，
		 * 不再改走框架页。框架原生干员页只是"皮肤没提供"时的兜底（皮肤包规范 §2 `operaPage` / §9）。 */
		/* ent 参数（2026-09-11）：启动器两个皮肤的 GAL 按钮分别带 ?ent=generic|arknights，
		 * 返回时回到对应皮肤入口（ark 页内 ak-back 的 from=gal 语义不变）；无参回落 generic 根。 */
		var m = location.search.match(/[?&]ent=([^&]+)/);
		var base = 'https://app.local/' +
			((m && decodeURIComponent(m[1]) === 'arknights') ? 'skins/arknights/index.html' : 'index.html');
		var url = base + (hash || '');
		/* from=gal 不可省：皮肤页返回键据此回 GAL 页（跨源 referrer 只剩 origin，判不出来）。 */
		if (hash === '#opera') url = base + '?from=gal#opera';
		location.href = url;
	} catch (e) {
		toast('请从启动器内使用');
	}
}

/* ============================== 启动幕 ============================== */
function veilText(t) {
	var v = el('boot-veil');
	if (!v) return;
	var tx = v.querySelector('.veil-text');
	if (tx) tx.textContent = t;
}
function removeVeil() {
	var v = el('boot-veil');
	if (v && v.parentNode) v.parentNode.removeChild(v);
}

/* 首次加载先取 /gal/state 拿 token 与初始状态，再连 WS */
function fetchState() {
	fetch(HTTP_BASE + '/gal/state').then(function (r) {
		if (!r.ok) throw new Error('HTTP ' + r.status);
		return r.json();
	}).then(function (st) {
		S.token = st.token || '';
		S.retryDelay = 3000;
		applyState(st);
		connectWS();
	}).catch(function (e) {
		veilText('无法连接服务（' + e.message + '），3 秒后重试…');
		setTimeout(fetchState, 3000);
	});
}

/* ============================== 事件绑定与启动 ============================== */
function bindEvents() {
	var btns = document.querySelectorAll('#mode-switch .seg-btn');
	for (var i = 0; i < btns.length; i++) {
		(function (b) {
			b.addEventListener('click', function () { setMode(b.getAttribute('data-mode')); });
		})(btns[i]);
	}
	el('btn-qq-gal').addEventListener('click', function () { setMode('gal'); });
	el('btn-qq-chat').addEventListener('click', function () { setMode('chat'); });
	el('btn-history').addEventListener('click', openHistory);
	el('btn-lifelog').addEventListener('click', openLifelog);
	el('btn-home').addEventListener('click', function () { goLauncher(''); });
	el('btn-opera').addEventListener('click', function () { goLauncher('#opera'); });
	el('hist-close').addEventListener('click', closeOverlays);
	el('lifelog-close').addEventListener('click', closeOverlays);
	['overlay-history', 'overlay-lifelog'].forEach(function (id) {
		el(id).addEventListener('click', function (e) { if (e.target === el(id)) closeOverlays(); });
	});
	el('btn-send').addEventListener('click', function () { sendMessage('msg-input'); });
	el('msg-input').addEventListener('keydown', function (e) {
		if (e.key === 'Enter') { e.preventDefault(); sendMessage('msg-input'); }
	});
	el('chat-send').addEventListener('click', function () { sendMessage('chat-input'); });
	el('chat-input').addEventListener('keydown', function (e) {
		if (e.key === 'Enter') { e.preventDefault(); sendMessage('chat-input'); }
	});
	/* VN 点击推进（两段式）：打字未完点击=立即补完本页；已显完点击=翻下一页（chat 不翻页） */
	el('dialog-layer').addEventListener('click', function (e) {
		if (e.target.closest && e.target.closest('#input-row')) return;
		if (S.mode !== 'gal') return;
		vnAdvance();
	});
	/* 滚轮向上翻看回想：gal 舞台直接触发；chat 需在消息列表顶部继续上滚 */
	var wheelAcc = 0, wheelLock = false;
	document.addEventListener('wheel', function (e) {
		if (isOverlayOpen()) return;
		if (e.deltaY >= 0) { wheelAcc = 0; return; }
		if (S.mode === 'chat' && el('chat-log').scrollTop > 2) { wheelAcc = 0; return; }
		wheelAcc += -e.deltaY;
		if (wheelAcc > 60 && !wheelLock) {
			wheelLock = true;
			wheelAcc = 0;
			openHistory();
			setTimeout(function () { wheelLock = false; }, 800);
		}
	}, { passive: true });
	document.addEventListener('keydown', function (e) {
		if (e.key === 'Escape') closeOverlays();
	});
}

function boot() {
	document.documentElement.style.setProperty('--char-color', ASSETS.charColor);
	if (GALR && typeof GALR.init === 'function') { /* 外部渲染器初始化（Live2D 约定；异常不拦启动） */
		try { GALR.init(el('stage')); } catch (e) {}
	}
	bindEvents();
	fetchState();
}

boot();
