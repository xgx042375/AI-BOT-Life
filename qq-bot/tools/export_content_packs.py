# -*- coding: utf-8 -*-
r"""export_content_packs —— 本地内容分发件导出（私有内容插件包化，2026-09-11）。

把四类 gitignored 私有内容各打成规范分发包，输出 release/local-content/（release/ 整体 gitignored）：
  1. arknights-skin-<date>.zip   方舟皮肤包：launcher/web/skins/arknights/ 全量（launcher-skin-v1 样板）
  2. cards-legacy-<date>.zip     本地人设卡 → robot-pack-v1 卡包：packs/<key>/pack.json + card.json
  3. arknights-stage-<date>.zip  GAL 舞台包 → robot-pack-v1 **type=gal**（立绘差分/基图/全身 + 背景）
  4. r18-gameplay-<date>.zip     R18 玩法配置层：scale_baseline.txt + _template.json + 安装指引
                                 ★ 这一项**故意不是 robot-pack-v1 包**：它装的是**框架级**文件
                                   （persona 直接读 qq-bot/data/scale_baseline.txt），任何包类型都消费不到它；
                                   强行套一个 type 只会让"它是什么"更模糊。故做成**安装件**并把
                                   "哪个文件放哪"写成逐行表格（见包内 README）。

定位与边界：这些内容含第三方 IP 与成人向配置，**不进公开仓/不进 SDK 包**——本脚本只在本机出包
（build_release.ps1 -LocalContent 可选调用；SDK 自检面天然不含 release/local-content）。
源内容只读取复制、零创作零改写（card.json / 皮肤素材逐字节原样进包）；本脚本仅生成
pack.json 元数据与各包 README（功能层描述，不创作任何显性文本）。幂等：重跑覆盖旧包。

用法：cd E:\robot\qq-bot && .venv\Scripts\python.exe tools\export_content_packs.py [--out 目录]
"""
from __future__ import annotations

import argparse
import json
import re
import zipfile
from datetime import date
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent          # qq-bot/tools
QQBOT_DIR = TOOLS_DIR.parent                          # qq-bot
ROBOT_ROOT = QQBOT_DIR.parent                         # 仓库根 E:\robot
# ⚠ 下面这些与 core/paths.py 同口径（本工具 DEV-ONLY、不随发行、不参与运行时审计）：
#   改 launcher 布局 / 数据根时两处都要看——audit_consistency.py 的 E 项只扫运行时，扫不到这里。
DATA_ROOT = ROBOT_ROOT / "data"                       # 运行时数据根（core/paths.py 同口径）
PERSONAS_DIR = QQBOT_DIR / "data" / "personas"        # 本地人设卡（gitignored）
SKIN_DIR = ROBOT_ROOT / "launcher" / "web" / "skins" / "arknights"  # 方舟皮肤（gitignored）
DEFAULT_OUT = ROBOT_ROOT / "release" / "local-content"

# 卡名 → 包目录键：personas 下除模板外的全部 *.json（_aoding_/default/char 等本人内容照打）
CARD_EXCLUDE = {"_template.json"}
# pack.json 的 name 段正则口径（PLUGIN_SDK §3.1）：点分小写；卡键口径 [A-Za-z0-9_-]+
_KEY_OK = re.compile(r"^[A-Za-z0-9_-]+$")
_SEG_BAD = re.compile(r"[^a-z0-9_-]+")


def _read_title(card_path: Path) -> str:
    """读卡 data.name 作 title（容忍 BOM；读不出回落文件名键）。只读，不改卡内容。"""
    try:
        obj = json.loads(card_path.read_text(encoding="utf-8-sig"))
        name = str(((obj or {}).get("data") or {}).get("name") or "").strip()
        return name or card_path.stem
    except Exception:  # noqa: BLE001 坏卡也给包（card.json 原样进包），title 回落键
        return card_path.stem


def _seg(key: str, used: set[str]) -> str:
    """卡键 → pack.json name 第二段（^[a-z0-9][a-z0-9_-]*$ 口径）；冲突追加序号。"""
    seg = _SEG_BAD.sub("-", key.lower()).strip("-_")
    if not seg or not re.match(r"^[a-z0-9]", seg):
        seg = f"card-{seg}" if seg else "card"
    base, i = seg, 1
    while seg in used:
        i += 1
        seg = f"{base}-{i}"
    used.add(seg)
    return seg


