# [DEV-ONLY] 开发/诊断工具——不随核心发行版与 SDK 分发，仅供本机排障。
# -*- coding: utf-8 -*-
"""audit_consistency —— 一致性审计：专抓"同一事实有多个来源"这一类 bug。

为什么要有这个脚本：本项目的真 bug 几乎全是这一类，而不是语法/逻辑错误——
  · 情绪词表两套（24 类 vs 9 类，只有「无语」相交）
  · 差分词表"两处同源须同步"（前端 bust4w 段 vs 后端 BUST_DIR）
  · 引擎参数三源（start.ps1 / Launcher.ps1 / debug.py），曾出现"同一台机器两条启动路径行为不同"
  · `.env.example` 里留着代码不读的 STICKER_ENABLED（盲审 P3-8）
这类问题 compileall 与 smoke 都盖不到，只能靠"把多个来源摆在一起比"。

审计项（只报告，不改动任何文件）：
  A. env 漂移：代码读的键 ⟷ .env.example ⟷ .env 实际
  B. 硬编码绝对路径（运行时代码；tests/tools 除外）
  C. 绕过 core.atomics 的写盘
  D. AST 精确检查：裸 except / 无注释的 `except: pass`（自动分级 清理类·观测类·待查）/
     可变默认参数 / async 内阻塞调用
  E. 多源常量：端口 / 模型文件 / 引擎可执行 / 差分目录 —— 分级（定义·注释·文档串·已声明·
     另会话产物·散落）；只有"Python 侧散落"与"多文件重复定义"才判 ❌，PS 侧跨语言只列不计
  F. BOM 漂移（PS 脚本丢 BOM = PS5.1 直接语法崩溃，2026-09-12 实际踩过）
  G. 依赖矩阵一致性（launcher/deps.json ⟷ docs/部署指南.md 的生成块）
  H. 真·静默写失败（`try: <写盘> except: pass`；白名单外应为 0）
  I. 数据根推导：`parents[N] / "data"` 解析到哪个根（规范根=状态 / 内容根=随仓内容），
     并用文件系统证据抓"同名文件两处都有"的真分裂
  J. 启动器引用完整性：`FindName("X")`/`BindClick "X"`/`Set-Lamp` 引用的控件名 ⟷ XAML `x:Name`
     （拼错名 = `$null` + 守卫 = 静默什么都不做）；另查僵尸名与 `-Mode` 用法串一致性
  K. 文档契约：`docs/接口文档.md` 声明的 HTTP/WS 路由与 `/gal/content.json` 顶层键 ⟷ 代码实况
     （文档是给第三方 MOD 作者的合同——"文档说有"必须能机器验证）
  L. 文档索引一致性：`docs/README.md` 登记的文件 ⟷ `docs/` 实际文件（死链 / 未登记 / 自称份数）
  M. 控制字符扫描：源码里的 TAB 与 C0 控制字符（语法合法但内容会悄悄错；生成物那半边由
     `build/build_release.ps1` 的 SDK 自检覆盖）
  N. 超时与任务引用：`subprocess` 调用缺 `timeout=`（卡住即挂死）+ 裸 `asyncio.create_task`
     （返回值不保存 → 只持弱引用 → 可能被 GC 静默吞掉；规范用 `core.bgtasks.spawn`）
     + `open()` 未包 `with`（句柄泄漏 → Windows 上锁文件）
  O. 本机身份外泄：主人 QQ 号 / 昵称出现在随仓文本或 **exe 二进制**里（ps2exe 把脚本含注释
     一起嵌进 exe → 注释里的"举例号"真的会随发行物出门）。值从 `.env` **运行时派生**、报告只印掩码

用法：
    cd qq-bot && .venv\\Scripts\\python.exe tools\\dev\\audit_consistency.py
    ... --json     机器可读输出
"""
from __future__ import annotations

import ast
import fnmatch
import io
import json
import re
import sys
import tokenize
from pathlib import Path

QQBOT = Path(__file__).resolve().parents[2]
ROBOT = QQBOT.parent
RUNTIME_DIRS = ("core", "agent", "plugins", "harness")
RUNTIME_ROOT_FILES = ("bot.py",)

# ---------------------------------------------------------------- 采集
_ENV_PATTERNS = (
    re.compile(r"""os\.environ(?:\.get)?\s*[\[(]\s*["']([A-Z][A-Z0-9_]{2,})["']"""),
    re.compile(r"""os\.getenv\s*\(\s*["']([A-Z][A-Z0-9_]{2,})["']"""),
    re.compile(r"""environ\.get\s*\(\s*["']([A-Z][A-Z0-9_]{2,})["']"""),
)
# nonebot 自带的配置键：由框架自身消费，不算死配置
_NONEBOT_BUILTIN = {"HOST", "PORT", "SUPERUSERS", "NICKNAME", "LOG_LEVEL",
                    "COMMAND_START", "DRIVER", "API_ROOTS", "FASTAPI_RELOAD"}


