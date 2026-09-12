# -*- coding: utf-8 -*-
"""packs —— robot-pack-v1 内容包：发现 / 校验 / 索引中枢（2026-09-10 Phase 3）。

包 = **内容与素材的发布单元**（人设卡、台词、语音包、立绘/差分素材、道具目录、
世界观/世界书、情绪基调……以数据形态随包发布）；**代码级扩展不走包**——
工具能力走 ToolCard 契约（agent/registry.py，铁律#1），包内 python 仅作 tool 包
装载通道且默认关闭（PACKS_ENABLE_PY，见 load_tools 安全声明）。

双包根（同名包 data 优先——用户安装覆盖随仓示例）：
    随仓示例：qq-bot/packs/            （tracked，随发行版带出的活示例/夹具）
    用户安装：E:/robot/data/packs/     （gitignored；放进来即用，Path 风格同 core/paths.DATA_ROOT）

清单 pack.json（no-BOM UTF-8 JSON）：
    {"spec":"robot-pack-v1", "name":"author.name", "version":"1.0.0",
     "type":"card|voice|item|emotion|world|tool", "title":"", "author":"",
     "license":"", "compat":{"core_api":">=1.0"}, ...按 type 的附加段}

校验纪律（坏包跳过 + loguru warning 留原因，绝不炸发现流程）：
    - spec 必须等于 "robot-pack-v1"；name 点分小写；version 语义版本三段；
    - 声明的相对路径 resolve 后必须仍位于包目录内（路径穿越拒绝）；
    - 单文件读取上限 MAX_FILE_BYTES=8MB（防呆）；
    - 未知 type 跳过不炸。

缓存：双根目录 stat（路径+mtime）为键，进程内缓存发现与索引结果；reload() 强制失效。
数据源与合并序（本机行为不变的根）：包打底 → data/quotes.json 覆盖（台词 IP 外置位）。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from core.paths import DATA_ROOT, QQBOT_ROOT

# 兼容承诺：本常量随 PLUGIN_SDK 发布；包 compat.core_api 声明其适配的核心接口版本（v1 仅记录不强制）。
CORE_API_VERSION = "1.0.0"
SPEC = "robot-pack-v1"
VALID_TYPES = ("card", "voice", "item", "emotion", "world", "tool", "gal")
# gal（GAL 舞台包，2026-09-12 T14）：把"舞台长什么样"从人设卡与皮肤里独立出来——
#   卡包给**词**（sprite_expressions / sprites/manifest.json 的 map）
#   舞台包给**图**（bg/ 背景 + sprites/diff/ 差分像素）
#   皮肤包给**壳**（配色/控件/布局）
# 三者变化轴正交：换角色不必换背景，换背景不必换皮肤。
MAX_FILE_BYTES = 8 * 1024 * 1024  # 单文件读取上限（防呆：素材与数据文件都不该这么大）

# 双包根：用户安装根在前（同名包优先=本地覆盖）
USER_PACKS_DIR = DATA_ROOT / "packs"
BUILTIN_PACKS_DIR = QQBOT_ROOT / "packs"
# 台词 IP 外置位（gal.js 台词迁出，Phase 3）：data/ gitignored——发行版天然无该文本
QUOTES_FILE = DATA_ROOT / "quotes.json"

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*(\.[a-z0-9][a-z0-9_-]*)+$")  # 点分小写（author.name）
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")  # 语义版本三段
_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")  # 卡键/音色键（进 URL 与文件名拼装，禁路径符与空白）

_TRUTHY = {"1", "true", "yes", "on"}


# ============ Pack：轻量包对象（manifest + 目录 + 校验过的路径解析器） ============
@dataclass(frozen=True)
class Pack:
    """一个已通过校验的内容包。resolve/read_json 是仅有的两个文件访问口（穿越/超限在此拦）。"""

    name: str
    manifest: dict = field(repr=False, compare=False)
    dir: Path
    root: str  # "user" | "builtin"

    @property
    def type(self) -> str:
        return str(self.manifest.get("type") or "")

    @property
    def card_key(self) -> str:
        """卡键（type=card 包 manifest["card"]["key"]；非卡包/未声明返回空串）。"""
        card = self.manifest.get("card")
        if isinstance(card, dict):
            k = str(card.get("key") or "").strip()
            if _KEY_RE.match(k):
                return k
        return ""

    def resolve(self, rel: str) -> Path:
        """包内相对路径 → 绝对路径（**校验过的解析器**）：resolve 后必须仍位于包目录内，
        否则抛 ValueError（路径穿越拒绝）。调用方负责再判 is_file()。"""
        rel = str(rel or "").strip().replace("\\", "/")
        if not rel or rel.startswith("/"):
            raise ValueError(f"bad pack path: {rel!r}")
        root = self.dir.resolve()
        p = (root / rel).resolve()
        if p != root and root not in p.parents:
            raise ValueError(f"path escapes pack dir: {rel!r} [{self.name}]")
        return p

    def read_json(self, rel: str):
        """读包内 JSON 文件（utf-8-sig 防 BOM）：超 MAX_FILE_BYTES / 解析失败 / 穿越一律
        warning + 返回 None（调用方按缺省处理）。"""
        try:
            p = self.resolve(rel)
        except ValueError as e:
            logger.warning("pack file rejected: {}", e)
            return None
        try:
            if p.stat().st_size > MAX_FILE_BYTES:
                logger.warning("pack file too large (>{}, cap {}): {} [{}]",
                               p.stat().st_size, MAX_FILE_BYTES, p, self.name)
                return None
            return json.loads(p.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as e:
            logger.warning("pack file unreadable: {} [{}] [{}]", p, type(e).__name__, self.name)
            return None


# ============ 发现与校验 ============
_CACHE: dict = {"roots": None, "packs": None, "ikey": None, "index": {}}
# roots/packs=发现缓存（键=双根目录 stat）；ikey/index=索引视图缓存（键=双根+data/quotes.json stat）——
# 两键形状不同刻意分开：索引键变化不得打掉发现缓存，反之亦然。


def _dir_key(d: Path):
    try:
        return (str(d), d.stat().st_mtime)
    except OSError:
        return (str(d), None)


def _roots_key() -> tuple:
    """缓存键：双根目录 stat + data/quotes.json stat（内容索引的覆盖源也要感知改动）。"""
    return (_dir_key(USER_PACKS_DIR), _dir_key(BUILTIN_PACKS_DIR), _dir_key(QUOTES_FILE))


def _load_manifest(pk_dir: Path) -> dict | None:
    """读 pack.json（校验过的单一入口：大小上限/BOM 容错/JSON 合法性）。失败返回 None。"""
    f = pk_dir / "pack.json"
    try:
        if f.stat().st_size > MAX_FILE_BYTES:
            logger.warning("pack manifest too large: {} [{}]", f, pk_dir.name)
            return None
        m = json.loads(f.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        logger.warning("pack manifest unreadable: {} [{}] ({})", f, type(e).__name__, e)
        return None
    return m if isinstance(m, dict) else None


def _load_pack(pk_dir: Path, root: str) -> Pack | None:
    """解析并校验一个包目录；任何不合法 → warning 留原因 + None（坏包跳过不炸）。"""
    m = _load_manifest(pk_dir)
    if m is None:
        if (pk_dir / "pack.json").is_file():
            logger.warning("pack skipped (manifest unusable): {} [{}]", pk_dir, root)
        return None
    if str(m.get("spec") or "") != SPEC:
        logger.warning("pack skipped (spec != {}): {} [{}]", SPEC, pk_dir, m.get("spec"))
        return None
    name = str(m.get("name") or "").strip()
    if not _NAME_RE.match(name):
        logger.warning("pack skipped (bad name {!r}, want dot.lower like author.name): {}",
                       name, pk_dir)
        return None
    ver = str(m.get("version") or "").strip()
    if not _VERSION_RE.match(ver):
        logger.warning("pack skipped (bad version {!r}, want semver x.y.z): {} [{}]",
                       ver, pk_dir, name)
        return None
    ptype = str(m.get("type") or "").strip().lower()
    if ptype not in VALID_TYPES:
        logger.warning("pack skipped (unknown type {!r}): {}", m.get("type"), pk_dir)
        return None
    pk = Pack(name=name, manifest=m, dir=pk_dir, root=root)
    if ptype == "tool":
        # 声明的相对路径（tools[].entry）必须留在包内——穿越包整体拒载（代码包从严）
        for t in (m.get("tools") or []):
            entry = t.get("entry") if isinstance(t, dict) else None
            try:
                pk.resolve(str(entry or ""))
            except ValueError as e:
                logger.warning("pack skipped (tool entry escapes dir): {} ({})", name, e)
                return None
    return pk


def discover() -> list[Pack]:
    """扫描双包根 → 解析校验 → Pack 列表（带目录 stat 键缓存；坏包跳过并 warning 留原因）。
    同名包 data 优先：用户安装根先扫，随仓同名示例被覆盖（本地覆盖包）。"""
    key = (_dir_key(USER_PACKS_DIR), _dir_key(BUILTIN_PACKS_DIR))
    if _CACHE["roots"] == key and _CACHE["packs"] is not None:
        return list(_CACHE["packs"])
    packs: dict[str, Pack] = {}
    for root, root_dir in (("user", USER_PACKS_DIR), ("builtin", BUILTIN_PACKS_DIR)):
        try:
            children = sorted(d for d in root_dir.iterdir() if d.is_dir())
        except OSError:
            continue  # 包根不存在=常态（未安装任何包）
        for d in children:
            if not (d / "pack.json").is_file():
                continue
            pk = _load_pack(d, root)
            if pk is None:
                continue
            if pk.name in packs:
                logger.info("pack shadowed by {} root: {} [{}]", packs[pk.name].root, pk.name, root)
                continue
            packs[pk.name] = pk
    # 顺序纪律（2026-09-12 修）：**用户根在前、随仓根在后**；同根内按名字排序（保证确定性）。
    # ★ 原实现是 `sorted(packs)` —— 按包名**全局**重排，把"用户优先"排没了。后果很具体：
    #   `example.stage`（随仓示例）在字典序上排 `local.stage`（用户自装的舞台包）**前面**，
    #   而 stage_index() 的 default_bg/bg_alt 是"先到先得" → **用户自己的舞台背景被示例占位图盖住**。
    #   同名遮蔽（上面那个 packs 字典）本来是对的，坏的是循环之后这一次排序。
    #   顺序敏感的消费方：plugins/webgal 的 sprite_dirs()（差分图同词取先到的）、stage_index()。
    ordered = ([packs[k] for k in sorted(packs) if packs[k].root == "user"]
               + [packs[k] for k in sorted(packs) if packs[k].root != "user"])
    _CACHE["roots"] = key
    _CACHE["packs"] = ordered
    _CACHE["ikey"] = None  # 包集合变了：全部索引视图一并失效
    _CACHE["index"] = {}
    return list(ordered)


def reload() -> None:
    """强制失效发现与全部索引缓存（下次调用重扫磁盘；mtime 键对包内文件编辑不敏感，开发期用它）。"""
    _CACHE["roots"] = None
    _CACHE["packs"] = None
    _CACHE["ikey"] = None
    _CACHE["index"] = {}


def get(name: str) -> Pack | None:
    name = str(name or "").strip()
    for pk in discover():
        if pk.name == name:
            return pk
    return None


def by_type(t: str) -> list[Pack]:
    t = str(t or "").strip().lower()
    return [pk for pk in discover() if pk.type == t]


def find_card(key: str) -> Pack | None:
    """按卡键（manifest.card.key）找卡包（persona 回落与差分菜单回落共用）。"""
    key = str(key or "").strip()
    if not key:
        return None
    for pk in by_type("card"):
        if pk.card_key == key:
            return pk
    return None


def _index(name: str, build):
    """索引视图缓存壳：键=双根+quotes 文件 stat；build() 只在缓存失效时执行一次。"""
    key = _roots_key()
    if _CACHE["ikey"] == key and name in _CACHE["index"]:
        return _CACHE["index"][name]
    dkey = (_dir_key(USER_PACKS_DIR), _dir_key(BUILTIN_PACKS_DIR))
    if _CACHE["roots"] != dkey or _CACHE["packs"] is None:
        discover()  # 包集合与键同步后再构建
    val = build()
    _CACHE["ikey"] = key
    _CACHE["index"][name] = val
    return val


# ============ type=card：GAL 页内容索引（台词 + 差分映射） ============
def _clean_quotes(raw) -> list[dict]:
    """quotes.json 的 {"quotes":[{q,by}]} → 干净条目列表（非 dict/无 q 的条目丢弃）。"""
    if not isinstance(raw, dict):
        return []
    out = []
    for it in (raw.get("quotes") or []):
        if isinstance(it, dict) and str(it.get("q") or "").strip():
            out.append({"q": str(it["q"]).strip(), "by": str(it.get("by") or "").strip()})
    return out


def content_index() -> dict:
    """GAL 页内容索引（/gal/content.json 载荷）：
        {"quotesByCard": {卡键: [{q,by}]}, "quoteFallback": [{q,by}],
         "spriteMap": {卡键: {基调词: 素材词}}, "stage": {…见 stage_index()}}
    合并序（**包打底 → data/quotes.json 覆盖**）：随仓/用户包的 quotes.json 先进，
    data/quotes.json（gal.js 迁出的 IP 文本位，用户手改层）按键整键覆盖；fallback 只来自 data 层。
    spriteMap 来自卡包 sprites/manifest.json 的 map → **舞台包同名键覆盖** → 额外情绪 mod 的 sprite_word。
    personas 卡自带 sprite_expressions 不经此（那是 bot 侧差分菜单，见 brain 注入点）。
    stage 键为 2026-09-12 T14 新增——**既有三键语义一字未改**，纯增量。"""
    return _index("content", _build_content)


def _build_content() -> dict:
    by_card: dict[str, list[dict]] = {}
    sprite_map: dict[str, dict[str, str]] = {}
    for pk in by_type("card"):
        k = pk.card_key
        if not k:
            continue
        qs = _clean_quotes(pk.read_json("quotes.json"))
        if qs:
            by_card[k] = qs
        man = pk.read_json("sprites/manifest.json")
        if isinstance(man, dict):
            m = man.get("map")
            if isinstance(m, dict) and m:
                sprite_map[k] = {str(a): str(b) for a, b in m.items() if str(a) and str(b)}
    # 舞台包（type=gal）的 map **覆盖**卡包同名键（T14）：舞台包既能供图，也能纠正"词→素材词"。
    # 作用域靠 pack.json 的 card.key（与卡包同一机制）；不声明 card.key 的舞台包其 map 不生效——
    # 否则"这份 map 属于哪张卡"就成了歧义，宁可不要。
    for pk in by_type("gal"):
        k = pk.card_key
        if not k:
            continue
        man = pk.read_json("sprites/manifest.json")
        if isinstance(man, dict):
            m = man.get("map")
            if isinstance(m, dict) and m:
                clean = {str(a): str(b) for a, b in m.items() if str(a) and str(b)}
                if clean:
                    sprite_map.setdefault(k, {}).update(clean)
    for t in tones_index():
        sw = str(t.get("sprite_word") or "").strip()
        k = str(t.get("card_key") or "").strip()
        w = str(t.get("word") or "").strip()
        if sw and k and w:
            sprite_map.setdefault(k, {})[w] = sw
    # data/quotes.json 覆盖层（台词 IP 外置位；缺失=发行版常态，包内容照常生效）
    try:
        dq = json.loads(QUOTES_FILE.read_text(encoding="utf-8-sig")) if QUOTES_FILE.is_file() else {}
    except (OSError, ValueError) as e:
        logger.warning("data quotes unreadable, pack quotes only: {} [{}]", QUOTES_FILE, type(e).__name__)
        dq = {}
    fallback: list[dict] = []
    if isinstance(dq, dict):
        dbc = dq.get("byCard")
        if isinstance(dbc, dict):
            for k, v in dbc.items():
                if isinstance(k, str) and k and isinstance(v, list) and v:
                    by_card[k] = v  # 整键覆盖（用户手改优先于包）
        fb = dq.get("fallback")
        if isinstance(fb, list):
            fallback = [it for it in fb if isinstance(it, dict) and str(it.get("q") or "").strip()]
    return {"quotesByCard": by_card, "quoteFallback": fallback,
            "spriteMap": sprite_map, "stage": stage_index()}


# ============ type=gal：GAL 舞台索引（背景 + 场景映射，2026-09-12 T14） ============
GAL_IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def stage_index() -> dict:
    """舞台索引（进 /gal/content.json 的 stage 键）：
        {"default_bg": URL, "bg_alt": URL, "bg_by_scene": {场景: URL}, "roots": [{pack, dir}]}

    **URL 由本函数解析并核实文件存在**——背景 id 对应的文件不存在就**不出现在结果里**
    （前端因此走它原有的 CSS 渐变兜底，而不是拿到一个必然 404 的地址）。
    服务端路由 `/gal/stage/<包名>/<包内相对路径>` 由 plugins/webgal 提供（与这里同一套校验）。

    多包合并纪律：**先到先得**（`setdefault`）——discover() 已保证用户安装根在前，
    所以"用户舞台包覆盖随仓示例"是自然结果，无需特判。
    """
    default_bg = ""
    bg_alt = ""
    by_scene: dict[str, str] = {}
    roots: list[dict] = []
    for pk in by_type("gal"):
        st = pk.manifest.get("stage")
        roots.append({"pack": pk.name, "dir": str(pk.dir / "bg")})
        if not isinstance(st, dict):
            continue
        if not default_bg:
            default_bg = _stage_bg_url(pk, str(st.get("default_bg") or "").strip())
        if not bg_alt:
            bg_alt = _stage_bg_url(pk, str(st.get("bg_alt") or "").strip())
        m = st.get("bg_by_scene")
        if isinstance(m, dict):
            for scene, bg_id in m.items():
                s = str(scene).strip()
                if not s or s in by_scene:
                    continue
                u = _stage_bg_url(pk, str(bg_id).strip())
                if u:
                    by_scene[s] = u
    return {"default_bg": default_bg, "bg_alt": bg_alt, "bg_by_scene": by_scene, "roots": roots}


def _stage_bg_url(pk: Pack, bg_id: str) -> str:
    """背景 id → 可访问 URL；文件不存在 / id 非法 / 路径穿越 → 空串（调用方按"没有"处理）。

    扩展名按 GAL_IMG_EXT 逐个探测——作者不必在清单里写扩展名，也就不会写错。"""
    if not bg_id or "/" in bg_id or "\\" in bg_id or bg_id.startswith("."):
        return ""
    for ext in GAL_IMG_EXT:
        try:
            p = pk.resolve(f"bg/{bg_id}{ext}")
        except ValueError:
            return ""
        if p.is_file():
            return f"/gal/stage/{pk.name}/bg/{bg_id}{ext}"
    return ""


def stage_file(pack_name: str, rel: str):
    """按 URL 取舞台文件（webgal 路由用）：命中返回 (绝对路径, 包)，否则 (None, None)。

    三重校验：① 包必须存在且 `type=gal`（**不做通配**——别让人拿这个路由读任意包）；
    ② rel 非空且不以 `/` 开头；③ `Pack.resolve()` 已拦路径穿越。
    """
    pk = get(str(pack_name or "").strip())
    if pk is None or pk.type != "gal":
        return None, None
    rel = str(rel or "").strip().replace("\\", "/")
    if not rel or rel.startswith("/"):
        return None, None
    try:
        p = pk.resolve(rel)
    except ValueError as e:
        logger.warning("stage file rejected: {} [{}]", e, pk.name)
        return None, None
    if not p.is_file():
        return None, None
    return p, pk


# ============ 语音：card 包 voice.json + type=voice 包 ============
_VOICE_ABS_FIELDS = ("gpt", "sovits")  # 必须绝对路径的字段（权重属 tools/，gitignored）
_VOICE_ABS_OPT = ("ref_dir",)


def voice_index() -> dict[str, dict]:
    """音色注册表合并视图（对齐 plugins/voice VOICES 条目结构）：
        {key: {name, lang, personas, gpt, sovits, ref_dir?, refs?, emo_refs?}}
    card 包读 voice.json（键取 voice.json.key > manifest.card.key > 包名末段）；
    type=voice 包同构。路径字段必须**绝对路径**，相对路径条目跳过+warning
    （GPT-SoVITS 权重属 tools/，gitignored——公开发行不带权重但带包格式，见 PLUGIN_SDK）。
    合并纪律在消费方：plugins/voice 以 setdefault 合并——内置表永远优先。"""
    return _index("voices", _build_voices)


def _build_voices() -> dict[str, dict]:
    out: dict[str, dict] = {}
    sources: list[tuple[Pack, dict]] = []
    for pk in by_type("card"):
        v = pk.read_json("voice.json")
        if isinstance(v, dict):
            sources.append((pk, v))
    for pk in by_type("voice"):
        v = pk.read_json("voice.json")
        if isinstance(v, dict):
            sources.append((pk, v))
    for pk, v in sources:
        key = str(v.get("key") or "").strip() or pk.card_key or pk.name.rsplit(".", 1)[-1]
        if not _KEY_RE.match(key):
            logger.warning("voice pack entry skipped (bad key): {} [{}]", key, pk.name)
            continue
        entry: dict = {
            "name": str(v.get("name") or "").strip(),
            "lang": str(v.get("lang") or "zh").strip().lower(),
            "personas": [str(p).strip() for p in (v.get("personas") or []) if str(p).strip()],
        }
        if not entry["name"] or not entry["personas"]:
            logger.warning("voice pack entry skipped (need name+personas): {} [{}]", key, pk.name)
            continue
        bad = False
        for f in _VOICE_ABS_FIELDS:
            path = str(v.get(f) or "").strip()
            if not path or not os.path.isabs(path):
                logger.warning("voice pack entry skipped ({} must be absolute): {} [{}]", f, key, pk.name)
                bad = True
                break
            entry[f] = path
        if bad:
            continue
        for f in _VOICE_ABS_OPT:
            path = str(v.get(f) or "").strip()
            if path:
                if not os.path.isabs(path):
                    logger.warning("voice pack entry skipped ({} must be absolute): {} [{}]", f, key, pk.name)
                    bad = True
                    break
                entry[f] = path
        if bad:
            continue
        if str(v.get("refs") or "").strip():
            entry["refs"] = str(v["refs"]).strip()
        if isinstance(v.get("emo_refs"), dict) and v["emo_refs"]:
            entry["emo_refs"] = {str(a): str(b) for a, b in v["emo_refs"].items() if a and b}
        out[key] = entry
    return out


# ============ 世界观 / 世界书（type=world，酒馆式；可独立包也可内嵌卡包） ============
def world_index() -> dict[str, list[dict]]:
    """{universe: [entries]}——扫描全部包的 world.json（双根合并），条目按 priority 降序稳定排序。
    条目 schema：{"keys":["触发词",...], "text":"", "always":false, "priority":0}
    v1 只消费 always:true 条目（persona.build_system_prompt 注入）；keys 触发匹配是 v2 路线。
    层次定位：world 包是「设定文本」层；data/universe_roles.json（同世界观互认）与
    qq-bot/data/scenes.json（场景词库）是「关系/场景」层——universe 键对齐、互补不重叠。"""
    return _index("world", _build_world)


def _build_world() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for pk in discover():
        w = pk.read_json("world.json")
        if not isinstance(w, dict):
            continue
        uni = str(w.get("universe") or "").strip()
        if not uni:
            logger.warning("world.json skipped (no universe): [{}]", pk.name)
            continue
        entries: list[dict] = []
        for e in (w.get("entries") or []):
            if not isinstance(e, dict):
                continue
            text = str(e.get("text") or "").strip()
            if not text:
                continue
            try:
                prio = int(e.get("priority") or 0)
            except (TypeError, ValueError):
                prio = 0
            entries.append({
                "keys": [str(k).strip() for k in (e.get("keys") or []) if str(k).strip()],
                "text": text,
                "always": bool(e.get("always")),
                "priority": prio,
            })
        if entries:
            entries.sort(key=lambda x: -x["priority"])  # sorted 稳定：同 priority 保持文件序
            out.setdefault(uni, []).extend(entries)
    for uni in out:
        out[uni].sort(key=lambda x: -x["priority"])  # 跨包合并后再稳定排序一次
    return out


# ============ 道具目录（type=item）/ 额外情绪基调（type=emotion）——均可在任意包 manifest 附加段声明 ============
def items_index() -> list[dict]:
    """预置道具目录条目（manifest["items"]：[{"name","label","note","ttl_min"}]，全部包合并）。
    只做形状校验；消毒/label/ttl 严格校验在 core.special.add_item_catalog（消费方）。"""
    return _index("items", _build_items)


def _build_items() -> list[dict]:
    out: list[dict] = []
    for pk in discover():
        items = pk.manifest.get("items")
        if not isinstance(items, list):
            continue
        for it in items:
            if isinstance(it, dict) and str(it.get("name") or "").strip():
                out.append({
                    "name": str(it["name"]).strip(),
                    "label": str(it.get("label") or "").strip(),
                    "note": str(it.get("note") or "").strip(),
                    "ttl_min": it.get("ttl_min"),
                    "pack": pk.name,
                })
    return out


def tones_index() -> list[dict]:
    """额外情绪基调条目（manifest["tones"]：[{"word","sprite_word","voice":{temp,speed,semi}|{}}]）。
    voice 结构对齐 plugins/voice EMO_PARAMS 的 (温度, 语速, 半音) 三元组；
    空 voice=只扩 sprite/文案层。word 校验与内置 9 类优先的合并纪律在消费方（plugins/voice）。"""
    return _index("tones", _build_tones)


def _build_tones() -> list[dict]:
    out: list[dict] = []
    for pk in discover():
        tones = pk.manifest.get("tones")
        if not isinstance(tones, list):
            continue
        for t in tones:
            if not isinstance(t, dict) or not str(t.get("word") or "").strip():
                continue
            voice = t.get("voice") if isinstance(t.get("voice"), dict) else None
            out.append({
                "word": str(t["word"]).strip()[:6],  # 对齐【基调：两三字】口径，钳 6 字防呆
                "sprite_word": str(t.get("sprite_word") or "").strip(),
                "voice": ({k: voice[k] for k in ("temp", "speed", "semi") if k in voice} if voice else {}),
                "card_key": pk.card_key,  # sprite_word 归属卡（content_index.spriteMap 用）
                "pack": pk.name,
            })
    return out


# ============ tool 包装载（type=tool；**默认不执行任何 python**） ============
def py_tools_enabled() -> bool:
    """工具包 python 装载开关：env PACKS_ENABLE_PY 显式开启才为 True（默认关闭）。
    安全边界（诚实声明）：工具包=代码。数据包（card/voice/item/emotion/world）无代码、
    天然无害；代码走 ToolCard 契约（agent/registry.py，铁律#1：工具结果来自受控执行环境），
    装载通道必须显式开 env——发行版默认关闭，普通用户装数据包零风险。"""
    return str(os.environ.get("PACKS_ENABLE_PY") or "").strip().lower() in _TRUTHY


def load_tools() -> list:
    """按清单装载 tool 包的 entry 模块（importlib 按文件路径导入，不注册 sys.modules）。
    默认关闭：PACKS_ENABLE_PY 未显式开启 → 直接返回空列表，**不做任何 import**。
    失败隔离：单包/单模块导入失败 warning 跳过，不影响其他包与主进程。"""
    if not py_tools_enabled():
        return []
    import importlib.util

    mods: list = []
    for pk in by_type("tool"):
        for t in (pk.manifest.get("tools") or []):
            entry = str((t or {}).get("entry") or "")
            if not entry:
                continue
            try:
                p = pk.resolve(entry)  # 二次校验（discover 已验过：从严复验不信任缓存期改动）
            except ValueError as e:
                logger.warning("tool entry rejected: {}", e)
                continue
            if not p.is_file():
                logger.warning("tool entry missing: {} [{}]", p, pk.name)
                continue
            try:
                spec = importlib.util.spec_from_file_location(f"_pack_tool_{pk.name.replace('.', '_')}", p)
                if spec is None or spec.loader is None:
                    raise ValueError("spec unavailable")
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                mods.append(mod)
                logger.info("pack tool loaded: {} [{}]", entry, pk.name)
            except Exception as e:  # noqa: BLE001
                logger.warning("pack tool import failed: {} [{}] {} [{}]", entry, pk.name, e, type(e).__name__)
    return mods