def _write_zip(zip_path: Path, texts: list[tuple[str, str]], entries: list[tuple[str, Path]]) -> int:
    """写 zip（先文本件后复制件，重跑覆盖）；返回入包文件数。"""
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()
    n = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for arc, text in texts:
            zf.writestr(arc, text)
            n += 1
        for arc, src in entries:
            zf.write(src, arc)   # 逐字节原样复制，不触碰内容
            n += 1
    return n


def _report(out_dir: Path, zip_path: Path, files: int) -> None:
    print(f"[ok]   {zip_path.name}  files={files}  bytes={zip_path.stat().st_size}")


# ---------------------------------------------------------------- 1. 方舟皮肤包
def pack_skin(out_dir: Path, stamp: str) -> Path | None:
    if not SKIN_DIR.is_dir():
        print(f"[skip] arknights-skin：皮肤目录不存在（{SKIN_DIR}）")
        return None
    readme = f"""# 方舟皮肤包（arknights-skin-{stamp}）

> 私有内容分发件：含第三方 IP 素材，不进公开仓/公开 SDK，仅限本地与私下点对点分发。
> 格式：launcher-skin-v1（规范见仓库 `docs/皮肤包接口规范-v1.md`；本皮肤即该规范的现实样板）。

## 安装

1. 把压缩包内 `launcher\\` 目录整体解压/合并到启动器安装根（即得到
   `<安装根>\\launcher\\web\\skins\\arknights\\`；本 README 为说明件，无需安装）；
2. 启动启动器 → 主页「管理」行 → 设置页 → 皮肤下拉选择 arknights → 保存即切换
   （选择写入 `<安装根>\\data\\launcher.json` 的 skin 键；皮肤目录缺失时回落 generic）。

素材版权归原权利方，仅限已持有方自用。
"""
    entries: list[tuple[str, Path]] = []
    for p in sorted(SKIN_DIR.rglob("*")):
        if p.is_file():
            # arcname 保持安装根全路径 launcher/web/skins/arknights/<rel>——整包解到安装根即落位
            entries.append((p.relative_to(ROBOT_ROOT).as_posix(), p))
    zp = out_dir / f"arknights-skin-{stamp}.zip"
    n = _write_zip(zp, [("README.md", readme)], entries)
    return zp if _report_ok(zp, n) else None


# ---------------------------------------------------------------- 2. 本地卡 legacy 包
def pack_cards(out_dir: Path, stamp: str) -> Path | None:
    if not PERSONAS_DIR.is_dir():
        print(f"[skip] cards-legacy：人设卡目录不存在（{PERSONAS_DIR}）")
        return None
    cards = sorted(
        p for p in PERSONAS_DIR.glob("*.json")
        if p.name not in CARD_EXCLUDE and not p.name.startswith(".")
    )
    if not cards:
        print("[skip] cards-legacy：无可导出卡")
        return None
    readme = f"""# 本地人设卡包（cards-legacy-{stamp}）

> 私有内容分发件：含第三方 IP 角色卡与本人自建卡，不进公开仓/公开 SDK，仅限私下分发。

每张卡一个 **robot-pack-v1** 卡包（规范见仓库 `docs/接口文档.md` §3）：

```
packs/<key>/pack.json    type=card，license=private，compat.core_api>=1.0
packs/<key>/card.json    chara_card_v2 卡原文（逐字节复制，未做任何改写）
```

## 安装（二选一）

- **包方式**：把 `packs\\` 下各 `<key>\\` 目录整体拷入 `<安装根>\\data\\packs\\`（放进来即用；
  改包内文件后重启 bot 生效）；
- **卡方式**：把各包内 `card.json` 拷为 `<安装根>\\qq-bot\\data\\personas\\<key>.json`
  （本地卡永远优先于包卡；新卡须无 BOM）。

## quotes 分层

`quotes.json`（若随包附带）来自运行时 `<安装根>\\data\\quotes.json`：整文件放回
`<安装根>\\data\\quotes.json` 即整键生效——byCard 同名卡键覆盖包内 quotes、fallback 全局兜底
只来自 data 层（合并序见 `docs/接口文档.md` §3.2）。也可按卡拆出 `{{"quotes":[...]}}` 放进各包目录。

## launcher 卡网格接线

包卡要出现在启动器卡网格：跑 `qq-bot\\tools\\export_cards.py`
（只追加不覆盖 `data\\cards.json`，现有卡零影响），重启启动器生效。
"""
    texts: list[tuple[str, str]] = [("README.md", readme)]
    entries: list[tuple[str, Path]] = []
    used: set[str] = set()
    for p in cards:
        key = p.stem
        seg = _seg(key, used)                       # pack name 段（合法化+去重）
        card_key = key if _KEY_OK.match(key) else seg  # 卡键口径更宽，通常原样
        pack_json = {
            "spec": "robot-pack-v1",
            "name": f"legacy.{seg}",
            "version": "1.0.0",
            "type": "card",
            "title": _read_title(p),
            "author": "local",
            "license": "private",
            "compat": {"core_api": ">=1.0"},
            "card": {"key": card_key},
        }
        texts.append((
            f"packs/{key}/pack.json",
            json.dumps(pack_json, ensure_ascii=False, indent=2) + "\n",
        ))
        entries.append((f"packs/{key}/card.json", p))
        print(f"[add]  card {key} → packs/{key}/（title={pack_json['title']}）")
    quotes = DATA_ROOT / "quotes.json"
    if quotes.is_file():
        entries.append(("quotes.json", quotes))
        print("[add]  quotes.json（运行时台词层，见 README 分层说明）")
    else:
        print("[warn] data/quotes.json 不存在：本包不含台词层")
    zp = out_dir / f"cards-legacy-{stamp}.zip"
    n = _write_zip(zp, texts, entries)
    return zp if _report_ok(zp, n) else None