def _iter_runtime_py():
    for d in RUNTIME_DIRS:
        for p in sorted((QQBOT / d).rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            yield p
    for f in RUNTIME_ROOT_FILES:
        p = QQBOT / f
        if p.is_file():
            yield p


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return ""


def audit_env() -> dict:
    """env 漂移三向对照。

    ★ 关键修正（第一版假阳性教训）：`.env` 的键**大多不是**用 os.environ 读的——
    nonebot 把 .env 收进 pydantic 配置后，代码读的是**小写属性** `config.llm_base_url`。
    只扫 os.environ 会把 LLM_*/VTS_*/STICKER_ENABLED 等 19 个活配置误报成"死配置"。
    故本审计对每个键同时找两种消费方式：os.environ（大写）与 config.<小写属性>。
    """
    env_used: dict[str, list[str]] = {}
    all_src: list[tuple[Path, str]] = []
    for p in _iter_runtime_py():
        txt = _read(p)
        all_src.append((p, txt))
        for i, line in enumerate(txt.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for pat in _ENV_PATTERNS:
                for m in pat.finditer(line):
                    env_used.setdefault(m.group(1), []).append(f"{p.relative_to(ROBOT)}:{i}")

    def keys_of(path: Path) -> set[str]:
        out = set()
        for line in _read(path).splitlines():
            m = re.match(r"\s*#?\s*([A-Z][A-Z0-9_]{2,})\s*=", line)
            if m:
                out.add(m.group(1))
        return out

    def config_sites(key: str) -> list[str]:
        """该键是否被消费（三种方式之一即可）：

        ① nonebot 配置属性 `.llm_base_url` / `getattr(config, "llm_base_url")`
        ② **带引号的大写字面量** `_env_flag("LIVE_DANMAKU_ENABLED")`——辅助函数读法
        ③ **动态模板键** `_get(f"llm_model_{purpose}")`——静态无法枚举，只能按前缀认领

        ★ 三种都是实测踩出来的假阳性来源：只扫 os.environ 会把 19 个活配置报成"死配置"，
        而 ③ 会让 LLM_MODEL_FICTION 被误判成死配置（core/llm.py:119 拼的是 f"llm_model_{purpose}"）。
        宁可报"无法静态确认"，也不能报"死配置"骗人。
        """
        low = key.lower()
        pats = (re.compile(rf"\.{low}\b"), re.compile(rf"""config\s*,\s*["']{low}["']"""),
                re.compile(rf"""["']{low}["']"""), re.compile(rf"""["']{key}["']"""))
        hits = []
        for p, txt in all_src:
            for i, line in enumerate(txt.splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                if any(pt.search(line) for pt in pats):
                    hits.append(f"{p.relative_to(ROBOT)}:{i}")
        return hits

    def dynamic_template_site(key: str) -> str:
        """该键是否可能由动态模板键消费（如 llm_model_fiction ← f"llm_model_{purpose}"）。"""
        prefix = key.lower().rsplit("_", 1)[0] + "_"
        pat = re.compile(rf"""{re.escape(prefix)}\{{""")
        for p, txt in all_src:
            for i, line in enumerate(txt.splitlines(), 1):
                if pat.search(line):
                    return f"{p.relative_to(ROBOT)}:{i}"
        return ""

    example = keys_of(QQBOT / ".env.example")
    actual = keys_of(QQBOT / ".env")
    example_or_env = example | actual

    consumed: dict[str, dict] = {}
    for k in sorted(example_or_env):
        cs = config_sites(k)
        dyn = dynamic_template_site(k)
        consumed[k] = {"env_sites": env_used.get(k, []), "config_sites": cs,
                       "dynamic": dyn,
                       "consumed": bool(env_used.get(k) or cs or dyn)}
    return {
        "env_used": {k: v for k, v in sorted(env_used.items())},
        "example_keys": sorted(example),
        "env_keys": sorted(actual),
        "consumed": consumed,
        "missing_in_example": sorted(k for k in env_used if k not in example),
        "dead_in_example": sorted(k for k in example
                                  if not consumed.get(k, {}).get("consumed")
                                  and k not in _NONEBOT_BUILTIN),
        "in_env_not_consumed": sorted(k for k in actual
                                      if not consumed.get(k, {}).get("consumed")
                                      and k not in _NONEBOT_BUILTIN),
    }


# ---------------------------------------------------------------- AST 审计（精确，不用正则）
def audit_ast() -> dict:
    """用 ast 做四类精确检查——正则做这些必然噪声爆炸（第一版 D 项 427 条假阳性）。

    1. 裸 except（`except:`）——会把 KeyboardInterrupt/SystemExit 一起吞掉
    2. except 体只有 pass 且无注释——静默失败，出事时没有线索
    3. 可变默认参数（def f(x=[])）——跨调用共享可变状态，经典隐藏 bug
    4. async 函数里的阻塞调用（time.sleep / requests. / urllib）——卡死整个事件循环
    """
    import ast

    bare, empty, mutable, blocking = [], [], [], []
    for p in _iter_runtime_py():
        rel = str(p.relative_to(ROBOT))
        src = _read(p)
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            bare.append({"file": rel, "line": e.lineno or 0, "kind": "语法错误", "src": str(e.msg)})
            continue
        lines = src.splitlines()

        def has_comment(lineno: int) -> bool:
            head = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
            if "#" in head.split(":", 1)[-1]:
                return True
            nxt = lines[lineno] if 0 < lineno < len(lines) else ""
            return "#" in nxt

        for node in ast.walk(tree):
            if isinstance(node, _TRY_TYPES):
                # 按 Try 迭代（而不是按 ExceptHandler）：分级要看 **try 体** 干了什么，
                # handler 自己只有一个 pass，看不出任何东西——这是分级器的第一作者 bug。
                for h in node.handlers:
                    body = [b for b in h.body if not (isinstance(b, ast.Expr)
                                                      and isinstance(b.value, ast.Constant))]
                    if h.type is None:
                        bare.append({"file": rel, "line": h.lineno, "kind": "裸 except",
                                     "src": lines[h.lineno - 1].strip()[:90]})
                    if len(body) == 1 and isinstance(body[0], ast.Pass) and not has_comment(h.lineno):
                        empty.append({"file": rel, "line": h.lineno,
                                      "kind": _classify_pass_handler(node.body),
                                      "src": lines[h.lineno - 1].strip()[:90]})
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for d in node.args.defaults + [x for x in node.args.kw_defaults if x]:
                    if isinstance(d, (ast.List, ast.Dict, ast.Set)):
                        mutable.append({"file": rel, "line": node.lineno, "kind": "可变默认参数",
                                        "src": f"{node.name}()"})
                if isinstance(node, ast.AsyncFunctionDef):
                    for sub in ast.walk(node):
                        if not isinstance(sub, ast.Call):
                            continue
                        f = sub.func
                        name = ""
                        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                            name = f"{f.value.id}.{f.attr}"
                        elif isinstance(f, ast.Name):
                            name = f.id
                        if name in ("time.sleep", "requests.get", "requests.post",
                                    "urllib.request.urlopen", "subprocess.run",
                                    "subprocess.check_output"):
                            blocking.append({"file": rel, "line": sub.lineno,
                                             "kind": f"async 内阻塞 {name}",
                                             "src": lines[sub.lineno - 1].strip()[:90]})
    # D 项分开报：机械可证无害的（清理/观测）不该盖住真信号；另会话产物另列。
    ep: dict[str, list] = {"清理类": [], "观测类": [], "待查": [], "另会话产物": []}
    for h in empty:
        foreign = h["file"].replace("\\", "/").startswith("qq-bot/plugins/voice/")
        ep["另会话产物" if foreign else h["kind"]].append(h)
    ep["selftest"] = _pass_classifier_selftest()
    return {"bare_except": bare, "except_pass": ep, "mutable_defaults": mutable,
            "blocking_in_async": blocking}


def audit_bom() -> list[dict]:
    """BOM 漂移：与 git HEAD 比对每个追踪文件的前三字节。

    ★ 为什么单列一项：这个缺陷**已经咬过两次**——
      ① `launcher/Launcher.ps1` 编辑后丢 BOM → PS 5.1 报 115 个假语法错误；
      ② `build/build_release.ps1` 编辑后丢 BOM → PS 5.1 解析失败（而脚本自己的用法说明
         就叫用户用 `powershell -File` 调用，等于构建在文档路径上直接挂掉）。
    根因：PS 5.1 对**无 BOM 的 UTF-8 中文脚本**会按 ANSI 解码，产生结构性误判。
    Python 文件丢 BOM 无害（Py3 默认 UTF-8），但脚本文件必须保持 BOM。
    本项对**全部追踪文件**比对，比"记得手工检查"可靠。
    """
    import subprocess as sp

    root = Path(__file__).resolve().parents[3]
    if not (root / ".git").exists():
        return [{"file": "(跳过)", "line": 0, "kind": "非 git 仓库", "src": str(root)}]
    try:
        listed = sp.run(["git", "ls-files"], cwd=root, capture_output=True, text=True,
                        encoding="utf-8", timeout=120).stdout
    except (OSError, sp.SubprocessError) as e:
        return [{"file": "(跳过)", "line": 0, "kind": "git 调用失败", "src": type(e).__name__}]

    out = []
    bom = b"\xef\xbb\xbf"
    for rel in (x.strip() for x in listed.split("\n") if x.strip()):
        p = root / rel
        if not p.is_file():
            continue
        try:
            r = sp.run(["git", "show", f"HEAD:{rel}"], cwd=root,
                       capture_output=True, timeout=60)
            head, cur = r.stdout[:3], p.read_bytes()[:3]
        except (OSError, sp.SubprocessError):
            continue
        if r.returncode != 0:
            # **新入库文件在 HEAD 里没有基准**（未 commit，只 add 过）：那时"有没有 BOM"
            # 无从比对——报成"漂移"是假警报。2026-09-12 实测：`git add launcher\build_exe.ps1`
            # 之后本项立刻报了一条"BOM 新增（HEAD 无）"，而那个文件本来就该带 BOM。
            # 新文件由 git status 负责展示，这里只需不误报。
            continue
        if (head == bom) != (cur == bom):
            # 标签按**后果**说准确话：`.ps1` 丢 BOM 是致命的（PS5.1 按 GBK 读 → 假语法错误一片），
            # 其它文件丢 BOM 只是与 HEAD 的约定漂移（2026-09-12 实际抓到 launcher/README.md 这种，
            # 若标签一律写"PS 脚本会挂"，读者会以为工具在瞎报——工具一被当成瞎报就没人看了）。
            _fatal = rel.lower().endswith((".ps1", ".psm1"))
            if head == bom:
                _kind = "BOM 丢失（PS5.1 会按 GBK 读→假语法错误）" if _fatal else "BOM 丢失（非 PS：与 HEAD 的约定漂移）"
            else:
                _kind = "BOM 新增（HEAD 无）"
            out.append({
                "file": rel, "line": 0, "kind": _kind,
                "src": f"HEAD={'BOM' if head == bom else 'no-BOM'} 工作区={'BOM' if cur == bom else 'no-BOM'}",
            })
    return out


def audit_deps_doc() -> dict:
    """依赖矩阵一致性：launcher/deps.json（事实源）⟷ docs/部署指南.md 的生成块。

    为什么要查：这个矩阵同时被**文档**（给人看）和**启动器**（S5 做能力检测）消费。
    再加上文档里的表格，就有三处可能在说同一件事——正是本项目反复踩的漂移源。
    故：JSON 是唯一事实源，文档块是生成物，本项负责在两者分叉时报警。
    """
    import subprocess as sp

    deps = ROBOT / "launcher" / "deps.json"
    doc = ROBOT / "docs" / "部署指南.md"
    tool = QQBOT / "tools" / "dev" / "sync_deps_doc.py"
    if not deps.is_file() or not doc.is_file() or not tool.is_file():
        missing = [str(p.relative_to(ROBOT)) for p in (deps, doc, tool) if not p.is_file()]
        return {"ok": False, "detail": f"缺文件: {', '.join(missing)}"}
    try:
        r = sp.run([str(QQBOT / ".venv" / "Scripts" / "python.exe"), str(tool), "--check"],
                   cwd=QQBOT, capture_output=True, text=True, encoding="utf-8", timeout=120)
    except (OSError, sp.SubprocessError) as e:
        return {"ok": False, "detail": f"调用失败 {type(e).__name__}"}
    return {"ok": r.returncode == 0, "detail": (r.stdout or r.stderr or "").strip()[:400]}


_WRITE_METHODS = {"write_text", "write_bytes", "write_json_atomic", "write_text_atomic",
                  "write_bytes_atomic", "unlink", "commit", "executemany"}
# 良性白名单：临时文件清理。失败只意味着"少删一个 tmp"，真实错误在别处已报。
_SILENT_WRITE_BENIGN = {
    ("core/atomics.py", "os.remove"),          # tmp 清理
    ("plugins/voice/__init__.py", "os.remove"),  # 语音临时 wav 清理
}


def audit_silent_write() -> list[dict]:
    """真·静默写失败：`try: <写盘> except: pass` 且**连注释都没有**。

    为什么单独一项：`core/atomics.py` 的模块 docstring 记着本项目的经典事故——
    「一次写坏 = 全部用户状态静默清零」。写失败被无声吞掉正是它的成因。

    ★ 三次迭代才做对，故固化（避免下次从头试错）：
      ① 按"前 9 行出现 write/json/db"判 → 81 条噪声（这些词几乎每处都有）；
      ② 按 try 体里的调用名判 → `str.replace` 被当成 `os.replace`、`list.sort` 之类混入；
      ③ 本版：**限定名精确匹配**（`os.replace` / `os.remove` / `Path.unlink` / SQL 的
         INSERT|UPDATE|DELETE）+ 剥掉纯字符串表达式 + 要求裸 `pass` 且同**行**无注释。
    结果（2026-09-12）：11 处，逐条复核**全部良性**——临时文件清理（删 tmp 失败无害）
    或 `atomics.write_*`（其内部已 logger.warning 并返回 False，不是静默）。
    故本项**当前应为 0 条非白名单项**；一旦出现新条目，很可能是真的吞掉了写失败。
    """
    import ast

    def write_ops(node) -> list[str]:
        out = []
        for n in ast.walk(node):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            if not isinstance(f, ast.Attribute):
                continue
            qual = f"{f.value.id}.{f.attr}" if isinstance(f.value, ast.Name) else f.attr
            if qual in ("os.replace", "os.remove", "shutil.copy"):
                out.append(qual)
            elif f.attr in _WRITE_METHODS:
                out.append(qual)
            elif f.attr == "execute":
                for a in ast.walk(n):
                    if isinstance(a, ast.Constant) and isinstance(a.value, str):
                        if a.value.strip().upper()[:6] in ("INSERT", "UPDATE", "DELETE"):
                            out.append("SQL-WRITE")
                            break
            elif f.attr == "dump":
                out.append("json.dump")
        return sorted(set(out))

    hits = []
    for p in _iter_runtime_py():
        rel = str(p.relative_to(QQBOT)).replace("\\", "/")
        src = _read(p)
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        lines = src.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            for h in node.handlers:
                body = [b for b in h.body if not (isinstance(b, ast.Expr)
                                                  and isinstance(b.value, ast.Constant))]
                if len(body) != 1 or not isinstance(body[0], ast.Pass):
                    continue
                # 有注释说明 = 有意为之，不算静默（注释可能写在 except 行，也可能写在 pass 行）
                _same = lines[h.lineno - 1]
                _body = lines[body[0].lineno - 1] if 0 < body[0].lineno <= len(lines) else ""
                if "#" in _same or "#" in _body:
                    continue
                ops = write_ops(node)
                if not ops:
                    continue
                if any((rel, o) in _SILENT_WRITE_BENIGN for o in ops):
                    continue
                hits.append({"file": rel, "line": h.lineno, "kind": "静默写失败",
                             "src": ",".join(ops)})
    return hits


# 多源常量：这些字符串一旦出现在两个以上来源，就有"改了 A 忘了 B"的风险
# ★ needle 必须**够精确**：曾用 "llama-server" 判"引擎可执行名"，结果把 llama-server.log
#   一类无关串全算成命中（子串假阳性）。工具一开始误报，人就开始了忽略它的输出。
_MULTISOURCE = {
    "端口 11434（引擎）": "11434",
    "端口 11435（embedding）": "11435",
    "端口 8080（bot HTTP）": "8080",
    "模型文件名": "gemma-4-12B-it-heretic-Q4_K_M.gguf",
    "引擎可执行名": "llama-server.exe",
    "差分目录段": "bust4w",
    "立绘目录段": "fullw",
    "embedding 模型名": "Qwen3-Embedding-0.6B-Q8_0.gguf",
}
# PS 侧（start.ps1 等）无法 import Python 常量 —— 只能"对齐值"，故单列，不计 ❌
# （值对齐由 smoke §36 逐参数守护；Python 侧单一来源由本项守护，两者互补）
_MS_PS_FILES = ("start.ps1", "stop.ps1", "launcher/Launcher.ps1", "build/build_release.ps1")
# 声明式豁免：跨语言 / 外部格式契约（前端 JS、PS 约定等 Python 管不到的另一端）。
# 在同处或上一行写本标记 → 计"已声明"并**照旧列出**（不隐藏，只是不再算 ❌）。
_MS_WAIVER = "# audit-ok:"
_MS_BUCKETS = ("定义", "注释", "文档串", "已声明", "另会话产物", "散落")


def _src_marks(src: str) -> tuple[dict[int, tuple[int, int]], set[int]]:
    """返回 (注释跨度, 文档串行号集)。

    注释用 tokenize 取（比找 '#' 可靠：字符串里的 '#' 不是注释）；
    文档串用 AST 取（模块/类/函数体的首个字符串常量）。
    """
    spans: dict[int, tuple[int, int]] = {}
    docs: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                spans[tok.start[0]] = (tok.start[1], tok.end[1])
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return spans, docs
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None) or []
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            docs.update(range(body[0].lineno, (getattr(body[0], "end_lineno", None) or body[0].lineno) + 1))
    return spans, docs


def _def_assign_re(needle: str):
    """匹配"模块级常量赋值且整个字面量就是该 needle"：`NAME = "needle"` / `NAME = needle`。

    ★ 必须要求**整体相等**：否则 `X = "http://127.0.0.1:11434/v1/..."` 也会被认成"定义"，
      于是真·散落字面量被洗白。定义处的意义是"这条针有一个明确的单一来源"。
    """
    n = re.escape(needle)
    return re.compile(rf"^\s*[A-Z][A-Z0-9_]{{2,}}\s*=\s*(?:f?[\"']{n}[\"']|{n})\s*$")


def _grade_ms_hit(rel: str, lineno: int, col: int, spans, docs,
                  prev_line: str, line: str, needle: str) -> str:
    """一条多源命中 → "注释"/"文档串"/"定义"/"已声明"/"另会话产物"/"散落"。"""
    span = spans.get(lineno)
    if span and span[0] <= col:
        return "注释"
    if lineno in docs:
        return "文档串"
    if rel.replace("\\", "/").endswith("core/paths.py"):
        return "定义"
    if _MS_WAIVER in line or _MS_WAIVER in prev_line:
        return "已声明"
    if rel.replace("\\", "/").startswith("qq-bot/plugins/voice/"):
        return "另会话产物"
    code = line[:span[0]] if span else line
    if _def_assign_re(needle).match(code.rstrip()):
        return "定义"
    return "散落"


# 自检样本：分级器必须两头都认得出（"永远报散落"和"永远报无害"都在骗人）
_MS_SELFTEST_CASES = (
    ("注释", "qq-bot/plugins/x.py", ("html = 1  # 端口 11434 的说明",), "11434"),
    ("文档串", "qq-bot/plugins/x.py", ('"""模块说明：端口 11434。"""',), "11434"),
    ("定义", "qq-bot/core/paths.py", ("ENGINE_PORT = 11434",), "11434"),
    ("定义", "qq-bot/plugins/x.py", ('DIFF_SEG = "bust4w"',), "bust4w"),
    ("已声明", "qq-bot/plugins/x.py", ('U = "https://a/11434/{k}"  # audit-ok: 与前端契约',), "11434"),
    ("另会话产物", "qq-bot/plugins/voice/__init__.py", ('U = "http://127.0.0.1:11434/v1/a"',), "11434"),
    ("散落", "qq-bot/plugins/x.py", ('U = "http://127.0.0.1:11434/v1/a"',), "11434"),
    ("散落", "qq-bot/plugins/x.py", ('U = "http://127.0.0.1:11434" + tail',), "11434"),
)


def _ms_grader_selftest() -> str:
    bad = []
    for want, rel, lines, needle in _MS_SELFTEST_CASES:
        src = chr(10).join(lines)
        spans, docs = _src_marks(src)
        col = lines[0].index(needle)
        got = _grade_ms_hit(rel, 1, col, spans, docs, "", lines[0], needle)
        if got != want:
            bad.append(f"{want}→{got}")
    if bad:
        return "FAIL: " + " | ".join(bad)
    return f"PASS（{len(_MS_SELFTEST_CASES)} 样本全判对）"


def audit_multisource() -> dict:
    """多源常量 → **分级**：Python 侧散落字面量才是问题；PS 侧只能对齐值（跨语言）。

    为什么要分级：本项原先只报"命中 N 处"，于是永远一片 ❌ ——**永远红的检查等于没有检查**。
    真正要抓的两件事：
      ① 散落：Python 运行时代码里出现字面量（而非引用唯一来源）；
      ② 重复定义：同一个 needle 在**多个文件**各定义一份（"改了 A 忘了 B"的温床）。

    ★ 扫描范围**刻意**只含运行时（core/agent/plugins/harness + bot.py）：`tools/`、`tests/` 里
      出现同款字面量是**有意为之**（导出工具要镜像本机布局、测试要拿字面量当权威值去比对三源），
      纳入进来只会用豁免标记刷屏，反而把真信号埋掉。跨语言那半边由 smoke §36 用实构造参数守护。
    """
    out: dict[str, dict] = {}
    for label, needle in _MULTISOURCE.items():
        hits: dict[str, list[str]] = {k: [] for k in _MS_BUCKETS}
        ps: list[str] = []
        for p in _iter_runtime_py():
            rel = str(p.relative_to(ROBOT))
            src = _read(p)
            spans, docs = _src_marks(src)
            lines = src.splitlines()
            seen: set[tuple[int, str]] = set()
            for i, line in enumerate(lines, 1):
                if needle not in line:
                    continue
                start = 0
                while True:
                    col = line.find(needle, start)
                    if col < 0:
                        break
                    start = col + len(needle)
                    kind = _grade_ms_hit(rel, i, col, spans, docs,
                                         lines[i - 2] if i >= 2 else "", line, needle)
                    if (i, kind) in seen:
                        continue  # 同一行同一类只计一次（否则一行里两处字面量=两处噪声）
                    seen.add((i, kind))
                    hits[kind].append(f"{rel}:{i}")
        for extra in _MS_PS_FILES:
            q = ROBOT / extra
            if q.is_file():
                for i, line in enumerate(_read(q).splitlines(), 1):
                    if needle in line:
                        ps.append(f"{extra}:{i}")
        out[label] = {**hits, "PS侧": ps}
    return {"needles": out, "selftest": _ms_grader_selftest()}


# ── I. 数据根推导（2026-09-12 新增）─────────────────────────────────────────────
# 本项目有两个语义**不同**的 data 根，而同名目录让人一眼看不出差别：
#   · 规范根 = core.paths.DATA_ROOT（`E:\robot\data`，gitignored）→ 记忆库 / 状态 / 日志
#   · 内容根 = `qq-bot/data`（未被忽略，随仓）→ memes / scenes / 风格样本等**内容**
# 于是 `Path(__file__).resolve().parents[N] / "data"` 这一行成了最容易写错的地方：
# N 差 1 就换了根，而且**不报错**——只是静默读到另一份（或空）文件。
# 现实证据（2026-09-12 实测）：内容根里躺着一个 0 字节的 memory.db（09-09 残留），
# 真库 7.4 MB 在规范根——任何一处根解析漂移都会静默打开那个空库 = "记忆全没了"。
_DERIVED_DATA_ROOT_RE = re.compile(
    r"Path\(__file__\)\.resolve\(\)\.parents\[(\d+)\]\s*/\s*[\"']data[\"']"
    r"(?:\s*/\s*[\"']([^\"']+)[\"'])?")
# 2026-09-12 收口后：规范根一律 core.paths.DATA_ROOT（33 处已迁移）；下面两个桶的含义变成——
# 规范根非空 = ❌ 回潮（N 取决于文件深度，档案里真出过"parents[3] 指错 → 场景库静默为空"）。


def _parse_derived_root(line: str) -> tuple[int, str] | None:
    """`...parents[N] / "data" [/ "file"]` → (N, 文件名)；不匹配 → None。"""
    m = _DERIVED_DATA_ROOT_RE.search(line)
    if not m:
        return None
    return int(m.group(1)), (m.group(2) or "")


def _root_label(resolved) -> str:
    """绝对路径 → "规范根" / "内容根" / "其它根"。"""
    resolved = Path(resolved)
    if resolved == QQBOT / "data":
        return "内容根"
    if resolved == QQBOT.parent / "data":
        return "规范根"
    return "其它根"


def _i_selftest() -> str:
    """自检：解析与判根都必须认得出——一个永远说"没问题"的检查器等于不存在。"""
    cases = (
        ((3, "a.json"), 'X = Path(__file__).resolve().parents[3] / "data" / "a.json"'),
        ((2, ""), "X = Path(__file__).resolve().parents[2] / 'data'"),
        (None, 'X = other_root / "data" / "a.json"'),
        (None, "X = Path(__file__).resolve().parents[3]"),  # 没 / "data" 不算
    )
    bad = []
    for want, line in cases:
        got = _parse_derived_root(line)
        if got != want:
            bad.append(f"解析 {want}→{got}")
    for want, p in (("规范根", QQBOT.parent / "data"), ("内容根", QQBOT / "data"),
                    ("其它根", QQBOT.parent / "elsewhere")):
        got = _root_label(p)
        if got != want:
            bad.append(f"判根 {want}→{got}")
    if bad:
        return "FAIL: " + " | ".join(bad)
    return f"PASS（解析 {len(cases)} 例 + 判根 3 例全对）"


def audit_data_root() -> dict:
    """数据根推导 → 按"解析到哪个根"分级，并用文件系统证据抓真分裂。

    ❌ 两种：① 内容根上的**未声明**推导（看着就像写错根，必须有人解释）
            ② 目标文件**两个根都有** → 到底读哪个只取决于这一行，是真分裂。
    """
    buckets: dict[str, list[str]] = {"规范根": [], "已声明": [], "豁免": [], "待声明": [],
                                     "双份(分裂)": [], "其它根": []}
    for p in _iter_runtime_py():
        rel = str(p.relative_to(ROBOT))
        lines = _read(p).splitlines()
        for i, line in enumerate(lines, 1):
            parsed = _parse_derived_root(line)
            if not parsed:
                continue
            depth, fname = parsed
            resolved = p.resolve().parents[depth] / "data"
            label = _root_label(resolved)
            loc = f"{rel}:{i}"
            if label == "其它根":
                buckets["其它根"].append(f"{loc} → {resolved}")
                continue
            if fname:
                in_canon = (QQBOT.parent / "data" / fname).is_file()
                in_content = (QQBOT / "data" / fname).is_file()
                if in_canon and in_content:
                    buckets["双份(分裂)"].append(f"{loc} → 两处都有 {fname}")
            if label == "规范根":
                buckets["规范根"].append(loc)
                continue
            prev = lines[i - 2] if i >= 2 else ""
            _src = line if _MS_WAIVER in line else prev
            if _MS_WAIVER in _src:
                # 豁免也要把理由**打印出来**：标记不等于隐身，只是"已知且有人负责"
                reason = _src.split(_MS_WAIVER, 1)[1].strip()[:110]
                buckets["豁免"].append(f"{loc} — {reason}")
            elif "#" in line or prev.lstrip().startswith("#"):
                buckets["已声明"].append(loc)
            else:
                buckets["待声明"].append(loc)
    buckets["selftest"] = _i_selftest()
    return buckets


# ── J. 启动器 WPF 引用完整性（2026-09-12 新增）────────────────────────────────
# 为什么要有这项：`$window.FindName("Xxx")` 取不到时返回 $null，而 Launcher.ps1 里到处是
# `if ($b) { … }` 这类守卫 —— 控件名拼错一个字母**不会报错**，只会静默什么都不做
# （按钮点了没反应、状态灯永远不刷新、页面上那行字永远是空的）。语法完全合法，解析器抓不到；
# 而 GUI 又没法无人值守截图，于是这类 bug 只能靠人肉对照 107 个控件名——正是该交给工具的活。
# 唯一事实 = XAML 里的 `x:Name` 声明；四类引用都必须能在里面找到。
_LAUNCHER_XAML_RE = re.compile(r"(?s)\[xml\]\$xaml = @'\r?\n(.*?)\r?\n'@")
_LAUNCHER_NAME_RE = re.compile(r'x:Name="([^"]+)"')
_REF_FINDNAME = re.compile(r'FindName\(\s*"([^"]+)"\s*\)')
_REF_BINDCLICK = re.compile(r'BindClick\s+"([^"]+)"')
# ★ 只写名字、没给处理器的 `BindClick "X"`（整行到此为止）——按钮会**静默无效**：
#   BindClick 内部 `Add_Click($scriptBlock)` 拿到 $null 什么也不接，界面看着正常、点了没反应。
#   2026-09-12 实测抓到日志页的「刷新」就是这种（每 3 秒轮询掩盖了它，肉眼很难发现）。
_REF_BINDCLICK_NOBLOCK = re.compile(r'^\s*BindClick\s+"([^"]+)"\s*(?:#.*)?$', re.M)
_REF_SETLAMP = re.compile(r'Set-Lamp(?:Mini)?\s+"([^"]+)"\s+"([^"]+)"')
_REF_DYNAMIC = re.compile(r"FindName\(\s*\$")


def _strip_ps_comments(src: str) -> str:
    """去掉 PS 行内注释（引用抽取/僵尸判定都要用它）。

    ★ 为什么必须去：J 项第一版把**修复说明里写的** `BindClick "BtnStart"` 当成真引用，
      等于"工具在骂自己的注释"；同理，注释里提过的控件名也不该算"被引用过"（那会藏住僵尸名）。
      实现是启发式：行首到第一个 `#` 之间的引号数为偶数 → 认定 `#` 起注释。
      为什么够用：XAML 里的颜色 `Fill="#4A515B"`、代码里的 `"#F5A623"` 前面都有奇数个引号，
      不会被误剥。反面案例（字符串里含未配对引号 + `#`）概率极低，且不判错的方向是"保留"。
    """
    out = []
    for line in src.splitlines():
        idx = line.find("#")
        if idx >= 0 and line[:idx].count('"') % 2 == 0:
            out.append(line[:idx])
        else:
            out.append(line)
    return chr(10).join(out)


def _launcher_control_refs(src: str) -> tuple[list, int]:
    """PS 源码 → ([(种类, 控件名, 行号)], 动态引用条数)。

    ★ 只认**字面量参数**能静态判定的引用。`FindName($n)`（按变量取名字）单列计数、**不判错**：
      那样只会拿工具的猜测去骂代码，而"会误报的检查 = 没人看的检查"（本项目反复踩过）。
    """
    code = _strip_ps_comments(src)
    refs: list = []
    for kind, rx in (("FindName", _REF_FINDNAME), ("BindClick", _REF_BINDCLICK)):
        for m in rx.finditer(code):
            refs.append((kind, m.group(1), code.count(chr(10), 0, m.start()) + 1))
    for m in _REF_SETLAMP.finditer(code):
        ln = code.count(chr(10), 0, m.start()) + 1
        refs.append(("Set-Lamp", m.group(1), ln))
        refs.append(("Set-Lamp", m.group(2), ln))
    return refs, len(_REF_DYNAMIC.findall(code))


def _launcher_refs_grade(src: str, xaml: str) -> dict:
    """纯函数：给一段 PS 源码 + XAML → 悬空/孤儿/动态/用法串 四项判定（自检直接喂样本）。"""
    declared = sorted(set(_LAUNCHER_NAME_RE.findall(xaml)))
    dset = set(declared)
    refs, dynamic = _launcher_control_refs(src)
    dangling = [{"kind": k, "name": n, "line": ln} for k, n, ln in refs if n not in dset]
    # 反向之一：只写名字没给处理器的 BindClick（按钮静默无效）
    code = _strip_ps_comments(src)
    code_lines = code.splitlines()
    noblock = []
    for m in _REF_BINDCLICK_NOBLOCK.finditer(code):
        ln = code.count(chr(10), 0, m.start()) + 1
        if _waived(code_lines, ln):
            continue
        noblock.append({"name": m.group(1), "line": ln})
    # 反向：声明了却在源码里没被提过——多半是重构残留的"僵尸名字"。
    # ★ 计数必须算上 XAML 自身：真实文件里 XAML 就在同一个 .ps1 内（声明那一次也算出现），
    #   否则每个控件都会被误判成僵尸（自检抓出过这个错）。
    _whole = xaml + chr(10) + _strip_ps_comments(src)
    orphan = [n for n in declared if _whole.count(f'"{n}"') <= 1]
    # 用法串 vs 实现分支：加了新 -Mode 却忘了写进用法串，用户就永远看不到它
    eq_modes = sorted(set(re.findall(r'\$Mode -eq "([a-z]+)"', src)))
    um = re.search(r"用法:\s*-Mode\s+([^\"（]+)", src)
    usage = [x.strip() for x in um.group(1).split("|")] if um else []
    missing_in_usage = [m for m in eq_modes if m not in usage]
    return {"declared": declared, "refs": len(refs), "dangling": dangling, "orphan": orphan,
            "dynamic": dynamic, "eq_modes": eq_modes, "usage": usage,
            "missing_in_usage": missing_in_usage, "noblock": noblock}


_LC_SELFTEST_CASES = (
    # (期望悬空数, 期望孤儿数, 期望动态数, 期望无处理器数, xaml, src)
    (1, 0, 0, 0, '<Button x:Name="BtnOk"/>', '$a = $window.FindName("BtnOk"); $b = $window.FindName("BtnTypo")'),
    (1, 1, 0, 0, '<Button x:Name="BtnOk"/>', 'BindClick "BtnNoSuch" ({ 1 })'),  # BtnNoSuch 悬空 / BtnOk 僵尸
    (1, 0, 0, 0, '<Ellipse x:Name="DotA"/>', 'Set-Lamp "DotA" "TxtB" 0 "x"'),
    (0, 0, 1, 0, '<Button x:Name="BtnOk"/>', '$n = "BtnOk"; $b = $window.FindName($n)'),
    # 僵尸名：声明了但没有任何引用（X/Y 需要声明，否则会先撞上"悬空"）
    (0, 1, 0, 0, '<Button x:Name="X"/><Button x:Name="Y"/><Button x:Name="BtnZombie"/>',
     'Set-Lamp "X" "Y" 0 "x"'),
    # ★ 注释里的提及**不算引用**（J 项第一版就是被自己的修复说明骗了）
    (0, 1, 0, 0, '<Button x:Name="Zed"/>', '# 删除：BindClick "Zed" 曾在此（说明用）'),
    # ★ 只写名字没给处理器 → 按钮静默无效（2026-09-12 实测就是日志页的「刷新」）
    (0, 0, 0, 1, '<Button x:Name="BtnOk"/>', 'BindClick "BtnOk"'),
    (0, 0, 0, 0, '<Button x:Name="BtnOk"/>', 'BindClick "BtnOk" ({ 1 })'),   # 有处理器 → 放行
    (0, 0, 0, 0, '<Button x:Name="BtnOk"/>',
     'BindClick "BtnOk" ({' + chr(10) + '  Refresh-Log' + chr(10) + '})'),   # 跨行处理器 → 放行
)


def _launcher_refs_selftest() -> str:
    bad = []
    for want_d, want_o, want_dyn, want_nb, xaml, src in _LC_SELFTEST_CASES:
        g = _launcher_refs_grade(src, xaml)
        if len(g["dangling"]) != want_d:
            bad.append(f"悬空 {want_d}→{len(g['dangling'])}")
        if len(g["orphan"]) != want_o:
            bad.append(f"孤儿 {want_o}→{len(g['orphan'])}")
        if g["dynamic"] != want_dyn:
            bad.append(f"动态 {want_dyn}→{g['dynamic']}")
        if len(g["noblock"]) != want_nb:
            bad.append(f"无处理器 {want_nb}→{len(g['noblock'])}")
    if bad:
        return "FAIL: " + " | ".join(bad)
    return f"PASS（{len(_LC_SELFTEST_CASES)} 样本全判对）"


# ── J 项第二片：**页面名闭包**（先有页面闭包，才有"页面能不能被打开"）────────────────
# 为什么单独立一片：J 第一片管的是"控件名拼错 → $null + 守卫 = 静默不做事"，
# 而**页面名**拼错更狠：`Show-Page "cffg"` 不匹配任何 `Page` + 首字母大写，于是
# `foreach ($nm in $script:PAGES)` 把所有页面都 Collapsed、PageHome 也被 Collapsed
# （第 1795 行）→ **整个窗口空白、零报错**。三种名字来源必须闭环：
#   ① `$script:PAGES` 的页面容器清单 ② 所有**静态** `Show-Page "x"` 实参
#   ③ `Set-NavActive` 的 `$map` 键（缺键 = 导航不高亮，静默）
# 映射的**值**还必须是被声明的 `x:Name`——它是经 `FindName($map[$k])` 取的**动态**引用，
# J 第一片的动态桶按设计不判错，所以这里单独把值钉住（拼错 = 导航项永不点亮）。
_PAGE_LIST_RE = re.compile(r"\$script:PAGES\s*=\s*@\(([^)]*)\)")
_SHOWPAGE_RE = re.compile(r'Show-Page\s+"([a-z]+)"')
_NAVMAP_START = "$map = @{"


def _launcher_pages_grade(src: str, declared) -> dict:
    """纯函数：PS 源码 + 已声明控件名 → 页面名闭包的四项判定（自检直接喂样本）。"""
    m = _PAGE_LIST_RE.search(src)
    pages = set(re.findall(r'"([^"]+)"', m.group(1))) if m else set()
    args = sorted(set(_SHOWPAGE_RE.findall(src)))
    navmap: dict = {}
    i = src.find(_NAVMAP_START)
    if i >= 0:
        j = src.find("}", i)
        for k, v in re.findall(r"(\w+)\s*=\s*\"(\w+)\"", src[i:j if j > 0 else len(src)]):
            navmap[k] = v
    dec = set(declared)
    # 页面实参 → 容器名：`home`/`opera` 都落在 PageHome（这一层映射写在 Show-Page 里）
    unknown = []
    for a in args:
        if a in ("home", "opera"):
            if "PageHome" not in dec:
                unknown.append("home → PageHome（未声明）")
            continue
        if ("Page" + a[:1].upper() + a[1:]) not in pages:
            unknown.append(a)
    return {"pages": sorted(pages), "args": args, "navmap": sorted(navmap.items()),
            "unknown": sorted(unknown),
            "unhighlighted": sorted(set(args) - set(navmap.keys())),
            "nav_dangling": sorted({v for v in navmap.values() if v not in dec})}


_JP_SELFTEST_CASES = (
    # (期望 unknown 数, 期望 unhighlighted 数, 期望 nav_dangling 数, 源码, 已声明控件)
    # 正常闭环：PageCfg 存在、Show-Page "cfg"、映射有 cfg
    (0, 0, 0, '$script:PAGES = @("PageCfg")' + chr(10) + 'Show-Page "cfg"' + chr(10)
     + '$map = @{ cfg = "NavCfg" }', ["PageCfg", "NavCfg"]),
    # 页面名拼错 → 全页 Collapsed = 白屏无报错（这条是第一片抓不到的那类）。
    # 注：拼错的名字同时也缺映射键，所以两个信号一起亮——那是**对的**，不是误报。
    (1, 1, 0, '$script:PAGES = @("PageCfg")' + chr(10) + 'Show-Page "cffg"' + chr(10)
     + '$map = @{ cfg = "NavCfg" }', ["PageCfg", "NavCfg"]),
    # 映射缺键 → 导航不高亮（静默）
    (0, 1, 0, '$script:PAGES = @("PageAbout")' + chr(10) + 'Show-Page "about"' + chr(10)
     + '$map = @{ cfg = "NavCfg" }', ["PageAbout", "NavCfg"]),
    # 映射值拼错 → 导航项永不点亮（动态引用，第一片按设计不判）
    (0, 0, 1, '$script:PAGES = @("PageCfg")' + chr(10) + 'Show-Page "cfg"' + chr(10)
     + '$map = @{ cfg = "NavCffg" }', ["PageCfg", "NavCfg"]),
    # home/opera 豁免页面清单，但 PageHome 必须真被声明（缺 → unknown 亮，其余两项刻意配平）
    (1, 0, 0, '$script:PAGES = @("PageCfg")' + chr(10) + 'Show-Page "home"' + chr(10)
     + '$map = @{ cfg = "NavCfg"; home = "BtnHome" }', ["PageCfg", "NavCfg", "BtnHome"]),
    (0, 0, 0, '$script:PAGES = @("PageCfg")' + chr(10) + 'Show-Page "home"' + chr(10)
     + '$map = @{ cfg = "NavCfg"; home = "BtnHome" }', ["PageHome", "PageCfg", "NavCfg", "BtnHome"]),
    # 动态实参（`Show-Page ($Mode -replace …)`）不该被当成页面名
    (0, 0, 0, '$script:PAGES = @("PageCfg")' + chr(10) + 'Show-Page ($Mode -replace "^view-", "")'
     + chr(10) + '$map = @{ cfg = "NavCfg" }', ["PageHome", "PageCfg", "NavCfg"]),
)


def _launcher_pages_selftest() -> str:
    bad = []
    for want_u, want_h, want_d, src, dec in _JP_SELFTEST_CASES:
        g = _launcher_pages_grade(src, dec)
        if len(g["unknown"]) != want_u:
            bad.append(f"页面名闭合 {want_u}→{g['unknown']}")
        if len(g["unhighlighted"]) != want_h:
            bad.append(f"映射缺键 {want_h}→{g['unhighlighted']}")
        if len(g["nav_dangling"]) != want_d:
            bad.append(f"映射值悬空 {want_d}→{g['nav_dangling']}")
    if bad:
        return "FAIL: " + " | ".join(bad)
    return f"PASS（{len(_JP_SELFTEST_CASES)} 例：闭环/页面名拼错/映射缺键/映射值悬空/home 豁免/动态实参）"


# ── J 项第三片：**皮肤网页的元素 id 闭包**（框架自带的 generic 样板这一对）──────────────
# 为什么单独立一片：前两片管的是 WPF 侧（控件名 / 页面名，都靠 XAML 的 x:Name 事实），
# 而框架**自带**的主页是 `launcher/web/index.html` + `launcher/web/app.js` 这一对：
# 它没有 XAML、也不走 FindName——前两片都盖不到它，偏偏它是所有皮肤的参考实现。
# 真实故障模式（2026-09-12 分层去重时亲手拆过一遍）：HTML 删掉某个按钮、
# 而 app.js 仍旧 `$('btn-x').addEventListener(...)` → 顶层 TypeError →
# **整个 IIFE 中断** → 后面的绑定与 1s 轮询全部不生效（页面看着"活着"，其实全死）。
# 判三个方向，都是静默类：
#   ① app.js 里当**字面量**用的 id（`$()` / `setText()` / `setLamp()` / `bind()` /
#      `querySelector('#x')`）必须在 index.html 里存在——否则是"更新到空气里"；
#   ② index.html 里 `id="btn-*"` 的按钮必须至少被 app.js 引用一次——
#      否则是"点了没反应"（网页版的无处理器按钮，与第一片同名不同层）；
#   ③ 非按钮 id 一律不判：布局/样式锚点合法地无人引用（`sec-status` 这类）。
#      这是**刻意的边界**——按"宁可少判也不要噪音"的既有口径，不做反向全量核对。
_WEB_ID_CALL_RE = re.compile(r"""(?:\$|setText|setLamp|bind)\(\s*['"]([A-Za-z0-9_-]+)['"]""")
_WEB_SEL_ID_RE = re.compile(r"""querySelector\(\s*['"]#([A-Za-z0-9_-]+)""")
_WEB_HTML_ID_RE = re.compile(r'id="([^"]+)"')


def _web_ids_grade(js: str, html: str) -> dict:
    """纯函数：app.js + index.html 的元素 id 闭包（自检直接喂样本）。"""
    declared = set(_WEB_HTML_ID_RE.findall(html))
    used = set(_WEB_ID_CALL_RE.findall(js)) | set(_WEB_SEL_ID_RE.findall(js))
    btns = {i for i in declared if i.startswith("btn-")}
    return {"declared": sorted(declared), "used": sorted(used),
            "missing": sorted(used - declared),   # js 用了、html 没有
            "dead_btn": sorted(btns - used)}      # btn-* 没人绑


_WEB_SELFTEST_CASES = (
    # (期望 missing 数, 期望 dead_btn 数, js 源码, html 源码)
    # 闭环：绑定 + setText 两处都在
    (0, 0, "bind('btn-ok', f); setText('life-scene', v);",
     '<i id="btn-ok"></i><b id="life-scene"></b>'),
    # HTML 里没有这个 id、JS 还绑它 → 死引用（顶层 TypeError 的来源）
    (1, 0, "$('btn-gone').addEventListener('click', f);", '<b id="life-scene"></b>'),
    # 按钮存在但没人绑 → 点了没反应
    (0, 1, "$('life-scene');", '<i id="btn-orphan"></i><b id="life-scene"></b>'),
    # 非按钮 id 无人引用 → 合法（布局锚点），不许报噪声
    (0, 0, "$('sec-status');", '<i id="sec-status"></i>'),
    # setLamp 与 querySelector('#id') 两种取法都要算进 used
    (1, 0, "setLamp('lamp-x', true); document.querySelector('#wb-text');", '<i id="lamp-x"></i>'),
)


def _web_ids_selftest() -> str:
    bad = []
    for want_m, want_d, js, html in _WEB_SELFTEST_CASES:
        g = _web_ids_grade(js, html)
        if len(g["missing"]) != want_m:
            bad.append(f"缺失 {want_m}→{g['missing']}")
        if len(g["dead_btn"]) != want_d:
            bad.append(f"死按钮 {want_d}→{g['dead_btn']}")
    if bad:
        return "FAIL: " + " | ".join(bad)
    return (f"PASS（{len(_WEB_SELFTEST_CASES)} 例：闭环/HTML 缺 id/按钮无人绑/"
            f"非按钮豁免/setLamp+querySelector）")


# ── J 项第四片：**界面文案表的覆盖面**（全局语言切换，2026-09-12）────────────────────
# 为什么单独立一片：语言切换做在"显示出口"（界面树）上——好处是漏翻看得见、坏处是**漏翻不会报错**。
# 这一片把"静态界面文案"的覆盖率变成机器判定的：XAML 里每个中文字面量都必须在文案表里有条目。
# 判据只做**单向**（XAML ⊆ 表）：表里多出来的键是合法的（动态句、列表项、正则模式），不判。
# 另外两条零噪声检查：表键不许是英文（键必须是中文原文，否则切换回中文会失败）、值不许为空。
_UI_LANG_TABLE_RE = re.compile(r"(?s)\$script:UI_LANG_TABLE = @\{(.*?)\n\}")
_UI_LANG_KEY_RE = re.compile(r'"((?:[^"\\]|\\.)*)"\s*=\s*"((?:[^"\\]|\\.)*)"')


def _ui_lang_grade(src: str, xaml: str) -> dict:
    """纯函数：PS 源码 + XAML → 文案表覆盖面判定（自检直接喂样本）。"""
    m = _UI_LANG_TABLE_RE.search(src)
    keys, empty_val, en_key = set(), [], []
    if m:
        for k, v in _UI_LANG_KEY_RE.findall(m.group(1)):
            keys.add(k)
            if not v.strip():
                empty_val.append(k)
            if not re.search(r"[\u4e00-\u9fff]", k):
                en_key.append(k)          # 键必须是中文原文
    lit = set()
    for attr in ("Text", "Content", "ToolTip"):
        for mm in re.finditer(rf'{attr}="([^"]*)"', xaml):
            v = mm.group(1).strip()
            if re.search(r"[\u4e00-\u9fff]", v):
                lit.add(v)
    return {"table_keys": sorted(keys), "xaml_literals": sorted(lit),
            "untranslated": sorted(lit - keys), "empty_value": sorted(empty_val),
            "non_chinese_key": sorted(en_key)}


_UI_LANG_SELFTEST_CASES = (
    # (期望未覆盖数, 期望空值数, 期望非中文键数, 源码, XAML)
    # 闭环：XAML 的文案在表里
    (0, 0, 0, '$script:UI_LANG_TABLE = @{\n  "主页" = "Home"\n}\n', '<TextBlock Text="主页"/>'),
    # XAML 多了一条、表里没有 → 漏翻（这一片存在的理由）
    (1, 0, 0, '$script:UI_LANG_TABLE = @{\n  "主页" = "Home"\n}\n', '<TextBlock Text="主页"/><Button Content="设置"/>'),
    # 值写空 → 切到英文会变成空白按钮
    (0, 1, 0, '$script:UI_LANG_TABLE = @{\n  "主页" = ""\n}\n', '<TextBlock Text="主页"/>'),
    # 键写成了英文 → 切回中文会失败（键必须是中文原文）；注意"漏翻"信号**同时**亮是正确的
    # （表里没有中文键 ⇒ XAML 那条中文自然没被覆盖），跟页面名那片"两个信号一起亮是对的"同一口径。
    (1, 0, 1, '$script:UI_LANG_TABLE = @{\n  "Home" = "Home"\n}\n', '<TextBlock Text="主页"/>'),
    # 表里多出的键（动态句/列表项）是合法的，不许报
    (0, 0, 0, '$script:UI_LANG_TABLE = @{\n  "主页" = "Home"\n  "（未选择）" = "(nothing selected)"\n}\n',
     '<TextBlock Text="主页"/>'),
)


def _ui_lang_selftest() -> str:
    bad = []
    for want_u, want_e, want_k, src, xaml in _UI_LANG_SELFTEST_CASES:
        g = _ui_lang_grade(src, xaml)
        if len(g["untranslated"]) != want_u:
            bad.append(f"漏翻 {want_u}→{g['untranslated']}")
        if len(g["empty_value"]) != want_e:
            bad.append(f"空值 {want_e}→{g['empty_value']}")
        if len(g["non_chinese_key"]) != want_k:
            bad.append(f"非中文键 {want_k}→{g['non_chinese_key']}")
    if bad:
        return "FAIL: " + " | ".join(bad)
    return (f"PASS（{len(_UI_LANG_SELFTEST_CASES)} 例：闭环/XAML 多一条/值空/键非中文/表多出键合法）")


def audit_launcher_refs() -> dict:
    """启动器引用完整性（见上方 J 项说明）。文件不存在时明确报错，不假装通过。"""
    p = ROBOT / "launcher" / "Launcher.ps1"
    src = _read(p)
    if not src.strip():
        return {"error": f"读不到 {p}", "declared": [], "dangling": [], "orphan": [],
                "refs": 0, "dynamic": 0, "selftest": "SKIP（无文件）"}
    m = _LAUNCHER_XAML_RE.search(src)
    if not m:
        return {"error": "未找到 XAML here-string（[xml]$xaml = @' … '@）", "declared": [],
                "dangling": [], "orphan": [], "refs": 0, "dynamic": 0, "selftest": "SKIP（无 XAML）"}
    out = _launcher_refs_grade(src, m.group(1))
    out.update({f"pg_{k}": v for k, v in _launcher_pages_grade(src, out.get("declared") or []).items()})
    out["pages_selftest"] = _launcher_pages_selftest()
    # J 第三片：框架自带主页（generic 样板）的网页元素 id 闭包。文件缺失**当失败**，
    # 不能因为"读不到 → 两个集合都空 → missing 0/dead 0"就假装通过（取空当失败）。
    _wjs = _read(ROBOT / "launcher" / "web" / "app.js")
    _wht = _read(ROBOT / "launcher" / "web" / "index.html")
    out["web_selftest"] = _web_ids_selftest()
    # J 第四片：界面文案表（全局语言切换）的覆盖面
    out.update({f"ui_{k}": v for k, v in _ui_lang_grade(src, m.group(1)).items()})
    out["ui_selftest"] = _ui_lang_selftest()
    if not _wjs.strip() or not _wht.strip():
        out["web_error"] = ("读不到 launcher/web/app.js 或 index.html——"
                            "框架自带样板，两份都必须在（否则本片静默通过）")
    else:
        out.update({f"web_{k}": v for k, v in _web_ids_grade(_wjs, _wht).items()})
        out["web_error"] = ""
    out["error"] = ""
    out["selftest"] = _launcher_refs_selftest()
    return out


# ── K. 文档契约（docs/接口文档.md ⟷ 代码）：文档是给第三方 MOD 作者的**合同** ──────
# 为什么要有这项：合同与实现脱节时，被坑的是**别人**——他们照文档写，拿到 404，再怀疑是自己写错了。
# 所以"文档里承诺的路由/字段"必须能机器验证。两类：① HTTP/WS 路由 ② /gal/content.json 的顶层键。
_CODE_ROUTE_RE = re.compile(r"@\w+\.(get|post|put|delete|patch|websocket)\(\s*[\"']([^\"']+)[\"']")
_DOC_ROUTE_TABLE_RE = re.compile(r"\|\s*(GET|POST|PUT|DELETE|PATCH|WS)\s*\|\s*`([^`]+)`")
# 行内写法（**`GET /gal/xxx`**）；刻意排除含 `<...>` 的**占位**写法——散文里的示例不是真路由
_DOC_ROUTE_INLINE_RE = re.compile(r"`(GET|POST|PUT|DELETE|PATCH|WS)\s+(/[A-Za-z0-9_\-./{}:]+)`")
_DOC_KEYS_RE = re.compile(r"内容索引：`\{([^}]+)\}`")
# 文档里**带仓库前缀**的路径：只有这四个顶层是"仓库根相对"的（`data/` 是用户运行时目录、
# `tools/` 是 gitignored 的第三方运行时，两者都允许不存在；`qq-bot/tools/...` 这类带前缀的则必须存在）。
# 限定前缀是刻意的：离开上下文没法判断 `tools/xxx.py` 是相对仓库根还是相对 `cd qq-bot` 之后——
# 而**会误报的检查等于没人看的检查**（本项目反复踩过），所以宁可比对范围窄一点。
_DOC_PREFIXED_PATH_RE = re.compile(r"`((?:qq-bot|docs|launcher|build)/[A-Za-z0-9_\-./]+?)`")
# K 项扫这几份权威文档（都是第三方/使用者会照着做的）
_DOC_CONTRACT_FILES = ("docs/接口文档.md", "docs/MOD开发指南.md", "docs/维护手册.md")


def _doc_prefixed_paths(text: str) -> list:
    """文档里带仓库前缀的路径（去重排序；尾斜杠去掉以便 os 判定）。"""
    return sorted({m.group(1).rstrip("/") for m in _DOC_PREFIXED_PATH_RE.finditer(text)})


# ── K 项·字段级（路由之下的一层：路由对了但**字段名**写错，同样是静默失效）──────────────
# 为什么只挑这两张表（不贪多）：
#   · §4.4 命令通道 = **第三方皮肤唯一的动作入口**。皮肤按钮 postMessage({cmd:"stopbot"})，
#     而 PS 侧 Handle-WebCmd 不认这个名字 → **什么都不发生**、不报错。本项目真踩过
#     "带处理器名但没挂处理器"的静默无效按钮，同一类故障在字段层再犯一次不值得。
#   · §5.3 ASSETS 素材口 = GAL 客户端**唯一换素材处**。键名写错 = 素材静默不生效，
#     而页面有渐变兜底，看上去"正常"——最难靠肉眼发现的那一类。
# 两张表的第一列都是**反引号标识符**，所以能机械双向比对、且能取到精确全集（不需要白名单）。
_DOC_SEC_CMD = "4.4"        # 命令通道（JS → PS）
_DOC_SEC_ASSETS = "5.3"     # 素材口 ASSETS
_DOC_SEC_RE = re.compile(r"^#{2,4}\s*(\d+(?:\.\d+)*)[^\n]*$", re.M)


def _doc_section(doc: str, number: str) -> str:
    """文档里编号为 number 的那一节正文（到下一个标题为止）。

    锚在**文档自己的小节号**上而不是标题措辞：标题文字会改，编号是给读者的定位坐标。
    取不到 → 空串，由调用方当**失败**处理（不静默通过）。
    """
    heads = list(_DOC_SEC_RE.finditer(doc))
    for i, m in enumerate(heads):
        if m.group(1) == number:
            end = heads[i + 1].start() if i + 1 < len(heads) else len(doc)
            # `[^\n]*$` 吃到行尾（不含换行），故正文从下一行起：去掉紧随的换行
            return doc[m.end():end].lstrip("\r\n")
    return ""


def _doc_table_first_col_idents(section: str) -> set:
    """小节里表格**第一列**的反引号标识符（一格写多个如 `` `a` / `b` `` 也拆开）。"""
    out: set = set()
    for line in section.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = s.split("|")
        if len(cells) < 3:          # split 后 [0] 是行首那个空串
            continue
        first = cells[1]
        if set(first.strip()) <= set("-: "):   # `|---|---|` 分隔行
            continue
        out |= {t.strip() for t in re.findall(r"`([^`]+)`", first) if t.strip()}
    return out


def _code_webcmds(src: str) -> set:
    """`Handle-WebCmd` 真正派发的 cmd 全集。

    ★ 只数**这个函数体内**的 `$cmd -eq "x"`。保留字 `diag` / `hb` 在**调用它之前**就被拦截
      （`add_WebMessageReceived` 里先判），天然不进这个集合——所以这里**不需要保留字白名单**，
      少一个会腐烂的清单（清单会忘记更新，而拦截点改位置不会）。
      取空由调用方当**失败**处理。
    """
    start = src.find("function Handle-WebCmd(")
    if start < 0:
        return set()
    end = src.find("\n}", start)
    body = src[start:end if end > 0 else len(src)]
    return {m.group(1) for m in re.finditer(r'\$cmd\s+-eq\s+"([a-z]+)"', body)}


def _code_assets_keys(src: str) -> set:
    """`gal.js` 里 **ASSETS 上真正可用**的键全集 = 字面量顶层键 ∪ `ASSETS.<键> =` 赋值键。

    为什么两个都要取：`quotesByCard` 不在字面量里，而是紧随其后由
    `ASSETS.quotesByCard = QUOTES_BY_CARD` 挂上去的（第 42 行）。只看字面量会把它漏掉，
    于是"文档写了、代码没有"变成假 ❌ —— 工具指错方向比不报更坏。

    实现按**字符扫描**而非逐行数花括号：注释里出现一个 `{`、或字符串值里出现 `//`
    （本文件的素材 URL 全是 `https://…`）都会让逐行计数算错深度。字符串/注释两种状态
    先剔除再数深度，才是语法而不是风格。
    """
    m = re.search(r"var\s+ASSETS\s*=\s*\{", src)
    if not m:
        return set()
    body = src[m.end():]
    n, i, depth, keys = len(body), 0, 1, set()
    at_line_start = True          # 本行除空白外还没出现别的字符（= 可能是键名）
    while i < n:
        c = body[i]
        if c in "\"'`":                                  # 字符串/模板串：整段跳过
            q, i = c, i + 1
            while i < n and body[i] != q:
                i += 2 if body[i] == "\\" else 1
            i += 1
            at_line_start = False
            continue
        if c == "/" and i + 1 < n and body[i + 1] == "*":   # 块注释
            j = body.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c == "/" and i + 1 < n and body[i + 1] == "/":   # 行注释
            j = body.find("\n", i)
            i = n if j < 0 else j
            continue
        if depth == 1 and at_line_start and (c.isalpha() or c in "_$"):
            j = i
            while j < n and (body[j].isalnum() or body[j] in "_$"):
                j += 1
            k = j
            while k < n and body[k] in " \t":
                k += 1
            if k < n and body[k] == ":":                 # `键:` 而非 `键 =`
                keys.add(body[i:j])
            i = j
            at_line_start = False
            continue
        if c == "{":
            depth += 1
            at_line_start = False
        elif c == "}":
            depth -= 1
            if depth <= 0:
                break
            at_line_start = False
        elif c == chr(10):
            at_line_start = True
        elif not c.isspace():
            at_line_start = False
        i += 1
    # 运行时挂上去的键（`ASSETS.x = …`，注意排除 `==` 比较）
    keys |= {m2.group(1) for m2 in re.finditer(r"ASSETS\.([A-Za-z_$][\w$]*)\s*=(?!=)", src)}
    return keys


# K 项·字段级（续）：**内容包 `type` 取值集合**——"未知 type 跳过不炸"意味着**整包静默跳过**，
# 所以文档与代码两个方向都必须一致（文档多写一个 = 作者造出永不生效的包；代码多一个 = 别人不知道能用）。
# 文档面锚在 §3.1 的 JSON 示例行（`"type": "card | voice | …"`）；代码面锚在 `VALID_TYPES` 元组。
# ★ 顺带记一条**刻意不做**的事（避免后来人重踩）：`pack.json` 的**逐字段**表（§3.1–3.8 各表首列）
#   不做机器双向核对。理由是取证过的：那几张表的首列**混装三类东西**——裸字段名（`spec`）、
#   字段路径（`card.name/cls/star`、`compat.core_api`、`entries[].always`）、**包内文件名**
#   （`card.json`、`sprites/manifest.json`）；而且契约由 **Python 与 PowerShell 两侧共同消费**
#   （§3.1 明写 `title/author/license` 是启动器展示用，读它的是 `Launcher.ps1` 而非 `core/packs.py`）。
#   硬凑一张比对本会产出 46 条假 ❌（真跑过），而"一个把噪声混报成 ❌ 的检查器会让人开始忽略它的输出"
#   ——比不查更坏。字段级的活儿留给真正扁平的契约（§4.4 命令、§5.3 素材口、§4.1 皮肤 manifest）。
_DOC_SEC_PACK_TYPE = "3.1"
_DOC_TYPE_LINE_RE = re.compile(r"\"type\"\s*:\s*\"([^\"]+)\"")
_CODE_VALID_TYPES_RE = re.compile(r"VALID_TYPES\s*=\s*\(([^)]*)\)")


def _doc_pack_types(doc: str) -> set:
    """§3.1 那行 `"type": "a | b | …"` 声明的 type 取值（取空当失败处理）。"""
    m = _DOC_TYPE_LINE_RE.search(_doc_section(doc, _DOC_SEC_PACK_TYPE))
    if not m:
        return set()
    return {t.strip() for t in m.group(1).split("|") if t.strip()}


def _code_pack_types(src: str) -> set:
    """`VALID_TYPES = (…)` 的取值（取空当失败处理）。"""
    m = _CODE_VALID_TYPES_RE.search(src)
    if not m:
        return set()
    return set(re.findall(r"[\"']([^\"']+)[\"']", m.group(1)))


# K 项·字段级（续）：**皮肤包 `manifest.json` 字段**——第三方皮肤作者照抄的那张表。
# 文档面是**两份文档的并集**：`接口文档.md` §4.1（契约总表）+ `皮肤包接口规范-v1.md` §2（深层细节）。
# 为什么必须并集：索引 §2 把两者定为"总表 + 细节"，`palette` 这类字段只在其中一份里出现——
# 只读一份就会把它误报成"代码有、文档无"。
# 归一化：文档写 `palette.bg/fg/accent/muted`，代码读 `$j.palette.bg`——两边都取**首个点分段**，
# 于是都归到 `palette`，口径一致（这是有意的：字段级的粒度就是 manifest 的顶层键）。
_DOC_SEC_SKIN_MANIFEST = "4.1"
_DOC_SKIN_DEEP = ("docs/皮肤包接口规范-v1.md", "2")
_CODE_SKIN_FIELD_RE = re.compile(r"\$j\.([A-Za-z_][A-Za-z0-9_]*)")


def _top_seg(name: str) -> str:
    """点分路径取首段（`palette.bg` → `palette`）。"""
    return name.split(".")[0].strip()


def _doc_skin_fields(doc: str, deep: str) -> set:
    """两份文档里声明的皮肤 manifest 顶层字段（取空由调用方当失败处理）。"""
    out = {_top_seg(t) for t in _doc_table_first_col_idents(_doc_section(doc, _DOC_SEC_SKIN_MANIFEST))}
    out |= {_top_seg(t) for t in _doc_table_first_col_idents(_doc_section(deep, _DOC_SKIN_DEEP[1]))}
    return {t for t in out if t}


def _code_skin_fields(src: str) -> set:
    """`Get-SkinManifest` 体内真正读取的 manifest 字段（`$j.<字段>`）。

    ★ 按**函数体**取而不是全文取 `$j.`：同一个 `$j` 变量名在别处还装着 pack.json
      （`$j.type/$j.version/$j.card.key`），全文取会把两个契约混成一个集合 → 假 ❌。
    """
    start = src.find("function Get-SkinManifest")
    if start < 0:
        return set()
    end = src.find("\n}", start)
    body = src[start:end if end > 0 else len(src)]
    return {m.group(1) for m in _CODE_SKIN_FIELD_RE.finditer(body)}


# K 项·自我描述一致性：`维护手册.md` §7.3 的审计表 ⟷ **工具实际打印的段清单**。
# 为什么：手册那张表是"审计到底有哪些检查"的唯一地图。加了新段却忘登记手册，读者就不知道有这项检查——
# O 项就是这么加上的（上一轮靠人记得，现在靠机器）。零误报：两边都是**结构化清单**（段字母），不是散文。
# ★ 正则必须同时认 `print("X. …")` 与 `print(f"X. …")` 两种写法：实测 B/C/F/H 四段用的是 f-string，
#   只认前一种会让这检查**自己漏掉四段**（写这条时先踩到，靠"先数一遍真实数量"发现）。
_TOOL_SECTION_RE = re.compile(r'print\(f?"([A-O])\. ')
_MANUAL_SECTION_ROW_RE = re.compile(r"^\| ([A-O]) \|", re.M)


def _tool_sections(src: str) -> list:
    """工具实际打印的段字母。"""
    return sorted(set(_TOOL_SECTION_RE.findall(src)))


def _manual_sections(md: str) -> list:
    """`维护手册.md` §7.3 审计表的段字母（表行首列）。"""
    return sorted(set(_MANUAL_SECTION_ROW_RE.findall(md)))


def _norm_route(meth: str, path: str) -> str:
    m = meth.upper()
    if m == "WS":            # 代码侧写 websocket，文档表格写 WS —— 归一成一个口径
        m = "WEBSOCKET"
    p = path.strip().rstrip("/")
    return f"{m} {p or '/'}"


def _code_routes_in(src: str) -> set:
    """一段源码里的路由集合（变量名不限：`@app.post(...)` / `@_la_app.post(...)` 都算）。"""
    return {_norm_route(m.group(1), m.group(2)) for m in _CODE_ROUTE_RE.finditer(src)}


def _doc_routes_in(doc: str) -> set:
    """接口文档里声明的路由集合（表格写法 ∪ 行内写法）。"""
    out = {_norm_route(m.group(1), m.group(2)) for m in _DOC_ROUTE_TABLE_RE.finditer(doc)}
    out |= {_norm_route(m.group(1), m.group(2)) for m in _DOC_ROUTE_INLINE_RE.finditer(doc)}
    return out


def _doc_content_keys(doc: str) -> list:
    """文档里写的 `/gal/content.json` 顶层键（取"内容索引：`{a, b, c}`"这句）。"""
    m = _DOC_KEYS_RE.search(doc)
    if not m:
        return []
    return [k.strip().strip("`") for k in m.group(1).split(",") if k.strip()]


def _content_index_keys() -> list:
    """`/gal/content.json` 载荷的顶层键（AST 取，不靠正则猜）。

    ★ 别只看 `content_index()`：它只是 `_index("content", _build_content)` 的壳，真正的载荷字典
      在 `_build_content()` 里——第一版只找前者，取到空列表，于是"代码有、文档没有"这条
      反而被报成了"文档多写了三键"（工具指错了方向）。取空由调用方当**失败**处理，不静默通过。
    """
    try:
        tree = ast.parse(_read(QQBOT / "core" / "packs.py"))
    except SyntaxError:
        return []
    funcs = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            funcs.setdefault(node.name, node)
    for fname in ("_build_content", "content_index"):
        node = funcs.get(fname)
        if node is None:
            continue
        keys: list = []
        for sub in ast.walk(node):
            if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
                for k in sub.value.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str) and k.value not in keys:
                        keys.append(k.value)
        if keys:
            return keys
    return []


def _k_selftest() -> str:
    bad = []
    _src = chr(10).join(('@app.get("/a/b")', '@_la_app.post("/launcher/persona")',
                         '@app.websocket("/gal/ws")'))
    if _code_routes_in(_src) != {"GET /a/b", "POST /launcher/persona", "WEBSOCKET /gal/ws"}:
        bad.append(f"代码路由抽取 {sorted(_code_routes_in(_src))}")
    _doc = ("| GET | `/a/b` | x |" + chr(10) + "| WS | `/gal/ws` | x |" + chr(10)
            + "散文里的占位写法 `GET /gal/stage/<包名>/<包内相对路径>` 必须被忽略" + chr(10)
            + "内容索引：`{k1, k2}`")
    if _doc_routes_in(_doc) != {"GET /a/b", "WEBSOCKET /gal/ws"}:
        bad.append(f"文档路由抽取 {sorted(_doc_routes_in(_doc))}")
    if _doc_content_keys(_doc) != ["k1", "k2"]:
        bad.append(f"文档键抽取 {_doc_content_keys(_doc)}")
    if _doc_content_keys("没有那句话") != []:
        bad.append("缺句应返回空（好让 K 项报'找不到'而不是静默通过）")
    # 带前缀路径抽取：只认四个仓库顶层，`tools/xxx`（可能相对 cd 之后的目录）必须被忽略
    _pp = _doc_prefixed_paths("见 `qq-bot/core/paths.py` 与 `tools/export_cards.py` 还有 `docs/README.md/`")
    if _pp != ["docs/README.md", "qq-bot/core/paths.py"]:
        bad.append(f"前缀路径抽取 {_pp}")
    # 字段级：小节取正文（锚小节号）+ 表第一列反引号标识符（一格多名要拆开、分隔行要跳过）
    _sec = ("## 9. 别节" + chr(10) + "| a | b |" + chr(10) + "|---|---|" + chr(10)
            + "### 4.4 命令通道" + chr(10) + "| cmd | data | 行为 |" + chr(10)
            + "|---|---|---|" + chr(10) + "| `start` | 空 | x |" + chr(10)
            + "| `opera` / `home` | 空 | x |" + chr(10) + "> 保留字 `diag`（正文，不算）"
            + chr(10) + "### 4.5 下一节" + chr(10) + "| `notmine` | x | y |")
    if _doc_section(_sec, "4.4") != ("| cmd | data | 行为 |" + chr(10) + "|---|---|---|" + chr(10)
                                     + "| `start` | 空 | x |" + chr(10)
                                     + "| `opera` / `home` | 空 | x |" + chr(10)
                                     + "> 保留字 `diag`（正文，不算）" + chr(10)):
        bad.append(f"小节取正文不对：{_doc_section(_sec, '4.4')!r}")
    if _doc_section(_sec, "9.9") != "":
        bad.append("小节号不存在应返回空（好让 K 项报'取不到'而不是静默通过）")
    if _doc_table_first_col_idents(_doc_section(_sec, "4.4")) != {"start", "opera", "home"}:
        bad.append(f"表首列抽取 {sorted(_doc_table_first_col_idents(_doc_section(_sec, '4.4')))}")
    # 命令侧：只认 Handle-WebCmd 体内，函数外的 diag 分支不许算进来
    _ps = ('$cm = [string]$m.cmd' + chr(10) + 'if ($cm -eq "diag") { }' + chr(10)
           + 'function Handle-WebCmd($cmd, $data) {' + chr(10)
           + '    if ($cmd -eq "start") { }' + chr(10)
           + '    elseif ($cmd -eq "plugins") { }' + chr(10)
           + '}' + chr(10) + 'function Other($cmd) { $cmd -eq "ghost" }')
    if _code_webcmds(_ps) != {"start", "plugins"}:
        bad.append(f"命令派发抽取 {sorted(_code_webcmds(_ps))}")
    if _code_webcmds("function Nothing() { }") != set():
        bad.append("无 Handle-WebCmd 应返回空（当失败处理）")
    # 素材口：注释里的花括号、字符串里的 //（素材 URL 全是 https://）、嵌套键、运行时挂键
    _js = ("var ASSETS = {" + chr(10) + "/** 注释 { 不是键 */" + chr(10)
           + "\tbgDefault: 'https://app.local/x.png'," + chr(10) + "\tbgByScene: {}," + chr(10)
           + "\tspriteMap: {" + chr(10) + "\t\tamiya: { '战斗': 'firm' }" + chr(10) + "\t},"
           + chr(10) + "\tfullBase: 'y'" + chr(10) + "};" + chr(10)
           + "ASSETS.quotesByCard = QUOTES_BY_CARD;" + chr(10)
           + "if (ASSETS.spriteMap === x) { }" + chr(10) + "var OTHER = { zzz: 1 };")
    if _code_assets_keys(_js) != {"bgDefault", "bgByScene", "spriteMap", "fullBase", "quotesByCard"}:
        bad.append(f"ASSETS 键抽取 {sorted(_code_assets_keys(_js))}")
    if "zzz" in _code_assets_keys(_js):
        bad.append("ASSETS 之外的第二个对象不该被算进来")
    # type 取值：只在 §3.1 的 JSON 示例行取；表格里那行「上表 N 类之一」不是枚举，不许被当成取值
    _doc_ty = ("### 3.1 `pack.json` 字段" + chr(10) + "```json" + chr(10) + "{" + chr(10)
               + '  "type": "card | voice | item",' + chr(10) + "}" + chr(10) + "```" + chr(10)
               + "| `type` | ✓ | 上表 3 类之一 |")
    if _doc_pack_types(_doc_ty) != {"card", "voice", "item"}:
        bad.append(f"type 取值抽取 {sorted(_doc_pack_types(_doc_ty))}")
    if _doc_pack_types("### 3.1 无示例行") != set():
        bad.append("无 type 示例行应返回空（好当失败处理）")
    if _code_pack_types('VALID_TYPES = ("a", "b")') != {"a", "b"}:
        bad.append("VALID_TYPES 抽取")
    # 皮肤字段：两份文档并集 + 点分归一（`palette.bg/fg` 与 `palette.bg` 都归到 `palette`）
    _doc_sk = ("### 4.1 `manifest.json` 字段" + chr(10) + "| 字段 | 类型 |" + chr(10) + "|---|---|"
               + chr(10) + "| `spec` | string |" + chr(10)
               + "| `palette.bg/fg/accent/muted` | string |")
    _deep_sk = ("## 2. manifest schema" + chr(10) + "| 字段 | 类型 |" + chr(10) + "|---|---|"
                + chr(10) + "| `palette.bg` | string |" + chr(10) + "| `note` | string |")
    if _doc_skin_fields(_doc_sk, _deep_sk) != {"spec", "palette", "note"}:
        bad.append(f"皮肤字段并集 {sorted(_doc_skin_fields(_doc_sk, _deep_sk))}")
    # 代码侧按**函数体**取：同一文件别处也把 pack.json 装进 `$j`，全文取会把两个契约混成一个集合
    _ps_sk = ("function Get-SkinManifest {" + chr(10) + "  if ($j) { $def.spec = [string]$j.spec;"
              + " $def.palette = @{ bg = [string]$j.palette.bg } }" + chr(10) + "}" + chr(10)
              + "function Other { $j.type" + chr(10) + "}")
    if _code_skin_fields(_ps_sk) != {"spec", "palette"}:
        bad.append(f"皮肤字段抽取 {sorted(_code_skin_fields(_ps_sk))}")
    # 自我描述：段清单必须两种打印写法都认（B/C/F/H 是 f-string），且两向差集都要报
    _ts = ('    print("A. 甲")' + chr(10) + '    print(f"B. 乙：{x}")' + chr(10)
           + '    print("C. 丙")')
    if _tool_sections(_ts) != ["A", "B", "C"]:
        bad.append(f"段清单抽取（f-string 必须认） {_tool_sections(_ts)}")
    _md = "| A | 甲 |" + chr(10) + "| B | 乙 |" + chr(10) + "| 说明 | 不是段 |"
    if _manual_sections(_md) != ["A", "B"]:
        bad.append(f"手册段清单抽取 {_manual_sections(_md)}")
    if bad:
        return "FAIL: " + " | ".join(bad)
    return ("PASS（12 例：代码路由/文档路由/文档键/缺句/前缀路径/小节取正文/命令派发/"
            "素材口键/type 取值/皮肤字段/段清单两写法/手册表行）")


def audit_doc_contract() -> dict:
    """接口文档 ⟷ 代码：文档说有的路由/键，代码必须真有（见上方 K 项说明）。

    ★ 已知边界（避免读者误判为"全绿=万无一失"）：
      · 路由只认**装饰器写法** `@<app>.get/post/...("路径")`。若将来改用 `include_router(prefix=…)`
        或 `add_api_route` 动态注册，这里会漏 → 文档侧就成了假 ❌。届时扩本函数，**别改文档去迁就工具**。
      · 键只认 `_build_content()` 里**字面量**的字典键（`**spread` 进来的取不到）。
      · 路径只查带 `qq-bot/ · docs/ · launcher/ · build/` 前缀的（其余无法脱离上下文判断，见正则上方说明）。
    """
    code_routes: dict = {}
    for p in _iter_runtime_py():
        for r in _code_routes_in(_read(p)):
            code_routes.setdefault(r, str(p.relative_to(ROBOT)))
    doc = _read(ROBOT / "docs" / "接口文档.md")
    doc_routes = _doc_routes_in(doc)
    doc_keys = _doc_content_keys(doc)
    code_keys = _content_index_keys()
    # 三份权威文档里的**带前缀路径**必须真存在（否则就是"照着文档做，找不到文件"）
    path_hits, missing_paths = 0, []
    for rel_doc in _DOC_CONTRACT_FILES:
        for cand in _doc_prefixed_paths(_read(ROBOT / rel_doc)):
            path_hits += 1
            if not (ROBOT / cand).exists():
                missing_paths.append(f"{rel_doc} → {cand}")
    # 字段级：§4.4 命令通道 ⟷ Handle-WebCmd 派发；§5.3 素材口 ⟷ gal.js ASSETS 顶层键
    doc_cmds = _doc_table_first_col_idents(_doc_section(doc, _DOC_SEC_CMD))
    code_cmds = _code_webcmds(_read(ROBOT / "launcher" / "Launcher.ps1"))
    doc_assets = _doc_table_first_col_idents(_doc_section(doc, _DOC_SEC_ASSETS))
    code_assets = _code_assets_keys(_read(ROBOT / "launcher" / "web" / "gal.js"))
    # 字段级：type 取值集合（§3.1 示例行 ⟷ VALID_TYPES）；皮肤 manifest 字段（两份文档并集 ⟷ 读取处）
    doc_types = _doc_pack_types(doc)
    code_types = _code_pack_types(_read(QQBOT / "core" / "packs.py"))
    doc_skin = _doc_skin_fields(doc, _read(ROBOT / _DOC_SKIN_DEEP[0]))
    code_skin = _code_skin_fields(_read(ROBOT / "launcher" / "Launcher.ps1"))
    # 自我描述一致性：手册 §7.3 审计表 ⟷ 本工具实际打印的段清单
    sec_doc = _manual_sections(_read(ROBOT / "docs" / "维护手册.md"))
    sec_code = _tool_sections(_read(Path(__file__)))
    return {
        "doc_only": sorted(doc_routes - set(code_routes)),
        "code_only": sorted(set(code_routes) - doc_routes),
        "n_code": len(code_routes), "n_doc": len(doc_routes),
        "code_keys": code_keys, "doc_keys": doc_keys, "keys_found": bool(doc_keys),
        "keys_missing": [k for k in code_keys if k not in doc_keys],
        "keys_extra": [k for k in doc_keys if k not in code_keys],
        "path_hits": path_hits, "missing_paths": missing_paths,
        "cmds": {"doc": doc_cmds, "code": code_cmds,
                 "doc_only": sorted(doc_cmds - code_cmds),
                 "code_only": sorted(code_cmds - doc_cmds)},
        "assets": {"doc": doc_assets, "code": code_assets,
                   "doc_only": sorted(doc_assets - code_assets),
                   "code_only": sorted(code_assets - doc_assets)},
        "packf": {"doc": doc_types, "code": code_types,
                  "doc_only": sorted(doc_types - code_types),
                  "code_only": sorted(code_types - doc_types)},
        "skin": {"doc": doc_skin, "code": code_skin,
                 "doc_only": sorted(doc_skin - code_skin),
                 "code_only": sorted(code_skin - doc_skin)},
        "sections": {"doc": set(sec_doc), "code": set(sec_code),
                     "doc_only": sorted(set(sec_doc) - set(sec_code)),
                     "code_only": sorted(set(sec_code) - set(sec_doc))},
        "selftest": _k_selftest(),
    }


# ── L. 文档索引一致性（docs/README.md ⟷ docs/ 实际文件）───────────────────────
# 为什么：索引是"想找什么读哪份"的唯一入口，它自己也会漂移——第一次跑本项就抓到
# 它声称"28 份根文档 + 52 份留档"而实际是 32 + 46。索引一旦说谎，读者就会照着旧清单去读
# 已被取代的文档（本项目文档精简的全部价值就在这条链上）。
_DOC_COUNT_RE = re.compile(r"有 (\d+) 份根文档 \+ (\d+) 份历史留档")
_DOC_NAME_RE = re.compile(r"`([^`]+\.md)`")
_DOC_TEMPLATE_SKIP = ("日期.md",)   # 命名约定里的模板名（`主题-日期.md`）——它不是文件


def _doc_index_grade(idx: str, root_files: list, arch_files: list) -> dict:
    """纯函数：索引文本 + 实际文件清单 → 死链/未登记/计数 三项判定（自检直接喂样本）。"""
    listed = [n for n in _DOC_NAME_RE.findall(idx) if not any(s in n for s in _DOC_TEMPLATE_SKIP)]
    plain = {n for n in listed if "*" not in n and "/" not in n}
    globs: list = []
    for n in listed:
        if "*" in n:
            globs.append(n)
            globs.extend(p for p in n.split("/") if "*" in p)   # `热修十二/十三-*.md` 这类简写按段各试
    actual = set(root_files) | set(arch_files)

    def _registered(name: str) -> bool:
        return name in plain or any(fnmatch.fnmatch(name, g) for g in globs)

    dead = sorted(n for n in plain if n not in actual)
    unreg = sorted(n for n in root_files if n not in ("README.md",) and not _registered(n))
    m = _DOC_COUNT_RE.search(idx)
    claim = (int(m.group(1)), int(m.group(2))) if m else None
    real = (len(root_files), len(arch_files))
    return {"dead": dead, "unreg": unreg, "claim": claim, "real": real,
            "count_ok": claim == real, "n_plain": len(plain), "n_glob": len(globs)}


_L_SELFTEST_CASES = (
    # (期望死链数, 期望未登记数, 期望计数一致, 索引文本, 根文件, 存档文件)
    # 死链：索引写了 `c.md` 但文件不存在（`a.md`/`b.md` 都在）
    (1, 0, True, "有 1 份根文档 + 1 份历史留档 `a.md` `c.md`", ["a.md"], ["b.md"]),
    # 未登记：根目录多出 `c.md`，索引里没有（读者不知道它算不算权威）
    (0, 1, True, "有 2 份根文档 + 0 份历史留档 `a.md`", ["a.md", "c.md"], []),
    # 计数说谎：声称 9+9，实际 1+0
    (0, 0, False, "有 9 份根文档 + 9 份历史留档 `a.md`", ["a.md"], []),
    # 通配覆盖 + 模板名跳过（`主题-日期.md` 是命名约定示例，不是文件）
    (0, 0, True, "有 2 份根文档 + 0 份历史留档 `a.md` `热修十三-*.md` `主题-日期.md`",
     ["a.md", "热修十三-x.md"], []),
    # ★ 已知边界（刻意钉住）：斜杠简写 `A/B-*.md` **不展开** → 相关文件算未登记。
    #   索引纪律：写两条通配，别写斜杠简写（这条样本防的是"以后有人偷偷放宽"）。
    (0, 1, True, "有 2 份根文档 + 0 份历史留档 `a.md` `热修十二/十三-*.md`",
     ["a.md", "热修十三-x.md"], []),
)


def _doc_table_columns_by_heading(idx: str, heading_pat: str) -> list:
    """取某标题（正则）之后**第一张表**的数据行（表头去掉，每行 = 单元格列表）。

    取列而不是"全文找名字"，因为同一行里"权威列"和"备注列"的语义完全不同：
    §2 备注列写「`PLUGIN_SDK.md` 已退为历史」是**有意为之**的说明，把它算进权威就成了噪声。
    """
    m = re.search(heading_pat, idx, re.M)
    if not m:
        return []
    rows: list = []
    for line in idx[m.end():].splitlines():
        s = line.strip()
        if s.startswith("#"):            # 下一节 → 停
            break
        if not s.startswith("|"):
            if rows:                     # 表已结束
                break
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if set("".join(cells)) <= set("-: "):     # `|---|---|` 分隔行
            continue
        rows.append(cells)
    return rows[1:] if rows else []      # 去掉表头


# L 项第三片：**命名约定的自洽**。索引 §3.3 末尾自己写了约定：
#   `主题-日期.md` = 带日期的快照或交付记录（不追改）；**不带日期的** = 常驻文档（持续维护）。
# 于是「§3.1 常驻活文档（会被持续维护）」那一串里**出现带日期文件名**就是自相矛盾：
# 要么它其实是快照（该进 §3.3），要么它其实常驻（该去掉日期后缀）。两种解法都得人来定，
# 但"有没有矛盾"机器能判——这条直接服务"文档精简之后权威不漂移"。
# 只查这一个方向：§3.3 里出现不带日期的文件名是**正常**的（那是"退役的常驻文档"，
# 如 `PLUGIN_SDK.md`），反向查会刷出一屏噪声。
_DATED_MD_RE = re.compile(r"-\d{4}-\d{2}-\d{2}\.md$")


def _doc_index_naming_grade(idx: str) -> dict:
    """§3.1「常驻活文档」里带日期后缀的文件（违反索引自称的命名约定）。"""
    names = sorted(set(_DOC_NAME_RE.findall(_doc_section(idx, "3.1"))))
    return {"living": names, "dated_living": sorted(n for n in names if _DATED_MD_RE.search(n))}


def _doc_index_authority_grade(idx: str, exists=None) -> dict:
    """索引的**自相矛盾**：同一份文档既在 §2「唯一权威」又在 §3.3「历史留档」。

    为什么：索引自己在 §4 纪律 5 写了"**不立第二份权威**"，而"一物两处"正是本项目文档漂移的
    源头形态（改一处忘一处）。这条纪律以前只靠人记——现在靠机器，且能报出**取代目标是否还在**
    （目标改名/删掉时，"被谁取代"这句话就成了把人引向空地的路牌）。
    """
    auth_rows = _doc_table_columns_by_heading(idx, r"^#{2,4}\s*2\.[^\n]*$")
    sup_rows = _doc_table_columns_by_heading(idx, r"^#{2,4}\s*3\.3[^\n]*$")
    auth: set = set()
    for cells in auth_rows:
        if len(cells) >= 2:                       # 只取「唯一权威」列
            auth |= set(_DOC_NAME_RE.findall(cells[1]))
    sup: set = set()
    missing: list = []
    for cells in sup_rows:
        if not cells:
            continue
        sup |= set(_DOC_NAME_RE.findall(cells[0]))       # 「文档」列
        if len(cells) >= 2:                              # 「被谁取代」列里的目标必须存在
            for t in _DOC_NAME_RE.findall(cells[1]):
                if exists is not None and not exists(t):
                    missing.append(t)
    return {"auth": sorted(auth), "sup": sorted(sup), "both": sorted(auth & sup),
            "missing_targets": sorted(set(missing))}


def _l_selftest() -> str:
    bad = []
    for want_d, want_u, want_c, idx, rf, af in _L_SELFTEST_CASES:
        g = _doc_index_grade(idx, rf, af)
        if len(g["dead"]) != want_d:
            bad.append(f"死链 {want_d}→{len(g['dead'])}")
        if len(g["unreg"]) != want_u:
            bad.append(f"未登记 {want_u}→{len(g['unreg'])}")
        if g["count_ok"] != want_c:
            bad.append(f"计数 {want_c}→{g['count_ok']}")
    # 权威归属片：正例（同一份既权威又留档）/反例（备注列提到不算冲突）/取代目标缺失/表头不算数据
    _both = ("## 2. 权威归属表" + chr(10) + "| 主题 | 唯一权威 | 备注 |" + chr(10) + "|---|---|---|"
             + chr(10) + "| 甲 | **`a.md`** | |" + chr(10) + "## 3. 文档分类" + chr(10)
             + "### 3.3 历史留档" + chr(10) + "| 文档 | 被谁取代 |" + chr(10) + "|---|---|" + chr(10)
             + "| `a.md` | → `b.md` |" + chr(10) + "| `c.md` | → `zzz.md` |")
    _g = _doc_index_authority_grade(_both, exists=lambda n: n != "zzz.md")
    if _g["both"] != ["a.md"]:
        bad.append(f"权威/留档冲突样本 {_g['both']}")
    if _g["missing_targets"] != ["zzz.md"]:
        bad.append(f"取代目标缺失样本 {_g['missing_targets']}")
    if _g["auth"] != ["a.md"] or "文档" in _g["sup"]:
        bad.append(f"列切分（表头不该当数据） {_g['auth']} / {_g['sup']}")
    _note = ("## 2. 权威归属表" + chr(10) + "| 主题 | 唯一权威 | 备注 |" + chr(10) + "|---|---|---|"
             + chr(10) + "| 甲 | **`a.md`** | `b.md` 已退为历史 |" + chr(10) + "### 3.3 历史留档"
             + chr(10) + "| 文档 | 被谁取代 |" + chr(10) + "|---|---|" + chr(10) + "| `b.md` | 一次性 |")
    if _doc_index_authority_grade(_note)["both"] != []:
        bad.append("备注列提到已退为历史的文件不该算冲突（否则是噪声）")
    # 命名约定片：§3.1 常驻清单里带日期 = 矛盾；不带日期 = 放行（`-*.md` 通配与模板名不算）
    _nm1 = ("### 3.1 常驻活文档（会被持续维护）" + chr(10) + "`a.md` · `b-2026-09-11.md`" + chr(10)
            + "### 3.2 计划与规格" + chr(10))
    if _doc_index_naming_grade(_nm1)["dated_living"] != ["b-2026-09-11.md"]:
        bad.append(f"命名约定样本 {_doc_index_naming_grade(_nm1)['dated_living']}")
    _nm2 = ("### 3.1 常驻活文档" + chr(10) + "`a.md` · `b.md`" + chr(10) + "### 3.2 x" + chr(10))
    if _doc_index_naming_grade(_nm2)["dated_living"] != []:
        bad.append("不带日期应放行")
    if bad:
        return "FAIL: " + " | ".join(bad)
    return (f"PASS（{len(_L_SELFTEST_CASES)} 样本 + 7 例权威归属/命名：死链/未登记/计数/通配+模板名/"
            "权威冲突/取代目标/列切分/带日期常驻）")


def audit_doc_index() -> dict:
    """文档索引一致性（见上方 L 项说明）。索引文件缺失时明确报错，不假装通过。

    ★ 已知边界：登记用的通配只按 `fnmatch` 原样匹配，**不展开 `A/B-*.md` 这类斜杠简写**
      （索引纪律：写成两条通配）。`主题-日期.md` 是命名约定里的模板名，按模板跳过。
    """
    docs_dir = ROBOT / "docs"
    idx = _read(docs_dir / "README.md")
    if not idx.strip():
        return {"error": "读不到 docs/README.md（文档索引）", "dead": [], "unreg": [],
                "claim": None, "real": (0, 0), "count_ok": False, "selftest": "SKIP（无索引）"}
    root_files = sorted(p.name for p in docs_dir.glob("*.md"))
    arch_dir = docs_dir / "archive"
    arch_files = sorted(p.name for p in arch_dir.glob("*.md")) if arch_dir.is_dir() else []
    out = _doc_index_grade(idx, root_files, arch_files)
    # 第二片：索引自相矛盾（同一份既"唯一权威"又"历史留档"）+ 取代目标是否还在
    def _doc_exists(name: str) -> bool:
        return (docs_dir / name).exists() or (arch_dir / name).exists()

    out.update({f"auth_{k}": v for k, v in _doc_index_authority_grade(idx, _doc_exists).items()})
    out.update({f"nm_{k}": v for k, v in _doc_index_naming_grade(idx).items()})
    out["error"] = ""
    out["selftest"] = _l_selftest()
    return out


# ── M. 控制字符扫描（源码层）：TAB / C0 控制字符不该藏在文本里 ────────────────────
# 为什么要有这项（两次真实事故逼出来的）：
#   ① 2026-09-12：RELEASE-NOTES 里写了 markdown 反引号包 `type=gal`——而那是 PowerShell 的
#      **双引号 here-string**，反引号是转义符，`` `t `` 被吃成一个真 TAB，生成物成了"（<TAB>ype=gal）"。
#      语法合法→构建成功→前三项自检全过，只有人眼看输出才发现。
#   ② 更早一次：0x0B（竖制表符）冒充了路径里的字母 "v"，`grep "voice_mode"` 搜不到，靠按字节比对才定位。
# 两者共性：**语法合法、程序照跑、内容悄悄错**——正是本项目最该被机器盯住的一类。
# 规则：.py/.ps1/.md/.json/.txt 禁 TAB（缩进一律空格）+ 禁任何 C0 控制字符（\n\r 除外）；
#       .js/.css/.html 只禁非 TAB 控制字符（前端缩进可能本就用 TAB，那是它的风格，不算缺陷）。
_CTL_TAB_FORBIDDEN_EXT = (".py", ".ps1", ".md", ".json", ".txt")
_CTL_TEXT_EXT = (".py", ".ps1", ".md", ".json", ".txt", ".js", ".css", ".html", ".example")


def _scan_control_chars(paths) -> list:
    """扫一批文件 → [{"file", "ch", "line"}]；只报**第一处**（一处就够修，避免刷屏）。"""
    out = []
    for p in paths:
        ext = p.suffix.lower()
        if ext not in _CTL_TEXT_EXT:
            continue
        try:
            txt = p.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue
        for i, ch in enumerate(txt):
            code = ord(ch)
            is_tab = code == 9
            is_other = code < 32 and code not in (10, 13, 9)
            if is_other or (is_tab and ext in _CTL_TAB_FORBIDDEN_EXT):
                try:
                    label = str(p.relative_to(ROBOT))
                except ValueError:
                    label = str(p)      # 自检用的临时文件不在仓库内，标签退回绝对路径
                out.append({"file": label, "ch": code,
                            "line": txt.count(chr(10), 0, i) + 1})
                break
    return out


_CTL_SELFTEST_CASES = (
    # (期望命中数, 文件名, 内容)
    (1, "a.py", "x = 1" + chr(9) + "y = 2"),          # TAB in .py → 命中
    (0, "a.js", "var x = 1" + chr(9) + "var y = 2"),  # TAB in .js → 前端风格，放行
    (1, "a.md", "路径 voice" + chr(11) + "_mode"),    # 0x0B 竖制表符 → 命中（真实事故原型）
    (0, "a.md", "干净文本\n第二行\r\n第三行"),         # 换行不算控制字符
    (0, "a.ps1", "$x = '``t 这是转义写法，不是真 TAB'"),
)


def _m_selftest() -> str:
    import tempfile
    bad = []
    for want, name, content in _CTL_SELFTEST_CASES:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            f = d / name
            f.write_text(content, encoding="utf-8")
            got = len(_scan_control_chars([f]))
        if got != want:
            bad.append(f"{name} 期望{want}→{got}")
    if bad:
        return "FAIL: " + " | ".join(bad)
    return f"PASS（{len(_CTL_SELFTEST_CASES)} 例：TAB/0x0B/放行 JS/换行/转义写法）"


def audit_control_chars() -> dict:
    """源码层控制字符扫描（见上方 M 项说明）。"""
    paths: list = list(_iter_runtime_py())
    for sub in ("tests", "tools"):
        paths += [p for p in (QQBOT / sub).rglob("*.py") if "__pycache__" not in p.parts]
    for sub in ("launcher", "build"):
        paths += [p for p in (ROBOT / sub).glob("*.ps1")]
    for name in ("start.ps1", "stop.ps1"):
        p = ROBOT / name
        if p.is_file():
            paths.append(p)
    # docs 扫根目录（archive 是只读证据档，可能本就有历史字符，不追改）
    paths += [p for p in (ROBOT / "docs").glob("*.md")]
    return {"hits": _scan_control_chars(paths), "scanned": len(paths), "selftest": _m_selftest()}


# ── N. 超时与任务引用（两类"静默卡死 / 静默不完成"）──────────────────────────────
# 为什么：这两类都不会报错，只会"没反应"，排查时最费时间。
#   ① `subprocess.run/call/check_output/check_call` **缺 timeout=** → 子进程卡住即永久挂住调用方
#      （例：bot.py 关机路径的 taskkill 卡住 → "关闭"永远关不掉。同块上一行就有 timeout=10，
#       说明是疏忽不是决定）。`Popen` 不算——它没有 timeout 参数，生命周期由调用方自己管。
#   ② `asyncio.create_task(...)` 的返回值没保存 → asyncio 只持**弱引用**，任务可能被 GC 静默吞掉。
#      本项目已踩过并分别在 4 处自建集合兜着（brain 交接任务的注释原文："不持引用可被 GC 静默
#      吞掉（交接无声失效，仅日志缺失可判）"）。规范：一律 `core.bgtasks.spawn(...)`；
#      确有理由裸调，就在该行或上一行写 `# audit-ok: <理由>`。
_TIMEOUT_CALLS = ("subprocess.run", "subprocess.call", "subprocess.check_output", "subprocess.check_call",
                  "_sp.run", "_sp.call", "_sp.check_output", "_sp.check_call")
_TASK_CONSUMERS = ("add", "append", "update")   # `SET.add(create_task(...))` 也算持引用


def _ast_call_name(node) -> str:
    f = getattr(node, "func", None)
    if isinstance(f, ast.Attribute):
        if isinstance(f.value, ast.Name):
            return f"{f.value.id}.{f.attr}"
        if isinstance(f.value, ast.Attribute) and isinstance(f.value.value, ast.Name):
            return f"{f.value.value.id}.{f.value.attr}.{f.attr}"
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return "?"


def _scan_timeout_and_tasks(src: str) -> tuple:
    """源码 → (缺 timeout 的调用, 裸 create_task)。只报**能静态判定**的：见上方说明。"""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return [], []
    lines = src.splitlines()
    consumed: set = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and node.value is not None:
            consumed.add(id(node.value))
        elif isinstance(node, (ast.Await, ast.Return, ast.Starred)):
            if getattr(node, "value", None) is not None:
                consumed.add(id(node.value))
        elif isinstance(node, ast.Call) and _ast_call_name(node).split(".")[-1] in _TASK_CONSUMERS:
            for a in node.args:
                consumed.add(id(a))
    no_timeout, bare_tasks = [], []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _ast_call_name(node)
        if name in _TIMEOUT_CALLS and "timeout" not in {k.arg for k in node.keywords}:
            no_timeout.append((node.lineno, name, lines[node.lineno - 1].strip()[:90]))
        if name in ("create_task", "asyncio.create_task") or name.endswith(".create_task"):
            if id(node) in consumed:
                continue
            if _waived(lines, node.lineno):
                continue
            bare_tasks.append((node.lineno, lines[node.lineno - 1].strip()[:90]))
    return no_timeout, bare_tasks


def _waived(lines: list, lineno: int) -> bool:
    """该行或上一行写了 `# audit-ok:` → 视为已声明（理由必须写在旁边，照旧打印）。"""
    cur = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
    prev = lines[lineno - 2] if lineno >= 2 else ""
    return _MS_WAIVER in cur or _MS_WAIVER in prev


_N_SELFTEST_CASES = (
    # (期望缺超时数, 期望裸任务数, 期望句柄泄漏数, 源码)
    (1, 0, 0, "subprocess.run(['x'], capture_output=True)"),
    (0, 0, 0, "subprocess.run(['x'], capture_output=True, timeout=10)"),
    (0, 0, 0, "p = subprocess.Popen(['x'])"),                      # Popen 没有 timeout 参数，不该报
    (0, 1, 0, "asyncio.get_running_loop().create_task(f())"),      # 裸调 → 报
    (0, 0, 0, "t = asyncio.get_running_loop().create_task(f())"),  # 有赋值 → 放行
    (0, 0, 0, "S.add(asyncio.get_running_loop().create_task(f()))"),  # 装进集合 → 放行
    (0, 0, 0, "spawn(f())"),                                        # 公共 helper → 无 create_task
    (0, 0, 0, "# audit-ok: 这里就是要裸调（自检样本）" + chr(10) + "asyncio.get_running_loop().create_task(f())"),
    (0, 0, 1, "stdout=open('a.log', 'ab')"),                        # 未包 with → 句柄泄漏
    (0, 0, 0, "with open('a.log', 'ab') as f:" + chr(10) + "    p = 1"),   # 包 with → 放行
)


def _n_selftest() -> str:
    bad = []
    for want_t, want_b, want_lk, src in _N_SELFTEST_CASES:
        got_t, got_b = _scan_timeout_and_tasks(src)
        if len(got_t) != want_t:
            bad.append(f"缺超时 {want_t}→{len(got_t)}")
        if len(got_b) != want_b:
            bad.append(f"裸任务 {want_b}→{len(got_b)}")
        if len(_scan_open_leaks(src)) != want_lk:
            bad.append(f"句柄泄漏 {want_lk}→{len(_scan_open_leaks(src))}")
    if bad:
        return "FAIL: " + " | ".join(bad)
    return f"PASS（{len(_N_SELFTEST_CASES)} 例：缺超时/Popen/裸任务/赋值/入集合/豁免/句柄泄漏/with 放行）"


def _scan_open_leaks(src: str) -> list:
    """`open(...)` 未包在 `with` 里 → 句柄泄漏（返回行号列表）。

    ★ 本项目真实发生过两次：brain 的引擎切换每次泄漏 2 个句柄（已用 `with` 修），
      同款写法在 debug 里**漏了两年**（2026-09-12 审计补抓到：`stdout=open(...)` 直接交给 Popen，
      父进程侧永不关闭）。理论上"显式 close"也可以，但本仓风格就是 with——所以判据统一成 with。
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    inside: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.With):
            for it in node.items:
                for sub in ast.walk(it.context_expr):
                    if isinstance(sub, ast.Call):
                        inside.add(id(sub))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
            if id(node) not in inside:
                out.append(node.lineno)
    return out


def audit_timeout_and_tasks() -> dict:
    """超时、任务引用与句柄泄漏（见上方 N 项说明）。"""
    no_timeout, bare, leaks = [], [], []
    for p in _iter_runtime_py():
        rel = str(p.relative_to(ROBOT))
        if rel.replace("\\", "/").startswith("qq-bot/plugins/voice/"):
            continue          # 另会话产物：只读不改（与 D/I 项同一口径）
        src = _read(p)
        t, b = _scan_timeout_and_tasks(src)
        no_timeout += [{"file": rel, "line": ln, "call": c, "src": s} for ln, c, s in t]
        bare += [{"file": rel, "line": ln, "src": s} for ln, s in b]
        leaks += [{"file": rel, "line": ln} for ln in _scan_open_leaks(src)]
    return {"no_timeout": no_timeout, "bare_tasks": bare, "open_leaks": leaks,
            "selftest": _n_selftest()}


_ABS_PATH_RE = re.compile(r"""(?<![\w.])[A-Za-z]:[\\/](?:[^"'\s\\)]|\\\\)+""")


def audit_abs_paths() -> list[dict]:
    hits = []
    for p in _iter_runtime_py():
        for i, line in enumerate(_read(p).splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for m in _ABS_PATH_RE.finditer(line):
                s = m.group(0)
                if len(s) < 6 or s.endswith(":"):
                    continue
                hits.append({"file": str(p.relative_to(ROBOT)), "line": i,
                             "path": s, "src": line.strip()[:110]})
    return hits


_WRITE_RE = re.compile(r"""write_text\(|write_bytes\(|\.open\(\s*["'][wax]|open\([^)]*["'][wax]["']""")
_OK_WRITE_FILES = {"core/atomics.py", "plugins/webgal/__init__.py"}  # 已审计并留档的两处
_APPEND_RE = re.compile(r"""["']a["']""")
_LOG_NAME_RE = re.compile(r"LOG|TRACE|JSONL|_log|HIST", re.I)


def audit_writes() -> dict:
    """绕过 core.atomics 的写盘 —— **分开报两类，严重性相差一个数量级**：

    · 危险：**状态文件**裸写。写坏 = 状态静默清零（正是 atomics docstring 记的那类事故）。
    · 良性：**append-only 日志**（jsonl 轨迹 / trace）。单次 append 由 OS 保证原子性，
      丢了只损失一行日志而非状态；给它们套 atomics 反而更慢更绕。

    2026-09-12 改进理由：原先混报成 12 条，噪声盖住了真信号——
    工具应当把"有问题"与"已判定无问题"分开报，而不是一律 ❌。
    """
    danger, benign = [], []
    for p in _iter_runtime_py():
        rel = str(p.relative_to(QQBOT)).replace("\\", "/")
        if rel in _OK_WRITE_FILES:
            continue
        for i, line in enumerate(_read(p).splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if not _WRITE_RE.search(line):
                continue
            item = {"file": rel, "line": i, "src": line.strip()[:110]}
            (benign if _classify_write_line(line) == "benign" else danger).append(item)
    return {"danger": danger, "benign": benign, "selftest": _write_classifier_selftest()}


def _classify_write_line(line: str) -> str:
    """单行写盘 → "benign" / "danger"。

    ★ 规则（2026-09-12 第二版，第一版两类都判错过）：
      · `"a"` 追加 → **一律良性**：追加不具破坏性（既有内容不动），丢也只丢这一行。
        第一版要求"追加 **且** 名字像日志"，于是 `open(DIARY_FILE, "a")` 被误判成危险——
        日记文件名叫 DIARY 而不是 LOG，跟危险性毫无关系。
      · 覆盖写（`"w"`/`"x"`）→ 看**目标名**是否像日志/trace：像 → 良性（启动时重置 trace 是有意为之）；
        不像 → **危险**（很可能是在裸写状态文件，那正是 atomics 存在的理由）。
    """
    if _APPEND_RE.search(line):
        return "benign"
    return "benign" if _LOG_NAME_RE.search(line) else "danger"


# 自检样本：一个"永远报 0"的检查器等于在骗人，所以让分类器当场证明它**能认出坏模式**。
_DANGER_SAMPLES = (
    'with open(STATE_FILE, "w", encoding="utf-8") as f:',
    '(DATA_ROOT / name).write_text(payload, encoding="utf-8")',
    'SECRET_FILE.write_bytes(raw)',
    'special_state.json.open("w")',
)
_BENIGN_SAMPLES = (
    'with open(LIFE_LOG_FILE, "a", encoding="utf-8") as f:',
    'with open(DIARY_FILE, "a", encoding="utf-8") as f:',
    'with open(BOT_TRACE_FILE, "w", encoding="utf-8") as _f:',
)


def _write_classifier_selftest() -> str:
    bad = [s for s in _DANGER_SAMPLES if _classify_write_line(s) != "danger"]
    bad += [s for s in _BENIGN_SAMPLES if _classify_write_line(s) != "benign"]
    if bad:
        return "FAIL: " + " | ".join(bad)
    return f"PASS（负例 {len(_DANGER_SAMPLES)} 全认出，正例 {len(_BENIGN_SAMPLES)} 全放行）"


# ── D 项分级：机械可证的两种无害 vs 必须有人解释的"待查" ───────────────────────
# 清理：关句柄 / 删临时文件。失败不影响任何状态；给它们套 logger 只会淹掉真信号。
_CLEANUP_METHODS = ("remove", "unlink", "rmdir", "rmtree", "close", "shutdown")
# 观测：写日志 / 轨迹。日志失败绝不能反过来打断主流程。
_OBSERVE_METHODS = ("debug", "info", "warning", "error", "critical", "exception", "log")
# TryStar 是 3.11+ 才有的；用动态元组，免得在旧解释器上 import 就炸
_TRY_TYPES = tuple(c for c in (ast.Try, getattr(ast, "TryStar", None)) if c is not None)
_CALL_VALUE_STMTS = (ast.Expr, ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Return)
_CALL_BODY_STMTS = tuple(c for c in (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With,
                                     ast.AsyncWith, ast.Try, getattr(ast, "TryStar", None))
                         if c is not None)


def _primary_calls(st) -> set[str]:
    """一条语句"实际在调用"的方法名——**忽略参数里的调用**。

    ★ 两个坑都是踩出来的：
      ① 只取最外层调用：`_lg.getLogger("brain").info(...)` 的判定依据是 `.info`（观测），
         内层 `_lg.getLogger` 不是无害方法名，按"全部调用"判会把整条误判成待查；
      ② 忽略参数里的调用：`logger.info("x", card.get("name"))` 里的 `card.get` 属于**参数**，
         同样会把合法的观测语句拖成待查（brain 戳一戳卡片那行就是这么被误报的）。
    只顺着"接收者链"往下（`a.b().c()` → `c`），不走进参数。
    """
    out: set[str] = set()
    if isinstance(st, _CALL_VALUE_STMTS):
        cur = getattr(st, "value", None)
        while isinstance(cur, ast.Await):
            cur = cur.value
        if isinstance(cur, ast.Call):
            f = cur.func
            for name in (getattr(f, "attr", ""), getattr(f, "id", "")):
                if name:
                    out.add(name)
                    break
    elif isinstance(st, _CALL_BODY_STMTS):
        subs = list(st.body) + list(getattr(st, "orelse", [])) + list(getattr(st, "finalbody", []))
        for h in getattr(st, "handlers", []):
            subs += list(h.body)
        for sub in subs:
            out |= _primary_calls(sub)
    return out


def _classify_pass_handler(try_body: list) -> str:
    """无注释的 `except: pass` → "清理类" / "观测类" / "待查"。

    判据只看 **try 体**（handler 体只有 pass，什么都看不出）。

    ★ 刻意**不设白名单**：其余一律"待查"，必须要么改代码（例如写一行 debug 日志），
      要么把"为什么失败无害"写进 except 行或 pass 行的注释——注释本身就是这条检查要的
      线索。白名单会让"新出现的无声失败"自动变合法，那正是拿工具骗自己（C 项踩过）。
    """
    calls: set[str] = set()
    for st in try_body:
        calls |= _primary_calls(st)
    if not calls:
        return "待查"
    if all(n in _CLEANUP_METHODS for n in calls):
        return "清理类"
    if all(n in _OBSERVE_METHODS for n in calls):
        return "观测类"
    return "待查"


# 自检样本：用 chr(10) 拼行而**不写 \\n 转义**——补丁脚本里嵌转义曾在 2026-09-12
# 把本文件写坏（落盘成了字面 `def f():\\n"`，SyntaxError 卡住整个审计工具）。
_PASS_SELFTEST_CASES = (
    ("清理类", ("try:", "    os.remove(tmp)", "except OSError:", "    pass")),
    ("清理类", ("try:", "    f.close()", "except OSError:", "    pass")),
    ("观测类", ("try:", "    logger.info(\"x\")", "except Exception:", "    pass")),
    # 链式调用陷阱：外层才是判定依据（内层 getLogger 不是无害方法名）
    ("观测类", ("try:", "    _lg.getLogger(\"b\").info(\"x\")", "except Exception:", "    pass")),
    # 参数陷阱：参数里的 card.get 不算"这条语句在调用什么"（brain 戳一戳卡片行）
    ("观测类", ("try:", "    logger.info(\"x\", card.get(\"name\"))", "except Exception:", "    pass")),
    ("待查", ("try:", "    save_state(x)", "except OSError:", "    pass")),
    ("待查", ("try:", "    conn.execute(\"UPDATE t SET a=1\")", "except sqlite3.Error:", "    pass")),
    ("待查", ("try:", "    text = \"x\"", "except Exception:", "    pass")),
)


def _pass_classifier_selftest() -> str:
    """分级器自检——一个"永远报待查"或"永远报无害"的分级器都是在骗人。"""
    bad = []
    for want, snippet in _PASS_SELFTEST_CASES:
        src = chr(10).join(snippet)
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            bad.append(f"样本语法错({e.msg})")
            continue
        node = next((n for n in ast.walk(tree) if isinstance(n, ast.Try)), None)
        if node is None:
            bad.append("样本无 Try")
            continue
        got = _classify_pass_handler(node.body)
        if got != want:
            bad.append(f"{want}→{got}")
    if bad:
        return "FAIL: " + " | ".join(bad)
    return f"PASS（{len(_PASS_SELFTEST_CASES)} 样本全判对）"


_EXC_RE = re.compile(r"^(\s*)except\b[^:]*:\s*(#.*)?$")  # 保留：旧接口兼容（现已由 AST 取代）


# ── O. 本机身份外泄扫描（"发行物里带着主人的号"这类事故）────────────────────────────
# 为什么要有这项：2026-09-12 实测发现 **launcher 发行包里的 exe 含真 QQ 号 5 处**——
# ps2exe 把脚本本体（含**注释**）嵌进 exe，源码里写着"举例：SUPERUSERS=['14xxxxxx09']"就真的发出去了；
# core 包的 `harness/driver.py` 缺省回落、`plugins/brain` 的昵称缺省值同理。三处都不是"故意署名"，
# 而是**把本机身份当示例**——这类泄漏靠人眼 review 抓不住（它藏在注释和示例文案里）。
# 关键设计：**值不硬编码**，运行时从 `qq-bot/.env` 派生（`SUPERUSERS` 首个数字 = 主人号，
# `OWNER_NICKNAME` = 昵称）。于是换主人 QQ 后检查自动跟着换；报告里只印掩码，不回显原值。
# 分两面：
#   · 文本面：追踪范围内的文本文件（源码/文档/配置样例）不许出现主人号 → ❌
#   · 二进制面：`QQAI-Launcher.exe` 两个副本（**发行物本体**）不许出现主人号 → ❌
#   · 昵称只**列出**不判 ❌：署名（`# © @某某`、README 作者行）与"代码缺省值"长得一样，
#     是不是有意属人工裁决（见 `docs/遗留任务.md` A6/A7），工具只负责让它**可见**。
_ENV_IDENTITY_KEYS = ("SUPERUSERS", "OWNER_NICKNAME")


def _local_identity() -> dict:
    """从 `qq-bot/.env` 派生本机身份（读不到就给空串，由调用方当 SKIP 处理，不静默通过）。"""
    txt = _read(QQBOT / ".env")
    out = {"qq": "", "nick": ""}
    if not txt:
        return out
    m = re.search(r"SUPERUSERS\s*=\s*\[?\"?'?(\d{5,12})", txt)
    if m:
        out["qq"] = m.group(1)
    m = re.search(r"OWNER_NICKNAME\s*=\s*\"?([^\"\r\n]+)", txt)
    if m:
        out["nick"] = m.group(1).strip()
    return out


def _mask(secret: str) -> str:
    """报告里只印掩码（本工具的 stdout 会被贴进对话/文档，原值不能回显）。"""
    if len(secret) <= 4:
        return "*" * len(secret)
    return secret[:2] + "*" * (len(secret) - 4) + secret[-2:]


def _identity_text_paths() -> list:
    """文本面扫描范围：运行时 py + tests/tools + 全部 .ps1 + 配置样例 + docs 根 + 根文件。

    刻意**不含** `vault/`（私有隔离区）、`data/`（运行时状态）、`tools/`（第三方）、
    `.venv/`、`release/`（可再生产物）：它们在公开面上不存在，扫了只会刷噪声。
    """
    paths: list = list(_iter_runtime_py())
    for sub in ("tests", "tools"):
        paths += [p for p in (QQBOT / sub).rglob("*.py") if "__pycache__" not in p.parts]
    for sub in ("launcher", "build"):
        paths += [p for p in (ROBOT / sub).rglob("*.ps1")]
        paths += [p for p in (ROBOT / sub).rglob("*.md")]
        paths += [p for p in (ROBOT / sub).rglob("*.js")]
        paths += [p for p in (ROBOT / sub).rglob("*.html")]
    # `build/staging/` 是**上次构建的暂存副本**（gitignored + 每次重出覆盖）→ 扫它只会重复报同一处；
    # zip 内部的检查由 `docs/公开发布检查单.md` 的"抽包复核"那一步负责（它解包后再 grep）
    paths = [p for p in paths if "staging" not in p.parts]
    for name in ("start.ps1", "stop.ps1", "README.md", ".gitignore", ".env.example"):
        p = ROBOT / name
        if p.is_file():
            paths.append(p)
    paths += [p for p in (ROBOT / "docs").glob("*.md")]
    paths.append(QQBOT / ".env.example")
    return [p for p in paths if p.is_file()]


def _scan_secret_hits(paths, needle: str, exts: tuple) -> list:
    """文本文件里出现的 needle → [{"file", "lines": [...]}]。**不返回命中行文本**（防原值进日志）。

    报**每一处**而不是每文件一处：这张清单的用途是"按名单修"，只给首处会漏改。
    """
    out = []
    if not needle:
        return out
    for p in paths:
        if exts and p.suffix.lower() not in exts:
            continue
        try:
            txt = p.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue
        lines = [txt.count(chr(10), 0, m.start()) + 1 for m in re.finditer(re.escape(needle), txt)]
        if lines:
            try:
                label = str(p.relative_to(ROBOT))
            except ValueError:
                label = str(p)
            out.append({"file": label, "lines": lines})
    return out


def _scan_secret_hits_binary(paths, needle: str) -> list:
    """二进制（exe）里出现的 needle。ps2exe 把脚本本体嵌进 exe，注释里的号码会**真的发出去**。"""
    out = []
    if not needle:
        return out
    for p in paths:
        if not p.is_file():
            continue
        try:
            raw = p.read_bytes()
        except OSError:
            continue
        try:
            label = str(p.relative_to(ROBOT))
        except ValueError:
            label = str(p)
        n = raw.count(needle.encode("latin-1", "ignore"))
        if n:
            out.append({"file": label, "hits": n})
    return out


def _identity_selftest() -> str:
    """自检：喂**假**身份证明正例（文本/二进制）与反例（不该命中的后缀/不存在的值）。"""
    import tempfile
    bad = []
    fake = "19999999999"
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "a.py").write_text("x = '" + fake + "'" + chr(10) + "# " + fake + chr(10), encoding="utf-8")
        (d / "b.png").write_bytes(fake.encode())
        (d / "c.exe").write_bytes(b"pre" + fake.encode() + b"post" + fake.encode())
        (d / "d.md").write_text("干净文本", encoding="utf-8")
        txt_hits = _scan_secret_hits([d / "a.py", d / "b.png", d / "d.md"], fake, (".py", ".md"))
        if len(txt_hits) != 1 or txt_hits[0]["lines"] != [1, 2]:
            bad.append(f"文本面命中 {txt_hits}")
        bin_hits = _scan_secret_hits_binary([d / "c.exe", d / "d.md"], fake)
        if len(bin_hits) != 1 or bin_hits[0]["hits"] != 2:
            bad.append(f"二进制面命中 {bin_hits}")
        if _scan_secret_hits([d / "a.py"], "", (".py",)) != []:
            bad.append("空 needle 必须不命中（防'没有身份时全量误报'）")
        if _mask(fake) != ("19" + "*" * (len(fake) - 4) + "99"):
            bad.append(f"掩码不对：{_mask(fake)}")
    if bad:
        return "FAIL: " + " | ".join(bad)
    return "PASS（5 例：文本命中/后缀过滤/二进制多命中/空值不误报/掩码）"


def audit_local_identity() -> dict:
    """本机身份外泄扫描（见上方 O 项说明）。`.env` 读不到时明确 SKIP，不假装通过。"""
    ident = _local_identity()
    if not ident["qq"]:
        return {"skipped": "读不到 qq-bot/.env 的 SUPERUSERS（第三方环境正常）——本项 SKIP",
                "qq_masked": "", "nick_masked": "", "text_hits": [], "bin_hits": [],
                "nick_hits": [], "scanned": 0, "selftest": _identity_selftest()}
    paths = _identity_text_paths()
    text_hits = _scan_secret_hits(paths, ident["qq"], ())
    bin_hits = _scan_secret_hits_binary([ROBOT / "QQAI-Launcher.exe",
                                         ROBOT / "launcher" / "QQAI-Launcher.exe"], ident["qq"])
    nick_hits = _scan_secret_hits(paths, ident["nick"], ()) if ident["nick"] else []
    return {"skipped": "", "qq_masked": _mask(ident["qq"]), "nick_masked": _mask(ident["nick"]),
            "text_hits": text_hits, "bin_hits": bin_hits, "nick_hits": nick_hits,
            "scanned": len(paths), "selftest": _identity_selftest()}


def _plugin_reg_grade(declared: set, dirs: list) -> list:
    """目录未注册清单（纯函数，供自检）。"""
    return [d for d in dirs if d not in declared]


def _plugin_reg_selftest() -> str:
    a = _plugin_reg_grade({"brain", "webgal"}, ["brain", "webgal"])
    b = _plugin_reg_grade({"brain"}, ["brain", "livesource"])
    c = _plugin_reg_grade(set(), [])
    ok = (a == [] and b == ["livesource"] and c == [])
    return "PASS" if ok else f"FAIL a={a} b={b} c={c}"


def _inject_no_mark(dirs: list) -> list:
    """合成事件注入点缺"合成轮标记"的文件（纯函数，供自检）。

    判据：文件里出现 `handle_event(` ⇒ 必须有 `set_synthetic_round(` 或 `_SYNTHETIC[`。
    理由（红线）：合成通道（GAL 页 / Telegram）跑管线时必须置位，否则下游（brain._typing_on、
    头像、输入状态）会去动**真实 QQ 账号**——webgal 计划表 §五#4。
    """
    out = []
    for p in _iter_runtime_py():
        txt = _read(p)
        if "handle_event(" not in txt:
            continue
        if "set_synthetic_round(" not in txt and "_SYNTHETIC[" not in txt:
            out.append(str(p.relative_to(QQBOT)).replace("\\", "/"))
    return out


def _inject_mark_selftest() -> str:
    """自检：三类样本文件的判据真值（有注入无标记=报；有标记=不报；无注入=不看）。"""
    samples = (("handle_event(a, b)\n", True),
               ("handle_event(a, b)\nset_synthetic_round(True)\n", False),
               ("_SYNTHETIC['on'] = True\nhandle_event(a, b)\n", False),
               ("x = 1\n", False))
    bad = []
    for src, expect in samples:
        got = ("handle_event(" in src) and ("set_synthetic_round(" not in src
                                           and "_SYNTHETIC[" not in src)
        if got != expect:
            bad.append(src.strip()[:28])
    return "PASS" if not bad else f"FAIL {bad}"


def audit_plugin_registration() -> dict:
    """插件注册与合成通道红线（2026-09-12 新增；livesource 教训的通用化）。

    病根：`plugins/xxx/` 目录存在 **≠** 被加载。加载清单在 pyproject.toml 的
    `[tool.nonebot].plugins`；漏一行 = 整个插件**静默不生效**——livesource 曾经就是：
    启动器写了 .env 开关、文档也写了，全链没有任何消费者，且**零报错**（人工检查漏过一次）。
    故做成机器判据：目录 ⇒ 必须在清单里；清单里的幽灵项也要报。
    """
    src = _read(QQBOT / "pyproject.toml")
    m = re.search(r"\[tool\.nonebot\](.*?)(?=\n\[|\Z)", src, re.S)
    block = m.group(1) if m else ""
    declared = set(re.findall(r"""["']plugins\.([A-Za-z_][A-Za-z0-9_]*)["']""", block))
    pdir = QQBOT / "plugins"
    dirs = sorted(p.name for p in pdir.iterdir()
                  if p.is_dir() and p.name != "__pycache__" and (p / "__init__.py").is_file())
    return {
        "dirs": dirs,
        "declared": sorted(declared),
        "unreg": _plugin_reg_grade(declared, dirs),
        "ghost": sorted(d for d in declared if not (pdir / d).is_dir()),
        "inject_no_mark": _inject_no_mark(dirs),
        "selftest": f"{_plugin_reg_selftest()} / {_inject_mark_selftest()}",
    }


def main() -> int:
    report = {
        "env": audit_env(),
        "abs_paths": audit_abs_paths(),
        "writes": audit_writes(),
        "ast": audit_ast(),
        "bom": audit_bom(),
        "deps_doc": audit_deps_doc(),
        "silent_write": audit_silent_write(),
        "multisource": audit_multisource(),
        "data_root": audit_data_root(),
        "launcher": audit_launcher_refs(),
        "doc_contract": audit_doc_contract(),
        "doc_index": audit_doc_index(),
        "control_chars": audit_control_chars(),
        "timeout_tasks": audit_timeout_and_tasks(),
        "local_identity": audit_local_identity(),
        "plugin_reg": audit_plugin_registration(),
    }
    if "--json" in sys.argv:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0

    e = report["env"]
    print("=" * 68)
    print("A. env 漂移（对每个键查两种消费方式：os.environ 大写 / nonebot config 小写属性）")
    print(f"   os.environ 直读 {len(e['env_used'])} 键 / .env.example {len(e['example_keys'])} 键 / .env {len(e['env_keys'])} 键")
    if e["missing_in_example"]:
        print("   ❌ 代码直读但样例没写（用户无从发现）：")
        for k in e["missing_in_example"]:
            print(f"      {k}  ← {', '.join(e['env_used'][k][:3])}")
    else:
        print("   ✅ 无：代码直读的键样例都写了")
    if e["dead_in_example"]:
        print("   ⚠️  .env.example 写了但代码完全不消费（死配置）：")
        for k in e["dead_in_example"]:
            print(f"      {k}")
    else:
        print("   ✅ 无死配置")
    if e["in_env_not_consumed"]:
        print("   ⚠️  .env 实际有但代码不消费：")
        for k in e["in_env_not_consumed"]:
            print(f"      {k}")

    print("=" * 68)
    print(f"B. 硬编码绝对路径：{len(report['abs_paths'])} 处（含注释）")
    seen = set()
    for h in report["abs_paths"]:
        key = (h["file"], h["path"])
        if key in seen or h["file"].split("\\")[-1] in ("paths.py",):
            continue
        seen.add(key)
        print(f"   {h['file']}:{h['line']}  {h['path']}")

    print("=" * 68)
    _w = report["writes"]
    print("C. 绕过 core.atomics 的写盘")
    print(f"   {'✅' if not _w['danger'] else '❌'} 危险（状态文件裸写）：{len(_w['danger'])} 处")
    for h in _w["danger"]:
        print(f"      {h['file']}:{h['line']}  {h['src']}")
    print(f"   ✅ 良性（append-only 日志）：{len(_w['benign'])} 处（原子性由 OS 保证，丢失只损失一行）")
    _st = _w.get("selftest", "—")
    print(f"   {'✅' if str(_st).startswith('PASS') else '❌'} 分类器自检：{_st}")

    a = report["ast"]
    print("=" * 68)
    print("D. AST 精确检查")
    for label, key in (("裸 except", "bare_except"),
                       ("可变默认参数", "mutable_defaults"),
                       ("async 内阻塞调用", "blocking_in_async")):
        items = a[key]
        flag = "❌" if items else "✅"
        print(f"   {flag} {label}：{len(items)} 处")
        for h in items[:20]:
            print(f"        {h['file']}:{h['line']}  {h['src']}")
        if len(items) > 20:
            print(f"        … 另有 {len(items) - 20} 处")

    ep = a["except_pass"]
    todo = ep["待查"]
    total = len(todo) + len(ep["清理类"]) + len(ep["观测类"]) + len(ep["另会话产物"])
    print(f"   {'✅' if not todo else '❌'} except: pass 且无注释：{total} 处"
          f"（待查 {len(todo)} ｜ 清理类 {len(ep['清理类'])} ｜ 观测类 {len(ep['观测类'])}"
          f" ｜ 另会话产物 {len(ep['另会话产物'])}）")
    for h in todo[:20]:
        print(f"        ❌ {h['file']}:{h['line']}  {h['src']}")
    if len(todo) > 20:
        print(f"        … 另有 {len(todo) - 20} 处")
    if not todo:
        print("       （判据：待查 = 必须改代码写日志、或把\"为什么失败无害\"注释在 except/pass 行上；"
              "分级只看 try 体实际调用什么，不看 handler 的 pass）")
    if ep["另会话产物"]:
        print("       ⏭ 另会话产物（plugins/voice/**，本会话不改；计 6 处已记入 docs/遗留任务.md）：")
        for h in ep["另会话产物"][:8]:
            print(f"          {h['file']}:{h['line']}  [{h['kind']}] {h['src']}")
    _epst = ep.get("selftest", "—")
    print(f"   {'✅' if str(_epst).startswith('PASS') else '❌'} 分级器自检：{_epst}")

    print("=" * 68)
    print(f"F. BOM 漂移（与 git HEAD 比对全部追踪文件）：{len(report['bom'])} 处")
    for h in report["bom"]:
        print(f"   {h['file']}  [{h['kind']}]  {h['src']}")
    if not report["bom"]:
        print("   ✅ 与 HEAD 一致（PS 脚本无 BOM 丢失）")

    print("=" * 68)
    print(f"H. 真·静默写失败：{len(report.get('silent_write') or [])} 处（白名单外应为 0）")
    for h in (report.get("silent_write") or []):
        print(f"   ❌ {h['file']}:{h['line']}  [{h['src']}]")
    if not (report.get("silent_write") or []):
        print("   ✅ 无：写盘失败不会被无声吞掉")

    print("=" * 68)
    print("G. 依赖矩阵一致性（launcher/deps.json ⟷ docs/部署指南.md）")
    dd = report.get("deps_doc") or {}
    print(f"   {'✅' if dd.get('ok') else '❌'} {dd.get('detail', '—')}")

    print("=" * 68)
    print("E. 多源常量（Python 侧必须单一来源；PS 侧跨语言只能对齐值 → smoke §36 守护）")
    _ms = report["multisource"]
    _nd = _ms["needles"] if isinstance(_ms, dict) and "needles" in _ms else _ms
    _scatter = sum(len(v.get("散落", [])) for v in _nd.values())
    _dupdef = []
    for label, v in _nd.items():
        _files = {s.split(":")[0] for s in v.get("定义", [])}
        if len(_files) > 1:
            _dupdef.append(f"{label}（{len(_files)} 个文件定义）")
    print(f"   {'✅' if not _scatter else '❌'} Python 侧散落字面量：{_scatter} 处")
    for label, v in _nd.items():
        for s in v.get("散落", []):
            print(f"      ❌ {label} → {s}")
    print(f"   {'✅' if not _dupdef else '❌'} 重复定义（同一常量在多个文件各定义一份）："
          f"{'无' if not _dupdef else ' / '.join(_dupdef)}")
    for label, v in _nd.items():
        # 同一文件内多处以"定义"出现不算问题（那正是唯一来源文件本身）；
        # 只有**跨文件**重复定义 + 散落字面量才判 ❌
        _files = {s.split(":")[0] for s in v.get("定义", [])}
        _quiet = len(_files) <= 1 and not v.get("散落")
        _line = (f"   {'✅' if _quiet else '❌'} {label}：定义 {len(v.get('定义', []))}"
                 f" ｜ 注释 {len(v.get('注释', []))} ｜ 文档串 {len(v.get('文档串', []))}"
                 f" ｜ 已声明 {len(v.get('已声明', []))} ｜ 另会话 {len(v.get('另会话产物', []))}"
                 f" ｜ PS 侧 {len(v.get('PS侧', []))}（跨语言）")
        print(_line)
        if v.get("定义"):
            print(f"        定义处：{', '.join(v['定义'])}")
    _msst = _ms.get("selftest", "—") if isinstance(_ms, dict) else "—"
    print(f"   {'✅' if str(_msst).startswith('PASS') else '❌'} 分级器自检：{_msst}")

    print("=" * 68)
    print("I. 数据根推导（规范根 core/paths.DATA_ROOT=状态 ｜ 内容根 qq-bot/data=随仓内容）")
    _dr = report["data_root"]
    print(f"   {'✅' if not _dr['规范根'] else '❌'} 规范根：{len(_dr['规范根'])} 处"
          f"（2026-09-12 起规范根**一律用 core.paths.DATA_ROOT**——见下）")
    if _dr["规范根"]:
        print("       ❌ 这些站点解析到规范根却还在自己算 parents[N]：改成 DATA_ROOT"
              "（N 取决于文件自身深度，档案里真出过'parents[3] 指错 → 场景库静默为空'的 bug）")
    for loc in _dr["规范根"][:8]:
        print(f"        ❌ {loc}")
    if len(_dr["规范根"]) > 8:
        print(f"        … 另有 {len(_dr['规范根']) - 8} 处")
    print(f"   ✅ 内容根·已声明：{len(_dr['已声明'])} 处（同行/上一行有注释说明为何走内容根）")
    for loc in _dr["已声明"]:
        print(f"        {loc}")
    if _dr["豁免"]:
        print(f"   ⏭ 豁免（`{_MS_WAIVER}` 已写理由，理由照印不隐藏）：{len(_dr['豁免'])} 处")
        for loc in _dr["豁免"]:
            print(f"        {loc}")
    print(f"   {'✅' if not _dr['待声明'] else '❌'} 内容根·未声明：{len(_dr['待声明'])} 处"
          f"（状态应走 core.paths.data_path；内容应写一行注释说明）")
    for loc in _dr["待声明"]:
        print(f"        ❌ {loc}")
    print(f"   {'✅' if not _dr['双份(分裂)'] else '❌'} 同一文件两个根都有（读哪个只取决于这一行）："
          f"{len(_dr['双份(分裂)'])} 处")
    for loc in _dr["双份(分裂)"]:
        print(f"        ❌ {loc}")
    if _dr["其它根"]:
        print(f"   ❌ 解析到预期之外的根：{len(_dr['其它根'])} 处")
        for loc in _dr["其它根"]:
            print(f"        ❌ {loc}")
    _ist = _dr.get("selftest", "—")
    print(f"   {'✅' if str(_ist).startswith('PASS') else '❌'} 自检：{_ist}")

    print("=" * 68)
    print("J. 启动器引用完整性（XAML x:Name 是唯一事实；拼错名=静默什么都不做）")
    _lc = report["launcher"]
    if _lc.get("error"):
        print(f"   ❌ {_lc['error']}")
    else:
        print(f"   声明控件 {len(_lc['declared'])} 个 ｜ 静态引用 {_lc['refs']} 处"
              f" ｜ 动态引用 {_lc['dynamic']} 处（按变量取名，静态核对不了，不判错）")
        print(f"   {'✅' if not _lc['dangling'] else '❌'} 悬空引用（引用了未声明的控件名）："
              f"{len(_lc['dangling'])} 处")
        for h in _lc["dangling"]:
            print(f"        ❌ {h['kind']} \"{h['name']}\"  （行 {h['line']}）")
        print(f"   {'✅' if not _lc['orphan'] else '⚠️ '} 声明但源码里从未引用（僵尸名）："
              f"{len(_lc['orphan'])} 个")
        for n in _lc["orphan"][:10]:
            print(f"        {n}")
        print(f"   {'✅' if not _lc['noblock'] else '❌'} 只写名字没给处理器的 BindClick"
              f"（按钮静默无效）：{len(_lc['noblock'])} 处")
        for h in _lc["noblock"]:
            print(f"        ❌ {h['name']}  （行 {h['line']}）——补上 `({ ... })` 处理器")
        print(f"   {'✅' if not _lc['missing_in_usage'] else '❌'} 用法串一致性"
              f"（实现 {len(_lc['eq_modes'])} 个 -Mode 分支：{'/'.join(_lc['eq_modes'])}）："
              f"{'一致' if not _lc['missing_in_usage'] else '漏写 ' + ','.join(_lc['missing_in_usage'])}")
    _lst = _lc.get("selftest", "—")
    print(f"   {'✅' if str(_lst).startswith('PASS') else '❌'} 分级器自检：{_lst}")
    _u, _h, _nd = _lc.get("pg_unknown") or [], _lc.get("pg_unhighlighted") or [], \
        _lc.get("pg_nav_dangling") or []
    print(f"   页面闭包：{len(_lc.get('pg_pages') or [])} 个页面容器 ｜ "
          f"{len(_lc.get('pg_args') or [])} 个静态页面实参 ｜ 导航映射 {len(_lc.get('pg_navmap') or [])} 项")
    print(f"   {'✅' if not _u else '❌'} Show-Page 实参都有对应页面容器"
          f"（拼错 = 所有页面 Collapsed = **整个窗口空白且零报错**）：{len(_u)} 个未闭合")
    for n in _u:
        print(f"        ❌ 没有对应页面：{n}")
    print(f"   {'✅' if not _h else '❌'} 导航映射覆盖全部可达页面"
          f"（缺键 = 导航项不高亮，静默）：{len(_h)} 个缺键")
    for n in _h:
        print(f"        ❌ `$map` 缺键：{n}")
    print(f"   {'✅' if not _nd else '❌'} 导航映射的值是已声明控件"
          f"（经 FindName($map[$k]) 取，属动态引用，第一片不判）：{len(_nd)} 个悬空")
    for n in _nd:
        print(f"        ❌ `$map` 指向未声明控件：{n}")
    _pst = _lc.get("pages_selftest", "—")
    print(f"   {'✅' if str(_pst).startswith('PASS') else '❌'} 页面闭包自检：{_pst}")
    # J 第三片：框架自带主页（web/index.html + web/app.js）的元素 id 闭包
    if _lc.get("web_error"):
        print(f"   ❌ 皮肤网页闭包：{_lc['web_error']}")
    else:
        _wm = _lc.get("web_missing") or []
        _wd = _lc.get("web_dead_btn") or []
        print(f"   皮肤网页闭包（框架自带 generic 样板 web/index.html + app.js）："
              f"声明 id {len(_lc.get('web_declared') or [])} 个 ｜ app.js 引用 "
              f"{len(_lc.get('web_used') or [])} 个")
        print(f"   {'✅' if not _wm else '❌'} app.js 引用的 id 都在 HTML 里"
              f"（否则是往空气里更新状态）：{len(_wm)} 个缺失")
        for n in _wm:
            print(f"        ❌ HTML 里没有：{n}")
        print(f"   {'✅' if not _wd else '❌'} HTML 的 btn-* 都有人绑"
              f"（点了没反应；反向则是删除按钮后 JS 仍在绑 = 顶层抛错 = 整页绑定与轮询全死）："
              f"{len(_wd)} 个无人绑定")
        for n in _wd:
            print(f"        ❌ 无人绑定：{n}")
    _wst = _lc.get("web_selftest", "—")
    print(f"   {'✅' if str(_wst).startswith('PASS') else '❌'} 皮肤网页闭包自检：{_wst}")
    # J 第四片：界面文案表覆盖面（全局语言切换）
    _uu = _lc.get("ui_untranslated") or []
    _ue = _lc.get("ui_empty_value") or []
    _uk = _lc.get("ui_non_chinese_key") or []
    print(f"   界面文案表：XAML 中文字面量 {len(_lc.get('ui_xaml_literals') or [])} 条 ｜ 表键 {len(_lc.get('ui_table_keys') or [])} 条")
    print(f"   {'✅' if not _uu else '❌'} 每个 XAML 界面文案都在语言表里"
          f"（漏翻不会报错，只会在英文界面上留中文）：{len(_uu)} 条未覆盖")
    for n in _uu:
        print(f"        ❌ 未覆盖：{n[:60]}")
    print(f"   {'✅' if not _ue else '❌'} 语言表的值都不为空（空值=切过去变空白按钮）：{len(_ue)} 条")
    for n in _ue:
        print(f"        ❌ 空值：{n[:60]}")
    print(f"   {'✅' if not _uk else '❌'} 语言表的键都是中文原文（键写成英文=切回中文失败）：{len(_uk)} 条")
    for n in _uk:
        print(f"        ❌ 非中文键：{n[:60]}")
    _ust = _lc.get("ui_selftest", "—")
    print(f"   {'✅' if str(_ust).startswith('PASS') else '❌'} 界面文案表自检：{_ust}")

    print("=" * 68)
    print("K. 文档契约（docs/接口文档.md ⟷ 代码）：文档承诺的路由/字段必须真存在")
    _dc = report["doc_contract"]
    print(f"   代码注册 {_dc['n_code']} 条路由 ｜ 文档声明 {_dc['n_doc']} 条")
    print(f"   {'✅' if not _dc['doc_only'] else '❌'} 文档有、代码无（**合同说谎**，"
          f"照它写会 404）：{len(_dc['doc_only'])} 条")
    for r in _dc["doc_only"]:
        print(f"        ❌ {r}")
    print(f"   {'✅' if not _dc['code_only'] else '⚠️ '} 代码有、文档无（别人无从知道）："
          f"{len(_dc['code_only'])} 条")
    for r in _dc["code_only"]:
        print(f"        {r}")
    if not _dc["keys_found"]:
        print("   ❌ 文档里找不到\"内容索引：`{…}`\"那句 → 键清单无法核对"
              "（别改成别的写法，K 项靠这句取键）")
    elif not _dc["code_keys"]:
        print("   ❌ 从 core/packs.py 取不到载荷键（`_build_content()` 改名/改结构了？）"
              "—— 取空当失败，不静默通过")
    else:
        _bad = len(_dc["keys_missing"]) + len(_dc["keys_extra"])
        print(f"   {'✅' if not _bad else '❌'} /gal/content.json 顶层键：代码 "
              f"{'/'.join(_dc['code_keys'])} ⟷ 文档 {'/'.join(_dc['doc_keys'])}")
        for k in _dc["keys_missing"]:
            print(f"        ❌ 代码有、文档没写：{k}")
        for k in _dc["keys_extra"]:
            print(f"        ❌ 文档写了、代码没有：{k}")
    _kst = _dc.get("selftest", "—")
    print(f"   {'✅' if str(_kst).startswith('PASS') else '❌'} 分级器自检：{_kst}")
    _mp = _dc.get("missing_paths") or []
    print(f"   {'✅' if not _mp else '❌'} 权威文档里带仓库前缀的路径（{_dc.get('path_hits', 0)} 处引用）："
          f"{'全部存在' if not _mp else str(len(_mp)) + ' 处不存在'}")
    for x in _mp:
        print(f"        ❌ {x}")
    # 字段级：命令通道与素材口（都能取到精确全集，双向都报）
    for _key, _label, _hint in (("cmds", "§4.4 命令通道 `cmd`",
                                 "皮肤按钮发这个名字 = 什么都不发生"),
                                ("assets", "§5.3 素材口 `ASSETS` 键",
                                 "写错 = 素材静默不生效（有渐变兜底，肉眼看不出）"),
                                ("packf", "§3.1 内容包 `type` 取值集合",
                                 "文档多写 = 作者造出永不生效的包；「未知 type 跳过不炸」是**静默跳过**"),
                                ("skin", "§4.1 + 皮肤包规范 §2 皮肤 `manifest.json` 字段",
                                 "皮肤作者照它写 = 不生效"),
                                ("sections", "`维护手册.md` §7.3 审计表段清单",
                                 "手册登记了但工具没有 = 读者会去找一个不存在的检查")):
        _c = _dc.get(_key) or {}
        _cd, _cc = _c.get("doc") or set(), _c.get("code") or set()
        if not _cd or not _cc:
            print(f"   ❌ {_label}：取不到（文档小节号改了？代码改名了？）——"
                  f"文档 {len(_cd)} 个 / 代码 {len(_cc)} 个；取空当失败，不静默通过")
            continue
        _cdo, _cco = _c.get("doc_only") or [], _c.get("code_only") or []
        print(f"   {'✅' if not _cdo and not _cco else '❌'} {_label}：代码 {len(_cc)} 个 ⟷ "
              f"文档 {len(_cd)} 个{'（一致）' if not _cdo and not _cco else ''}")
        for n in _cdo:
            print(f"        ❌ 文档写了、代码没有（{_hint}）：{n}")
        for n in _cco:
            print(f"        ❌ 代码有、文档无（第三方无从知道）：{n}")

    print("=" * 68)
    print("L. 文档索引一致性（docs/README.md ⟷ 实际文件）：索引本身也会漂移")
    _di = report["doc_index"]
    if _di.get("error"):
        print(f"   ❌ {_di['error']}")
    else:
        print(f"   索引登记 {_di['n_plain']} 个文件名 + {_di['n_glob']} 条通配 ｜ "
              f"实际 {_di['real'][0]} 份根文档 + {_di['real'][1]} 份留档")
        print(f"   {'✅' if not _di['dead'] else '❌'} 索引有、文件不存在（死链）：{len(_di['dead'])} 个")
        for n in _di["dead"]:
            print(f"        ❌ {n}")
        _ab, _am = _di.get("auth_both") or [], _di.get("auth_missing_targets") or []
        print(f"   {'✅' if not _ab else '❌'} 同一份文档不许既\"唯一权威\"又\"历史留档\""
              f"（索引 §4 纪律 5「不立第二份权威」）：{len(_di.get('auth_auth') or [])} 份权威 ／ "
              f"{len(_di.get('auth_sup') or [])} 份留档 ／ 冲突 {len(_ab)} 份")
        for n in _ab:
            print(f"        ❌ 两处都登记：{n}（权威与留档只能居其一）")
        print(f"   {'✅' if not _am else '❌'} 取代目标是否还在（"
              f"{'\"被谁取代\"指的文件必须真实存在，否则是把人引向空地的路牌' if not _am else '否则是把人引向空地的路牌'}）："
              f"{len(_am)} 个不存在")
        for n in _am:
            print(f"        ❌ 取代目标不存在：{n}")
        _dl = _di.get("nm_dated_living") or []
        print(f"   {'✅' if not _dl else '❌'} 命名约定（§3.3 末自称）：常驻活文档不许带日期后缀"
              f"（{len(_di.get('nm_living') or [])} 份常驻中 {len(_dl)} 份带日期）")
        for n in _dl:
            print(f"        ❌ 被列为\"常驻\"却带日期：{n}（要么改名去掉日期，要么移进 §3.3 历史留档）")
        print(f"   {'✅' if not _di['unreg'] else '❌'} 文件有、索引未登记（读者不知道它算不算权威）："
              f"{len(_di['unreg'])} 个")
        for n in _di["unreg"]:
            print(f"        ❌ {n}")
        _c = _di["claim"]
        print(f"   {'✅' if _di['count_ok'] else '❌'} 索引声称的份数："
              f"{'%d 份根文档 + %d 份留档' % _c if _c else '（找不到那句话）'}"
              f"{'' if _di['count_ok'] else '  ← 实际是 %d + %d，请改索引' % _di['real']}")
    _lst2 = _di.get("selftest", "—")
    print(f"   {'✅' if str(_lst2).startswith('PASS') else '❌'} 分级器自检：{_lst2}")

    print("=" * 68)
    print("M. 控制字符扫描（TAB 与 C0 控制字符：语法合法但内容会悄悄错）")
    _cc = report["control_chars"]
    print(f"   扫描 {_cc['scanned']} 个文本文件（运行时 py / tests / tools / *.ps1 / docs 根目录）")
    print(f"   {'✅' if not _cc['hits'] else '❌'} 命中：{len(_cc['hits'])} 个文件")
    for h in _cc["hits"]:
        print(f"        ❌ {h['file']}:{h['line']}  0x{h['ch']:02X}"
              f"{'（TAB）' if h['ch'] == 9 else ''}")
    if not _cc["hits"]:
        print("       （判据：.py/.ps1/.md/.json/.txt 禁 TAB 与 C0；.js/.css/.html 只禁非 TAB 的 C0）")
    _mst = _cc.get("selftest", "—")
    print(f"   {'✅' if str(_mst).startswith('PASS') else '❌'} 分级器自检：{_mst}")

    print("=" * 68)
    print("N. 超时与任务引用（两类静默：卡死 / 不完成）")
    _nt = report["timeout_tasks"]
    print(f"   {'✅' if not _nt['no_timeout'] else '❌'} subprocess 调用缺 timeout=："
          f"{len(_nt['no_timeout'])} 处（子进程卡住即永久挂住调用方；Popen 不算——它没有该参数）")
    for h in _nt["no_timeout"]:
        print(f"        ❌ {h['file']}:{h['line']}  {h['call']}  | {h['src']}")
    print(f"   {'✅' if not _nt['bare_tasks'] else '❌'} 裸 create_task（返回值没保存，可能被 GC 吞掉）："
          f"{len(_nt['bare_tasks'])} 处（规范：用 core.bgtasks.spawn；要裸调就写 `# audit-ok: <理由>`）")
    for h in _nt["bare_tasks"]:
        print(f"        ❌ {h['file']}:{h['line']}  {h['src']}")
    print(f"   {'✅' if not _nt['open_leaks'] else '❌'} `open()` 未包在 with 里（父进程侧句柄泄漏）："
          f"{len(_nt['open_leaks'])} 处（`plugins/voice/**` 属另会话产物，按 D/I 项口径跳过）")
    for h in _nt["open_leaks"]:
        print(f"        ❌ {h['file']}:{h['line']}")
    _nst = _nt.get("selftest", "—")
    print(f"   {'✅' if str(_nst).startswith('PASS') else '❌'} 分级器自检：{_nst}")

    print("=" * 68)
    print("O. 本机身份外泄扫描（发行物里带着主人的号 = 打包时谁也看不见的那种错）")
    _li = report["local_identity"]
    if _li["skipped"]:
        print(f"   ⏭ {_li['skipped']}")
    else:
        print(f"   身份来源：qq-bot/.env（号 {_li['qq_masked']} ／ 昵称 {_li['nick_masked']}）"
              f" ｜ 扫 {_li['scanned']} 个文本文件 + 2 个 exe 副本")
        print(f"   {'✅' if not _li['text_hits'] else '❌'} 主人 QQ 号出现在源码/文档里："
              f"{sum(len(h['lines']) for h in _li['text_hits'])} 处"
              f"（改成占位号 10001；号本身别写进任何随仓/随包文件）")
        for h in _li["text_hits"]:
            print(f"        ❌ {h['file']}  行 {','.join(str(x) for x in h['lines'])}")
        print(f"   {'✅' if not _li['bin_hits'] else '❌'} 主人 QQ 号出现在 exe（**发行物本体**，"
              f"ps2exe 把脚本含注释一起嵌入）：{len(_li['bin_hits'])} 个副本")
        for h in _li["bin_hits"]:
            print(f"        ❌ {h['file']}  含 {h['hits']} 处 → 改源码后必须重编译 exe")
        print(f"   {'✅' if not _li['nick_hits'] else '⚠️ '} 主人昵称出现处（**只列不判**：署名与"
              f"'代码缺省值'形态相同，是不是有意属人工裁决 A6/A7）："
              f"{sum(len(h['lines']) for h in _li['nick_hits'])} 处")
        for h in _li["nick_hits"]:
            print(f"        ⚠️  {h['file']}  行 {','.join(str(x) for x in h['lines'])}")
    _ist = _li.get("selftest", "—")
    print(f"   {'✅' if str(_ist).startswith('PASS') else '❌'} 分级器自检：{_ist}")

    print("=" * 68)
    print("P. 插件注册与合成通道红线（目录存在 ≠ 被加载；合成轮必须置位）")
    _pr = report["plugin_reg"]
    print(f"   plugins/ 目录 {len(_pr['dirs'])} 个 ｜ pyproject 清单 {len(_pr['declared'])} 条")
    print(f"   {'✅' if not _pr['unreg'] else '❌'} 目录存在但未注册（整个插件静默不生效）："
          f"{len(_pr['unreg'])} 个")
    for _n in _pr["unreg"]:
        print(f"        ❌ plugins/{_n}  → 在 pyproject.toml [tool.nonebot].plugins 补 "
              f"\"plugins.{_n}\"（livesource 就是这样静默失效的）")
    print(f"   {'✅' if not _pr['ghost'] else '❌'} 清单里的幽灵项（注册了但目录不在）："
          f"{len(_pr['ghost'])} 个")
    for _n in _pr["ghost"]:
        print(f"        ❌ plugins.{_n}")
    print(f"   {'✅' if not _pr['inject_no_mark'] else '❌'} 合成注入点带合成轮标记（缺了会去动真实"
          f" QQ 账号）：{len(_pr['inject_no_mark'])} 处缺")
    for _n in _pr["inject_no_mark"]:
        print(f"        ❌ {_n}  → 加 webgal.set_synthetic_round(True/False) 或置 _SYNTHETIC")
    _pst = _pr.get("selftest", "—")
    print(f"   {'✅' if str(_pst).startswith('PASS') else '❌'} 分级器自检：{_pst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