# ---------------------------------------------------------------- 3. R18 玩法配置包
def pack_r18(out_dir: Path, stamp: str) -> Path | None:
    baseline = QQBOT_DIR / "data" / "scale_baseline.txt"
    template = PERSONAS_DIR / "_template.json"
    if not baseline.is_file() and not template.is_file():
        print("[skip] r18-gameplay：scale_baseline.txt 与 _template.json 均不存在")
        return None
    readme = f"""# R18 玩法配置包（r18-gameplay-{stamp}）

> 私有内容分发件：成人向玩法**配置层**，不进公开仓/公开 SDK，仅限私下点对点分发。
> 本包只含配置文件与模板（功能层说明），不含任何演出文本。

## 组成与安装

| 文件 | 放哪 | 作用 |
|---|---|---|
| `scale_baseline.txt` | `<安装根>\\qq-bot\\data\\scale_baseline.txt` | 全局尺度兜底基线；卡内 `scale_rules` 留空时的框架通用兜底 |
| `personas/_template.json` | `<安装根>\\qq-bot\\data\\personas\\_template.json` | 卡模板：`response_rules` / `scale_rules` / `arrogance` 字段说明——卡级覆盖模式：卡自带即注入，不带即零注入 |

## 开启与开关

- **道具/情绪层**：special 状态（标记/道具/情绪层）的登记与解除见 `docs/接口文档.md`
  §6.2「稳定钩子点索引」与 §3.4「type=item 道具 mod」；运行时账本
  `<安装根>\\data\\special_state.json` 由框架自动生成，勿手工编辑；
- **主人称呼 / 私聊白名单等开关**：`qq-bot\\.env`（样例与注释见 `.env.example`，
  如 `OWNER_NICKNAME`、`SUPERUSERS`）；行为档位以卡字段与上述配置为准。

安装后重启 bot 生效。
"""
    texts = [("README.md", readme)]
    entries: list[tuple[str, Path]] = []
    if baseline.is_file():
        entries.append(("scale_baseline.txt", baseline))
    else:
        print("[warn] scale_baseline.txt 不存在：包内缺基线件")
    if template.is_file():
        entries.append(("personas/_template.json", template))
    else:
        print("[warn] personas/_template.json 不存在：包内缺模板件")
    zp = out_dir / f"r18-gameplay-{stamp}.zip"
    n = _write_zip(zp, texts, entries)
    return zp if _report_ok(zp, n) else None


def pack_stage(out_dir: Path, stamp: str) -> Path | None:
    """GAL 舞台包（type=gal，2026-09-12 S7/T14）：把私有立绘与背景按舞台包格式打出去。

    为什么值得单独出包：T14 把"舞台长什么样"从人设卡与皮肤里独立出来——卡包给**词**、
    舞台包给**图**、皮肤给**壳**。此前这些图只散在 launcher\\cards\\ 与皮肤目录里，
    既没有可分发形态，也没有"换角色不必换背景"的边界。

    目录形状**复用仓内已声明的约定**（`sprites/full|bust|diff/<键>_<词>.webp`）——
    所以是**逐字节纯拷贝、零改名**：
      sprites/diff/<键>_<词>.webp   ← launcher\\cards\\bust4w\\  （带下划线的都是差分）
      sprites/bust/<键>.webp        ← launcher\\cards\\bust4w\\  （不带下划线的基图）
      sprites/full/<键>.webp        ← launcher\\cards\\fullw\\
      bg/<id>.<ext>                 ← 皮肤目录里的场景图（本机仅有这两张）

    不写 `sprites/manifest.json` 的 map：卡包已经声明了各自的"词→素材词"映射，
    舞台包再写一份就是**第二个事实源**——这正是 T14 要避免的。故不发 `card.key`，
    也不覆盖卡包映射（无 `card.key` 时 map 本就不生效）。
    """
    cards_dir = ROBOT_ROOT / "launcher" / "cards"
    # 段名与 plugins/webgal 的 DIFF_SEG / FULL_SEG 同源（运行时侧已单一来源；本工具是导出用的镜像）
    bust = cards_dir / "bust4w"
    full = cards_dir / "fullw"
    bg_src = [
        (SKIN_DIR / "img" / "avg_2_1.png", "avg_2_1"),
        (SKIN_DIR / "img" / "UI_HOME_FRONT_BKG.png", "UI_HOME_FRONT_BKG"),
    ]
    if not bust.is_dir() and not full.is_dir():
        print(f"[skip] stage：立绘素材目录不存在（{cards_dir}）")
        return None

    entries: list[tuple[str, Path]] = []
    # 基图 / 差分的判据 = **文件名是否等于一个已知卡键**，而不是"名字里有没有下划线"。
    # ★ 2026-09-12 实测踩坑：按"含下划线即差分"分类会把 `_aoding_.webp` 判成差分——因为那张卡的
    #   **卡键本身就带下划线**。行为上无害（词表按 `<键>_` 前缀匹配，匹配不到它），
    #   但包结构是错的、还会把读包的人教歪。已知卡键取自 personas 目录（与 pack_cards 同源）。
    known = {q.stem for q in PERSONAS_DIR.glob("*.json")} if PERSONAS_DIR.is_dir() else set()
    diff_n = bust_n = full_n = 0
    for p in sorted(bust.glob("*.webp")) if bust.is_dir() else []:
        stem = p.stem
        if stem in known:                       # 名字就是卡键 → 基图（含卡键自带下划线的 `_aoding_`）
            entries.append((f"sprites/bust/{p.name}", p))
            bust_n += 1
        elif "_" in stem and any(stem.startswith(k + "_") for k in known):
            entries.append((f"sprites/diff/{p.name}", p))     # <卡键>_<词> → 差分
            diff_n += 1
        else:                                   # 未知键：无法判定，宁可当基图（不污染 diff 词表）
            entries.append((f"sprites/bust/{p.name}", p))
            bust_n += 1
    for p in sorted(full.glob("*.webp")) if full.is_dir() else []:
        entries.append((f"sprites/full/{p.name}", p))
        full_n += 1

    bg_default = ""
    bg_alt = ""
    for src, bg_id in bg_src:
        if not src.is_file():
            continue
        entries.append((f"bg/{bg_id}{src.suffix.lower()}", src))
        if not bg_default:
            bg_default = bg_id
        elif not bg_alt:
            bg_alt = bg_id
    if not bg_default:
        print("[warn] stage：未找到背景图（皮肤 img/ 下），包内将无 bg/——前端回落 CSS 渐变")

    stage_seg: dict = {}
    if bg_default:
        stage_seg["default_bg"] = bg_default
    if bg_alt:
        stage_seg["bg_alt"] = bg_alt
    # bg_by_scene 故意留空：场景词由 lifesim 自由生成，本机没有可信的场景→背景对应表——
    # 编一张表等于替用户决定"天台该长什么样"，且会盖掉他们自己写的映射（先到先得）。
    stage_seg["bg_by_scene"] = {}

    pack_json = {
        "spec": "robot-pack-v1",
        "name": "local.stage",
        "version": "1.0.0",
        "type": "gal",
        "title": f"本机舞台素材（{stamp}）",
        "author": "local",
        "license": "private",
        "compat": {"core_api": ">=1.0"},
        "stage": stage_seg,
    }
    readme = f"""# GAL 舞台包（arknights-stage-{stamp}）

> 私有内容分发件：含第三方 IP 立绘与背景，不进公开仓/公开 SDK，仅限本地与私下点对点分发。
> 格式：**robot-pack-v1 · type=gal**（契约见仓库 `docs/接口文档.md` §3.8）；本包即该格式的现实样板。

## 组成

| 目录 | 内容 | 来源 |
|---|---|---|
| `sprites/diff/` | 立绘**差分**（`<卡键>_<词>.webp`，{diff_n} 张） | `<安装根>\\launcher\\cards\\bust4w\\` |
| `sprites/bust/` | 半身基图（`<卡键>.webp`，{bust_n} 张） | 同上（不带下划线的那些） |
| `sprites/full/` | 全身立绘（{full_n} 张） | `<安装根>\\launcher\\cards\\fullw\\` |
| `bg/` | 背景（{1 if bg_default else 0}+ 张） | 皮肤目录 `img/` |

素材**逐字节原样进包，未做任何转换或改名**——命名沿用仓内既有约定，可直接互相拷来拷去。

## 安装

把 `packs\\local.stage\\` 整个目录拷进 `<安装根>\\data\\packs\\`，**重启 bot** 生效。

- 背景经 `GET /gal/stage/local.stage/bg/<文件名>` 提供（服务出口由框架提供，你无需配置任何路由）；
- 差分词表由框架按 `舞台包 sprites/diff/` → `launcher\\cards\\bust4w\\` **多根扫描**（包在前，
  即同名优先用包里的图）——所以**装了这个包，`launcher\\cards\\` 里可以留一份做兜底**；
- 场景→背景映射（`bg_by_scene`）本包**留空**：场景词来自模型自由生成，通用映射无从编起。
  要挂自己的映射就直接改包内 `pack.json` 的 `stage.bg_by_scene`（形如 `{{"天台": "avg_2_1"}}`，
  键是**短词**——匹配是"精确 → 最长键包含 → 默认"，写 `天台` 能命中「天台上看星星」）。

## 边界

- 与卡包的分工：**卡包给"词"**（`sprite_expressions` / `sprites/manifest.json` 的 `map`），
  **舞台包给"图"**。所以本包不含 `map`，也不声明 `card.key`——不制造第二个事实源；
- 与皮肤的分工：**皮肤给"壳"**（配色/控件/布局）。舞台包不被皮肤覆盖，换皮肤不影响背景；
- 素材版权归原权利方，仅限已持有方自用。
"""
    texts = [("README.md", readme),
             ("packs/local.stage/pack.json", json.dumps(pack_json, ensure_ascii=False, indent=2) + "\n")]
    # 包内路径前缀统一加 packs/local.stage/
    pkg_entries = [(f"packs/local.stage/{arc}", src) for arc, src in entries]
    zp = out_dir / f"arknights-stage-{stamp}.zip"
    bg_n = len([1 for _s, _i in bg_src if _s.is_file()])
    print(f"[add]  stage → packs/local.stage/（差分 {diff_n} · 基图 {bust_n} · 全身 {full_n} · 背景 {bg_n}）")
    n = _write_zip(zp, texts, pkg_entries)
    return zp if _report_ok(zp, n) else None


def _report_ok(zip_path: Path, files: int) -> bool:
    if files <= 1:  # 只有 README=空包
        print(f"[warn] {zip_path.name} 仅含 README，视为空包")
        if zip_path.exists():
            zip_path.unlink()
        return False
    _report(zip_path.parent, zip_path, files)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="本地内容分发件导出（私有内容插件包化）")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录（默认 release/local-content）")
    args = ap.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().strftime("%Y%m%d")
    print(f"== export_content_packs：输出目录 {out_dir}（重跑覆盖） ==")
    made = 0
    for fn in (pack_skin, pack_cards, pack_stage, pack_r18):
        if fn(out_dir, stamp) is not None:
            made += 1
    print(f"== [done] 生成 {made}/4 个分发包 ==")
    return 0 if made else 1


if __name__ == "__main__":
    raise SystemExit(main())
