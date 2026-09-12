# -*- coding: utf-8 -*-
"""语音插件（2026-08-28）：奥汀 GPT-SoVITS 模型选择性发声。
触发门：raw=True（/语音测试 诊断强制）或回复带 [VOICE] 标记（bot 自决）；群聊开关配置级。
2026-09-07（用户裁决）：**仅中文语音，日文封存**——奥汀保留原声线跨语言直出中文
（日文参考音 + prompt_lang=日文 + text=中文），translate_ja 与 /语音日文 留档不删。
链路：无 HTTP 常驻 worker（JSON-lines 协议，模型常驻 ~2s）→ 48k wav → F3 EQ（定版）→ 24k NT Silk → record(base64) 发送。
情绪参考库：奥汀按台词情绪自动选参考音（战斗/讥讽/温柔/默认）。
配置：data/voice_mode.json；状态：data/voice_state.json。"""
import json
import os
import random
import re
import subprocess
import threading
import time
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from nonebot.adapters.onebot.v11 import Bot, MessageEvent, GroupMessageEvent, MessageSegment
from nonebot.log import logger

from core import atomics
from . import emotion_params

ROOT = Path(__file__).resolve().parents[3]  # E:\robot
DATA = ROOT / "data"
CFG_FILE = DATA / "voice_mode.json"
STATE_FILE = DATA / "voice_state.json"
EMO_PARAM_FILE = DATA / "voice_emotion_params.json"  # 情绪参数变动器配置（可缺省）

DEFAULT_CFG = {
    "enabled": False,         # 默认关闭（用户要求）；「开启语音」启用 /「关闭语音」停用
    "group_enabled": True,    # 群聊开（2026-08-29 用户要求）
    # 2026-09-06：prob/cooldown/daily_max 机器门已移除——发不发语音由 agent 自决（[VOICE] 标记）
    # 2026-09-07：max_chars/language/translate_zh 死配置键删除（全库无读取方）
}

PKG = r"E:\robot\tools\GPT-SoVITS-v4-package\GPT-SoVITS-v4-20250529"          # V4 整合包（48kHz bigvgan）
RUNTIME_PY = os.path.join(PKG, "runtime", "python.exe")
GPT_MODEL = os.path.join(PKG, "GPT_weights_v4", "odin7-e15.ckpt")
SOVITS_MODEL = os.path.join(PKG, "SoVITS_weights_v4", "odin7_e10_s390_l64.pth")
FFMPEG = os.path.join(PKG, "ffmpeg.exe")
TMP = ROOT / "data" / "voice_tmp"

# ---- 无 HTTP 常驻 worker（2026-09-06：JSON-lines over stdin/stdout；模型常驻，短句热调用 ~1.6-2.3s） ----
DAEMON = os.path.join(PKG, "voice_tts_worker_daemon.py")

# ---- 音色注册表：人设 → 模型权重 + 参考音目录（2026-09-06：6 个 GSVI 中文角色包 + odin 日文兜底） ----
# personas：哪些人设卡使用此音色（_active_voice 按卡名匹配）
# lang（包语言，必须与参考音频一致！2026-09-06 教训：参考音频被按错误语言处理（中文参考→"日文"管道）
#   会导致 s1 语义错位 → 随机丢段/静音——之前 75% 丢段率的根因）：
#   zh = 中文语音包（中文原声台词，参考 prompt 用"中文"）：amiya/kaltsit/exusiai/skadi/lappland/phibia
#   ja = 日文语音包（日文原声台词，参考 prompt 用"日文"）：odin
#   en = 英文语音包（将来如有，参考 prompt 用"英文"）
VOICES = {
    "odin": {
        "name": "奥汀", "lang": "ja",
        "personas": ["奥汀", "奥丁"],
        "gpt": GPT_MODEL, "sovits": SOVITS_MODEL,
        "ref_dir": os.path.join(PKG, "logs", "odin6", "5-wav32k"),
        "refs": None,  # 内建 REF_TEXTS
    },
    "amiya": {
        "name": "阿米娅", "lang": "zh", "personas": ["阿米娅"],
        "gpt": os.path.join(PKG, "voices", "amiya", "阿米娅-e10.ckpt"),
        "sovits": os.path.join(PKG, "voices", "amiya", "阿米娅_e10_s230_l32.pth"),
        "ref_dir": os.path.join(PKG, "voices", "amiya", "refs"), "refs": "voice_refs.json",
        # emo_refs：情绪类别（classify_emotion 9 类口径）→ 包内参考音条目（按台词语义归类；缺类→「默认」）
        "emo_refs": {
            "战斗": "作战中2", "高冷": "作战中4",   # 切开法术 / 漫漫长夜（坚毅低沉）
            "讥讽": "闲置", "无语": "闲置",         # 「还不能休息哦」（嗔怪催促）
            "温柔": "进驻设施",                     # 「又回家了，真好」（放松柔软）
            "娇嗔": "戳一下", "纯H": "戳一下",      # 「唔啊，博士......！」（气声惊呼）
            "傲娇": "精英化晋升1",                  # 「并肩作战真是太好了」（明亮上扬）
        },
    },
    "kaltsit": {
        "name": "凯尔希", "lang": "zh", "personas": ["凯尔希"],
        "gpt": os.path.join(PKG, "voices", "kaltsit", "凯尔希-e10.ckpt"),
        "sovits": os.path.join(PKG, "voices", "kaltsit", "凯尔希_e10_s160_l32.pth"),
        "ref_dir": os.path.join(PKG, "voices", "kaltsit", "refs"), "refs": "voice_refs.json",
        "emo_refs": {
            "战斗": "行动出发",                     # 「再次检查抗源石设备」（令色沉稳）
            "高冷": "精英化晋升1",                  # 「越是强大越是脆弱，这就是万物的道理」（冷静超然的断言）
            "讥讽": "非3星结束行动",                # 「你应当多加小心」（责备）
            "无语": "观看作战记录",                 # 「不要太过依赖它们」（淡漠提醒）
            "温柔": "精英化晋升2",                  # 「保护所有人的，特别是博士你」（少见的柔软）
        },  # 娇嗔/纯H/傲娇/疑问 无贴合台词（其余未用参考=任务播报腔：按计划行动/整理录像存档，硬凑=语调错位）→ 缺省「默认」
    },
    "exusiai": {
        "name": "能天使", "lang": "zh", "personas": ["能天使", "新约能天使"],
        "gpt": os.path.join(PKG, "voices", "exusiai", "新约能天使-e10.ckpt"),
        "sovits": os.path.join(PKG, "voices", "exusiai", "新约能天使_e10_s160_l32.pth"),
        "ref_dir": os.path.join(PKG, "voices", "exusiai", "refs"), "refs": "voice_refs.json",
        "emo_refs": {
            "战斗": "行动出发",                     # 「像风暴一样碾过去！」
            "娇嗔": "交谈2", "疑问": "交谈2",       # 「今天送点什么呀！」（跳脱哼唱腔）
            "讥讽": "闲置", "无语": "闲置",         # 「这个人也是我们要拯救的吗？」（面无表情）
            "傲娇": "行动失败",                     # 「别介意别介意~」（嘻嘻哈哈打岔）
        },
    },
    "skadi": {
        "name": "斯卡蒂", "lang": "zh", "personas": ["斯卡蒂", "浊心斯卡蒂"],
        "gpt": os.path.join(PKG, "voices", "skadi", "浊心斯卡蒂-e10.ckpt"),
        "sovits": os.path.join(PKG, "voices", "skadi", "浊心斯卡蒂_e10_s290_l32.pth"),
        "ref_dir": os.path.join(PKG, "voices", "skadi", "refs"), "refs": "voice_refs.json",
        "emo_refs": {
            "战斗": "行动出发",                     # 「终归还是没什么手感」（冷冽杀意）
            "讥讽": "行动开始",                     # 「你们也太弱了」（轻蔑）
            "无语": "行动失败",                     # 「也只是个开始罢了」（低沉倦怠）
            "温柔": "编入队伍",                     # 「我倒也挺擅长……只是......」（难得迟疑的软）
            "疑问": "任命队长",                     # 「交到我手上了吗？」（疑问句）
        },  # 高冷=本体，走「默认」
    },
    "lappland": {
        "name": "拉普兰德", "lang": "zh", "personas": ["拉普兰德"],
        "gpt": os.path.join(PKG, "voices", "lappland", "拉普兰德-e10.ckpt"),
        "sovits": os.path.join(PKG, "voices", "lappland", "拉普兰德_e10_s150_l32.pth"),
        "ref_dir": os.path.join(PKG, "voices", "lappland", "refs"), "refs": "voice_refs.json",
        # 仅默认参考音 → 无 emo_refs，一律「默认」（补参考音后按 9 类口径加映射）
    },
    "phibia": {
        "name": "菲比", "lang": "zh", "personas": ["菲比"],
        "gpt": os.path.join(PKG, "voices", "phibia", "菲比_ZH-e10.ckpt"),
        "sovits": os.path.join(PKG, "voices", "phibia", "菲比_ZH_e10_s290_l32.pth"),
        "ref_dir": os.path.join(PKG, "voices", "phibia", "refs"), "refs": "voice_refs.json",
        # 仅默认参考音 → 无 emo_refs，一律「默认」
    },
}

# ---- 最终音色链（F3 定版：高透160 切拖尾 + 500Hz体量 + 音量5.6 + 24k silk 直发）——**仅奥汀日文** ----
JA_SILK_EQ = ("afftdn=nf=-22,highpass=f=160,equalizer=f=500:t=q:w=1:g=2,"
              "highshelf=f=3200:g=2,lowpass=f=10000,volume=5.6,alimiter=limit=0.93:level=false")
# ---- 中文原生音色中性链（2026-09-06：原生语音包不做奥汀式调音——高频削杂 + 简单限幅，不增益/不变调/不加味） ----
EQ_ZH = "afftdn=nf=-20,highpass=f=70,lowpass=f=12000,alimiter=limit=0.93:level=false"
WAV2SILK = r"E:\robot\tools\wav2silk.js"

# ---- 情绪参考库：LLM 分类（bot 理解）优先，关键词兜底 ----
REF_EMOTIONS = [
    (re.compile(r"天雷|撃ち抜け|裁け|貫け|愚か者ども|討ち取れ|滅ぼせ|壊せ"),
     "cv_E00065", os.path.join(PKG, "logs", "odin6", "5-wav32k", "cv_E00065.wav")),
    (re.compile(r"クク|ふふ|愚かだ|この程度|分際|笑止|可笑し|雑魚"),
     "cv_E00437", r"E:\robot\data\voice_refs\ref_E00437_head.wav"),
    (re.compile(r"怪我|心配|なぜ|どうした|無事|謝れ|構わず|許せ"),
     "cv_E00474", os.path.join(PKG, "logs", "odin6", "5-wav32k", "cv_E00474.wav")),
    (re.compile(r"呆れ|はぁ|まったく|物も言え|呆れ果て"),
     "cv_E00080", os.path.join(PKG, "logs", "odin6", "5-wav32k", "cv_E00080.wav")),
]
DEFAULT_REF = ("cv_E00440", r"E:\robot\data\voice_refs\ref_E00440_head.wav")

# LLM 情绪分类 → 参考音映射（高冷/傲娇 → 默认 E00440 高冷宣言）
EMO_REF_MAP = {
    "战斗": ("cv_E00065", os.path.join(PKG, "logs", "odin6", "5-wav32k", "cv_E00065.wav")),
    "讥讽": ("cv_E00437", r"E:\robot\data\voice_refs\ref_E00437_head.wav"),
    "温柔": ("cv_E00474", os.path.join(PKG, "logs", "odin6", "5-wav32k", "cv_E00474.wav")),
    "娇嗔": ("cv_E00474", os.path.join(PKG, "logs", "odin6", "5-wav32k", "cv_E00474.wav")),
    "疑问": ("cv_E00474", os.path.join(PKG, "logs", "odin6", "5-wav32k", "cv_E00474.wav")),
    "无语": ("cv_E00080", os.path.join(PKG, "logs", "odin6", "5-wav32k", "cv_E00080.wav")),
    "纯H": ("cv_E00470", os.path.join(PKG, "logs", "odin6", "5-wav32k", "cv_E00470.wav")),
}
# 基调 9 类固定词表（2026-09-11 热修十二 V1 单一事实源）：classify 提示词、ALLOWED_EMOS、
# brain 润色提示（基调自标）共用同一词集——brain 侧经 voice.TONE_CLASSES 引用（brain 本就 import voice）
TONE_CLASSES = ("高冷", "傲娇", "讥讽", "战斗", "温柔", "无语", "娇嗔", "疑问", "纯H")
ALLOWED_EMOS = set(TONE_CLASSES)

# 情绪 → (温度, 语速, 半音偏移)：声调=语气核心（f0 能量轮廓），半音偏移直接改音高；
# 战斗=更尖亢 / 温柔=更柔低 / 无语=更低更泄气 / 疑问=尾音上扬感
EMO_PARAMS = {
    "战斗": (1.05, 1.08, 1.5),
    "讥讽": (1.00, 1.02, 0.5),
    "温柔": (0.90, 0.94, -1.0),
    "娇嗔": (0.92, 0.98, 0.5),
    "疑问": (0.95, 0.98, 1.0),
    "无语": (0.90, 0.88, -1.5),
    "纯H": (0.98, 0.95, 1.0),
    "高冷": (1.00, 1.00, 0.0),
    "傲娇": (1.00, 1.00, 0.0),
}

# ---- zh 包情绪参数（2026-09-09 深夜用户裁决：参考音按 bot 自决情绪选，不再钉死「默认」）----
# 温度口径（用户问询「普遍中文包用的参数」的调研结论）：GPT-SoVITS 官方 api_v2/webui 滑条默认 1.0，
# 社区实践 0.6~1.0（越低越稳防漏字，越高情感越足；情感迁移常见 0.6-0.75 配低 top_p）。
# 本 worker 带丢段重试梯子（temp±0.1 clamp[0.5,1.0]+时长完整性检查）→ 温度偏高侧安全。
# 这里以奥汀 EMO_PARAMS 为基准取值并整体钳制 ≤1.0；其中 高冷/傲娇=0.95 略低于奥汀的 1.00
# （高冷/傲娇类 zh 参考音本就平稳，低温减漂移）；语速恒 1.0、无半音变调（2026-09-06 裁决：zh 包不做奥汀式调音）。
# 中性默认 0.85：介于官方 1.0 与防漏字 0.6-0.75 之间的稳妥档（曾 0.7 偏保守，棒读投诉成因之一）。
ZH_EMO_PARAMS = {
    "战斗": (1.00, 1.0),
    "讥讽": (1.00, 1.0),
    "温柔": (0.90, 1.0),
    "娇嗔": (0.92, 1.0),
    "疑问": (0.95, 1.0),
    "无语": (0.90, 1.0),
    "纯H": (0.98, 1.0),
    "高冷": (0.95, 1.0),
    "傲娇": (0.95, 1.0),
}
ZH_DEFAULT_PARAMS = (0.85, 1.0)

# ---- 情绪参数变动器：中心值表（2026-09-11 重做）----
# 上面 ZH_EMO_PARAMS 的语速全部是 1.0（历史上"语速恒 1.0"的遗留），若直接当中心值，
# 变动器只会让语速在 1.0 附近抖 ±3%——听不出任何"能量差"。所以这里给出**中心语速**，
# 按调研 §3 社区档位映射表取值（激动 1.05~1.15 / 温柔低落 0.85~0.95），
# 让不同情绪档之间真的有快慢差，同一档内部再由变动器抖动出"每次略不同"。
# 元组 = (中心温度, 中心语速, 中心 top_p)；未列出的档沿用 ZH_EMO_PARAMS（语速 1.0）。
EMO_VARIANT_CENTERS = {
    "战斗": (1.00, 1.10, 0.95),   # 快而亮
    "讥讽": (1.00, 1.03, 0.90),   # 略快、微扬
    "温柔": (0.88, 0.94, 0.78),   # 慢而柔
    "娇嗔": (0.92, 0.98, 0.90),   # 略软
    "疑问": (0.95, 1.00, 0.90),   # 平速、尾音上扬（上扬靠文本标点，不靠参数）
    "无语": (0.88, 0.90, 0.78),   # 慢而低泄气
    "纯H": (0.98, 1.00, 0.90),    # 微飘
    "高冷": (0.95, 1.00, 0.85),   # 平稳本体（勿动）
    "傲娇": (0.95, 1.00, 0.85),   # 平稳本体（勿动）
}

# ---- 情绪参数变动器配置缓存（2026-09-11 新增）----
# 读盘带 mtime 缓存：_voice_request 每次合成都会调，不能每次读文件；
# 但也必须能在运行期改配置后**自动重载**（否则调参要重启 bot 才能听到效果）。
_emo_cfg_cache: dict = {"mtime": None, "cfg": None}


def _emo_param_cfg() -> dict:
    """取变动器配置（带 mtime 缓存；文件缺失/损坏 → 默认配置，绝不抛）。"""
    try:
        p = EMO_PARAM_FILE
        mt = p.stat().st_mtime if p.exists() else None
        if _emo_cfg_cache["mtime"] != mt or _emo_cfg_cache["cfg"] is None:
            _emo_cfg_cache["cfg"] = emotion_params.load_config(p)
            _emo_cfg_cache["mtime"] = mt
        return _emo_cfg_cache["cfg"]
    except Exception:  # noqa: BLE001
        return emotion_params.load_config(None)


def apply_pack_merges() -> None:
    """内容包合并（robot-pack-v1 Phase 3）：包供音色/情绪基调 setdefault 合并——**内置表永远优先**。
    - 音色：core.packs.voice_index()（card 包 voice.json + type=voice 包；相对路径条目已被过滤+warning）
      → VOICES.setdefault（内置音色不被包覆盖）。
    - 基调：core.packs.tones_index() 中带完整 voice 参数的词 → EMO_PARAMS.setdefault
      （内置 9 类口径不扩不动；zh 包调音口径不受影响——zh 链路走 ZH_EMO_PARAMS，包词在 zh 下自然用中性默认档）。
      LLM 分类口径 9 类不扩（classify_emotion 不变）——新增情绪词先走【基调】自标直通路径。
      空 voice 的 tone 词不进 EMO_PARAMS（只扩 sprite/文案层，见 core.packs.content_index）。
    幂等（setdefault 语义）；启动期（import 后）与测试期可重复调用。"""
    try:
        from core import packs as _packs
        for k, v in (_packs.voice_index() or {}).items():
            VOICES.setdefault(str(k), v)
        for t in (_packs.tones_index() or []):
            w = str(t.get("word") or "").strip()
            vc = t.get("voice") or {}
            if w and vc and w not in EMO_PARAMS:
                try:
                    EMO_PARAMS[w] = (float(vc.get("temp", 1.0)),
                                     float(vc.get("speed", 1.0)),
                                     float(vc.get("semi", 0.0)))
                except (TypeError, ValueError):
                    continue  # 参数不可解析：该词不进声调表（sprite/文案层仍生效）
    except Exception as e:  # noqa: BLE001  包体系任何故障=内置表原状（零行为变化）
        logger.warning("voice pack merge failed: {} [{}]", e, type(e).__name__)


apply_pack_merges()


def _pitch_filter(semi: float) -> str:
    """半音偏移 → ffmpeg 链（asetrate+atempo 变调不变速）。semi=0 返回空串。"""
    if not semi:
        return ""
    f = 2 ** (semi / 12.0)
    return f"asetrate=48000*{f:.5f},aresample=48000,atempo={1 / f:.5f},"


def pick_ref(speech: str, emo: str | None = None) -> tuple[str, str]:
    """情绪参考选择：LLM 分类(emo) 优先 → 关键词兜底 → 默认高冷 E00440。"""
    if emo and emo in EMO_REF_MAP:
        return EMO_REF_MAP[emo]
    for rx, name, path in REF_EMOTIONS:
        if rx.search(speech or ""):
            return name, path
    return DEFAULT_REF


async def classify_emotion(text: str) -> str | None:
    """LLM 理解台词情绪（bot 的上下文理解影响语音情绪化）：返回情绪词或 None。
    引擎为 11434 当前加载模型（用户切换 gemma 后同引擎生效；model 字段仅为标识）。"""
    try:
        import httpx
        from .. import brain as _brain

        ukey = _brain.get_model_key("0")  # 默认 key（llama-server 实际以当前加载为准）
        mlabel = _brain.MODELS[ukey]["label"] if ukey in _brain.MODELS else "Gemma4-12B"
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                "http://127.0.0.1:11434/v1/chat/completions",
                json={
                    "model": mlabel,
                    "messages": [
                        {"role": "system", "content": (
                            "你是情绪分类器。判断下面这句台词的情绪，只输出一个词："
                            + "/".join(TONE_CLASSES) + "。"
                        )},
                        {"role": "user", "content": text[:150]},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 800,
                },
                timeout=30,
            )
            out = (r.json()["choices"][0]["message"]["content"] or "").strip().strip("\"'。")
            out = re.sub(r"[^\w\u4e00-\u9fff]", "", out)[:4]
            if out in ALLOWED_EMOS:
                return out
    except Exception:  # noqa: BLE001
        pass
    return None

# 显式指令（必发）
# 开关指令已迁移到 /语音 命令（debug 插件，仅私聊+超管；对话中不再触发）
# 文本清洗：动作括号/星号动作/【】标签去净；引号去掉；长括号（内心独白等）也去
_ACT_RE = re.compile(r"[（(][^（()）]{1,60}[)）]|\*[^*]{1,60}\*|【[^】]{1,60}】|《[^》]{1,60}》")
_QUOTE_RE = re.compile(r"[「」『』“”\"'‘’]")
_SEP_RE = re.compile(r"(?<=[。！？!?～~…])")

_tts_lock = asyncio.Lock()  # 多轮语音串行（显存与引擎共享）


def _load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return default


def _save_json(path, data):
    """2026-09-07 换原子写（项目铁律：状态文件全部 tmp+os.replace；曾因半截 JSON 静默清零过状态）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    atomics.write_json_atomic(path, data)


def load_cfg() -> dict:
    return dict(DEFAULT_CFG, **_load_json(CFG_FILE, {}))


def save_cfg(cfg: dict):
    _save_json(CFG_FILE, cfg)



def status_text(user_id: str = "") -> str:
    cfg = load_cfg()
    st = _load_json(STATE_FILE, {})
    today = time.strftime("%Y-%m-%d")
    e = st.get(str(user_id), {})
    cnt = e.get("count", 0) if e.get("date") == today else 0
    last = float(e.get("last", 0) or 0)
    ago = "—" if not last else f"{int((time.time() - last) / 60)} 分钟前"
    return (f"语音：{'开启' if cfg.get('enabled') else '关闭'}（群聊{'开' if cfg.get('group_enabled') else '关'}，"
            f"中文语音（日文封存），发不发由我自决，今日 {cnt} 次，上次 {ago}）")



def _cut_at_boundary(s: str, max_chars: int) -> str:
    """单句超长时：优先在逗号/顿号/分号/空格处截断（不砍词），无则硬截+省略号。"""
    idx = None
    for ch in "，、；： 　":
        i = s.rfind(ch, 0, max_chars)
        if i > int(max_chars * 0.5):
            idx = max(idx or 0, i)
    if idx:
        return s[: idx + 1].rstrip() + "…"
    return s[:max_chars] + "…"


def _sentences_for_voice(reply: str, max_single: int = 40) -> list[str]:
    """语音文本清洗→句列表（2026-09-06：无总字数上限——长话分多段，像文字一样）。
    去动作/引号、省略号归一为句号、按句切分；单句超长在词界/逗号截断（不砍词）。
    2026-09-09 深夜：协议基调/节奏标记任何括号变体先剥（[基调：x] 半角漂移曾随文本进 TTS 被念出来）——
    TTS 是发送链最后一环，此处兜底与上游 core/reply._TONE_RE 同口径，不信任上游。"""
    t = re.sub(r"[【\[]基调[：:]\s*[^】\]]{1,6}\s*[】\]]|[【\[]等\s*[0-9.]+\s*秒[】\]]", "", reply or "")
    t = _ACT_RE.sub("", t)
    t = _QUOTE_RE.sub("", t)
    t = re.sub(r"[…]{2,}|\.{3,}", "。", t)
    t = re.sub(r"\s+", "", t).strip()
    if not t:
        return []
    sents = [s for s in _SEP_RE.split(t) if s.strip()]
    out = []
    for s in sents:
        out.append(_cut_at_boundary(s, max_single) if len(s) > max_single else s)
    return out


# ---- 无 HTTP 常驻 worker（模型常驻；短句热调用 ~2s） ----
_workers: dict[str, subprocess.Popen] = {}
_worker_errfs: dict[str, object] = {}  # worker stderr 句柄（进程死后由父进程关闭，防句柄泄漏）
_worker_lock = threading.RLock()  # 2026-09-06 RLock（不可重入普通锁曾致 _get_worker→_spawn_worker 自死锁，startup 卡死）
# 2026-09-07 修复：readline 超时读取的执行器此前被引用但从未定义（NameError 被吞 → 所有 TTS 必失败且杀 worker）。
# 多线程：单个 worker 挂死时其读线程会阻塞到管道 EOF，单线程池会连累后续所有合成的读取。
_READ_EXEC = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tts-read")


_AV_WARNED = False  # 无音色包提示只发一次（该函数每轮多次调用）
def _active_voice() -> str:
    """当前人设 → 音色 key（按卡内 name/文件名匹配 personas 映射）。
    2026-09-10 用户裁决：无音色包=无语音（返回空串），不再 odin 兜底——音色是人设身份，
    借用别卡=人设污染；全部消费方按 falsy 优雅降级（跳过合成+留日志）。"""
    global _AV_WARNED
    try:
        from plugins import brain as _b

        pn = str(_b._persona_name("") or "")
        _card = _b._persona_card()
        cn = str(_card.get("name") or "") if isinstance(_card, dict) else ""
    except Exception:  # noqa: BLE001
        pn = cn = ""
    for key, v in VOICES.items():
        if key == "odin":
            continue
        if (pn in (v.get("personas") or []) or cn in (v.get("personas") or [])) \
                and os.path.exists(v["gpt"]) and os.path.exists(v["sovits"]):
            return key
    if not _AV_WARNED:
        _AV_WARNED = True
        logger.info("voice: 当前人设无音色包（语音跳过，不借用别卡音色）")
    return ""


def _spawn_worker(voice: str) -> subprocess.Popen | None:
    """拉起一个音色的常驻 worker（模型首次请求时加载；stdin EOF 自动退出）。"""
    v = VOICES.get(voice)
    if not v:
        return None
    env = {
        **os.environ,
        "gpt_path": v["gpt"],
        "sovits_path": v["sovits"],
        "voice_ref_dir": v.get("ref_dir") or "",
        "CUDA_VISIBLE_DEVICES": "0",
        "HF_HUB_OFFLINE": "1",
    }
    if v.get("refs"):
        env["voice_ref_texts"] = os.path.join(v["ref_dir"], v["refs"])
    # 2026-09-06 换人设=换音色：停掉其他音色 worker（只留当前，防两个模型常驻占显存）
    with _worker_lock:
        for _v, _p in list(_workers.items()):
            if _v != voice and _p and _p.poll() is None:
                try:
                    _p.kill()
                    _p.wait(timeout=5)  # 2026-09-08：回收进程句柄（曾 kill 后不 wait）
                except Exception:  # noqa: BLE001
                    pass
                _workers.pop(_v, None)
                _close_errf(_v)
                logger.info("tts worker swapped out: {} (switching to {})", _v, voice)
    # worker stderr 落盘（诊断：daemon 的加载/推理错误原来被 DEVNULL 吞掉）
    try:
        TMP.mkdir(parents=True, exist_ok=True)
        _errf = open(str(TMP / f"worker_{voice}.err.log"), "ab")
        _worker_errfs[voice] = _errf  # 2026-09-07：登记句柄，worker 结束后统一关闭（曾只开不关泄漏句柄）
    except OSError:
        _errf = subprocess.DEVNULL
    try:
        p = subprocess.Popen(
            [RUNTIME_PY, "-u", DAEMON], cwd=PKG, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=_errf,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        _workers[voice] = p
        logger.info("tts worker spawned: {} pid={}", voice, p.pid)
        return p
    except Exception as e:  # noqa: BLE001
        logger.warning("tts worker spawn failed: {}", e)
        return None


def _close_errf(voice: str):
    """关闭某音色 worker 的 stderr 句柄（进程已死后父进程侧的清理）。"""
    f = _worker_errfs.pop(voice, None)
    if f is not None:
        try:
            f.close()
        except OSError:
            pass


def _get_worker(voice: str) -> subprocess.Popen | None:
    with _worker_lock:
        p = _workers.get(voice)
        if p and p.poll() is None:
            return p
        return _spawn_worker(voice)


def ensure_tts_server() -> bool:
    """公开名（bot.py 启动器调用）：拉起当前人设音色的常驻 worker（惰性加载模型）。"""
    try:
        return _get_worker(_active_voice()) is not None
    except Exception as e:  # noqa: BLE001
        logger.warning("tts worker ensure failed: {}", e)
        return False


async def warmup_worker() -> None:
    """启动预热（2026-09-06 启动器优化）：让当前音色 worker 把模型加载好——首次语音不用再等 ~6.5s。
    发一条最小合成请求（结果丢弃）；失败静默（首次语音时自然加载）。
    2026-09-07：预热也走全局 _tts_lock（此前是唯一绕串行锁的调用方，与真实合成并发会互相顶超时）。"""
    try:
        cfg = load_cfg()
        if not cfg.get("enabled"):
            return
        voice = _active_voice()
        if not voice:
            return  # 当前人设无音色包：预热跳过（2026-09-10 无兜底裁决）
        lang = "中文"  # 2026-09-07 日文封存：预热文本与主链路同口径直出中文（参考音语言由 prompt_lang 单独管）
        TMP.mkdir(parents=True, exist_ok=True)
        wav = str(TMP / "_warmup.wav")
        async with _tts_lock:
            ok = await asyncio.to_thread(_run_tts, "你好。", lang, wav, None, voice)
        try:
            os.remove(wav)
        except OSError:
            pass
        logger.info("tts worker warmup: voice={} ok={}", voice, ok)
    except Exception as e:  # noqa: BLE001
        logger.debug("tts warmup skip: {}", e)


def stop_tts_server() -> bool:
    """停止全部常驻 worker（释放显存）；兜底清理旧 8123 HTTP 服务残留。"""
    try:
        with _worker_lock:
            for _v, _p in list(_workers.items()):
                if _p and _p.poll() is None:
                    try:
                        _p.kill()
                        _p.wait(timeout=5)  # 2026-09-08：回收进程句柄
                    except Exception:  # noqa: BLE001
                        pass
            _workers.clear()
            for _v in list(_worker_errfs):
                _close_errf(_v)
        logger.info("tts workers stopped")
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("tts worker stop failed: {}", e)
        return False


def _readline_timeout(p: subprocess.Popen, timeout: float = 60.0) -> bytes:
    """带超时的 readline；超时/读线程异常一律返回 b""（调用方负责杀 worker 释放读线程）。"""
    try:
        fut = _READ_EXEC.submit(p.stdout.readline)
        return fut.result(timeout=timeout)
    except Exception:  # noqa: BLE001
        return b""


def _voice_request(voice: str, rid: str, text: str, lang: str, emo: str | None = None) -> dict:
    """构造 worker 合成请求（纯函数——请求参数口径的单一来源，smoke 行为级断言锚）。
    2026-09-09 深夜（用户裁决，替代同日 A 方案的钉死「默认」）：zh 包按情绪类别选包内参考音——
    钉死「默认」的代价是全程垫一句平稳工作腔参考（棒读投诉根因）；空 ref 的教训保留（A 方案史实：
    空值被 worker 解析成「除默认外第一条」= amiya 惊呼样本，全天语调错位）——类别无映射/未知 emo
    一律显式发「默认」，绝不发空值。温度走 ZH_EMO_PARAMS（语速恒 1.0，无半音变调）。
    奥汀（ja）不变：情绪参考库按台词/基调选（pick_ref），prompt_lang=日文（2026-09-07 跨语言直出中文裁决）。
    2026-09-11 重做（用户裁决）：参数不再发固定值——经 `emotion_params.variant` 在中心值附近
    小幅抽样（同档情绪每次略有不同，消"同一段演绎被复制"的机械感）。中心值仍是下面两张表，
    `data/voice_emotion_params.json` 可覆盖中心值与幅度；变动器关掉即逐字节回到原固定值行为。
    变动只作用于 (温度, 语速, top_p)，**不碰参考音选择**（情绪方向由参考音决定，参数只管能量与稳定）。"""
    _lang = VOICES.get(voice, {}).get("lang", "ja")
    if _lang == "zh":
        _zh_refs = VOICES.get(voice, {}).get("emo_refs") or {}
        ref_name, ref_path = (_zh_refs.get(emo or "", "") or "默认"), ""
        temp, speed, topp = emotion_params.variant(
            emo, EMO_VARIANT_CENTERS, ZH_DEFAULT_PARAMS, base_topp=0.90, cfg=_emo_param_cfg(),
            third_is_topp=True)   # zh 中心表第三位 = top_p
    else:
        ref_name, ref_path = pick_ref(text, emo)
        temp, speed, topp = emotion_params.variant(
            emo, EMO_PARAMS, (1.0, 1.0, 0.0), base_topp=0.90, cfg=_emo_param_cfg(),
            third_is_topp=False)  # 奥汀表第三位 = 半音（后处理链的事，本模块不碰）
    return {
        "id": rid, "text": text, "lang": lang, "ref": ref_name,
        "ref_path": ref_path, "temp": temp, "speed": speed, "topp": topp,
        # prompt_lang（参考语言）= 包语言——必须与参考音频一致，否则 s1 语义错位（丢段/静音）
        "prompt_lang": {"zh": "中文", "ja": "日文", "en": "英文"}.get(_lang, "日文"),
    }


def _run_tts(text: str, lang: str, out_wav: str, emo: str | None = None, voice: str = "") -> bool:
    """常驻 worker 推理（协议行读写；热调用 ~2s）；失败返回 False。
    请求构造收口在 _voice_request（参考音/温度/语速/prompt_lang 单一口径），
    2026-09-06 的语义选参考屏蔽与「默认参考」口径裁决均落在该函数内。"""
    voice = voice or _active_voice()
    try:
        worker = _get_worker(voice)
        if worker is None:
            logger.warning("tts worker unavailable: {}", voice)
            return False
        rid = str(int(time.time() * 1000))
        req = json.dumps(_voice_request(voice, rid, text, lang, emo), ensure_ascii=False)
        with _worker_lock:
            worker.stdin.write((req + "\n").encode("utf-8"))
            worker.stdin.flush()
            line = ""
            _deadline = time.monotonic() + 120.0  # 单次合成总预算（含跳非协议行）；超时按无响应处理
            for _ in range(40):  # 防御：跳过库 print 等非协议行
                if time.monotonic() > _deadline:
                    line = b""
                    break
                line = _readline_timeout(worker, 60.0)
                if not line:
                    break
                try:
                    out = json.loads(line.decode("utf-8"))
                    break
                except (ValueError, UnicodeDecodeError):
                    continue
            else:
                line = b""
            if not line:
                # 无响应/挂死：worker 已不可信 → 杀掉（下次调用重新拉起）；读线程随管道 EOF 自然退出
                try:
                    worker.kill()
                    worker.wait(timeout=5)  # 2026-09-08：回收进程句柄
                except Exception:  # noqa: BLE001
                    pass
                _workers.pop(voice, None)
                _close_errf(voice)
        if not line:
            logger.warning("tts worker no response (voice={})", voice)
            return False
        if not out.get("ok"):
            logger.warning("tts worker error: {}", str(out.get("error", "?"))[:200])
            return False
        with open(out_wav, "wb") as f:
            f.write(base64.b64decode(out.get("wav_b64", "")))
        return os.path.getsize(out_wav) > 1000
    except Exception as e:  # noqa: BLE001
        logger.warning("tts worker call failed: {}", e)
        return False


async def tts_wav(text: str, tone: str = "") -> bytes | None:
    """GAL web 路语音（批次1'，计划表 v2 #4）：出 48k wav bytes，**不进 QQ silk 链路**。
    复用 _voice_request + _run_tts（请求口径单一来源）+ _tts_lock（与 QQ 语音串行，防显存/引擎争用）；
    wav 临时文件用完即删。实施取简定案：页面逐行 POST /gal/tts（client 拉），bot 不推 seg(voice)。
    不查 cfg.enabled——GAL 页"每条回复都有语音"是 gal 模式特征（v2 #1），独立于 QQ 端 /语音 开关。
    返回 None = 合成失败（文本空/worker 不可用/超时），调用方回 {"err":...}。"""
    t = str(text or "").strip()
    if not t:
        return None
    vo = _active_voice()
    emo = tone if tone in EMO_PARAMS else None  # 奥汀情绪演绎参数可用；中文原生包本就中性
    TMP.mkdir(parents=True, exist_ok=True)
    wav = str(TMP / f"webgal_{int(time.time() * 1000)}.wav")
    try:
        async with _tts_lock:
            ok = await asyncio.to_thread(_run_tts, t, "中文", wav, emo, vo)
        if not ok:
            return None
        with open(wav, "rb") as f:
            return f.read()
    except Exception as e:  # noqa: BLE001
        logger.warning("webgal tts_wav failed: {}", e)
        return None
    finally:
        try:
            os.remove(wav)
        except OSError:
            pass


def _to_silk(wav: str, silk: str, emo: str | None = None, voice: str = "") -> bool:
    """48k wav →（奥汀：情绪半音偏移 + F3 高透 EQ）→ 24k NT Silk。
    2026-09-06：中文原生包走中性 EQ（EQ_ZH：不做变调、不做奥汀高透/增益——调音污染原生音色）。
    2026-09-07：voice 由调用方传入（曾内部重取 _active_voice——人设切换瞬间会拿错 EQ/变调链）。"""
    _is_zh = VOICES.get(voice or _active_voice(), {}).get("lang", "ja") == "zh"
    semi = 0.0 if _is_zh else EMO_PARAMS.get(emo or "", (1.0, 1.0, 0.0))[2]
    af = _pitch_filter(semi) + (EQ_ZH if _is_zh else JA_SILK_EQ)
    eqw = wav + ".eq.wav"
    r = subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", "-i", wav, "-af", af, eqw],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        return False
    r2 = subprocess.run(["node", WAV2SILK, eqw, silk], capture_output=True, text=True, timeout=60)
    try:
        os.remove(eqw)
    except OSError:
        pass
    return r2.returncode == 0 and os.path.exists(silk) and os.path.getsize(silk) > 500


# 中日文自动切换：按假名占比判断（≥2 个假名或单假名短句 → 日文）
_KANA_RE = re.compile(r"[\u3040-\u30ff]")


def detect_lang(text: str) -> str:
    kana = len(_KANA_RE.findall(text or ""))
    if kana >= 2:
        return "日文"
    if kana == 1 and len(text) <= 6:
        return "日文"
    return "中文"


# ---- B1 本地回放（2026-09-08，默认关）：TTS 生成 wav 落地后同步在本地默认输出设备播一遍
# （调试用"亲耳听"通道）。.env LIVE_AUDIO_PLAYBACK=true 才启用；关闭时零行为变化、零导入。----
_TRUTHY = {"1", "true", "yes", "on"}


def _env_flag(name: str, default: bool = False) -> bool:
    """读 .env 开关（nonebot driver config 优先——nonebot.init 已载入 .env；os.environ 兜底）。
    纯读函数：驱动未初始化/字段缺失不抛，测试友好。"""
    try:
        from nonebot import get_driver

        v = getattr(get_driver().config, name.lower(), None)
        if v is not None:
            return str(v).strip().lower() in _TRUTHY
    except Exception:  # noqa: BLE001
        pass
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUTHY


def _live_audio_enabled() -> bool:
    """B1 开关：.env LIVE_AUDIO_PLAYBACK（默认 false；关闭零行为变化）。"""
    return _env_flag("LIVE_AUDIO_PLAYBACK", False)


def _play_wav_bytes(data: bytes) -> None:
    """同步播放内存 wav（winsound=Windows 标准库，零新依赖）。阻塞所在线程——只在 to_thread 里跑；
    播放失败只留日志（B1 纪律：绝不影响发送链）。"""
    try:
        import winsound  # 懒导入：开关关闭时零导入成本

        winsound.PlaySound(data, winsound.SND_MEMORY)
    except Exception as e:  # noqa: BLE001
        logger.warning("live audio playback failed: {}", e)


_LOCAL_PLAY_TASKS: set = set()  # 引用火后不管的回放任务（防 GC 中途取消）


def _maybe_play_local(wav_path: str) -> None:
    """B1：wav 落地后交后台线程本地回放（C14 措辞勘误：to_thread 任务火后不管=**异步旁路**，
    不是"并发"——发送链不等它、也不与它共享锁；本机音箱仅放一遍供调试者亲耳听）。异常只留日志。
    用内存副本播放（wav 在发送链 finally 里即删，SND_FILENAME 有删后竞态）。"""
    try:
        data = Path(wav_path).read_bytes()
        task = asyncio.get_running_loop().create_task(asyncio.to_thread(_play_wav_bytes, data))
        _LOCAL_PLAY_TASKS.add(task)
        task.add_done_callback(_LOCAL_PLAY_TASKS.discard)
    except Exception as e:  # noqa: BLE001
        logger.debug("live audio playback skip: {}", e)


async def translate_ja(text: str) -> str:
    """中文 → 日文（中性直译；语气由人设驱动，翻译层不夹带人设——2026-09-06 污染清理）。
    走本地 LLM（llama-server :11434）。失败返回原文本。
    **2026-09-07 日文封存**：主链路不再调用（奥汀直出中文）；仅 /语音日文 诊断路径使用。"""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=45) as c:
            r = await c.post(
                "http://127.0.0.1:11434/v1/chat/completions",
                json={
                    "model": "gemma",
                    "messages": [
                        {"role": "system", "content": (
                            "你是日文翻译。把中文翻译成自然、地道的日文，忠实保留原意与语气。"
                            "只输出译文本身，不加引号、不加解释，长度接近原文。"
                        )},
                        {"role": "user", "content": text[:200]},
                    ],
                    "temperature": 0.4,
                    "max_tokens": 800,
                },
                timeout=45,
            )
            import re as _re

            out = (r.json()["choices"][0]["message"]["content"] or "")
            out = _re.sub(r"<think>.*?</think>", "", out, flags=_re.S)  # 裸 httpx 无 extra_body，手动剥思考段
            out = out.strip().strip("「」\"'")
            if out and 1 <= len(out) <= 300:
                return out
    except Exception:  # noqa: BLE001
        pass
    return text


async def try_make_record_segments(event: MessageEvent, reply: str, user_id: str = "", raw: bool = False,
                                   tone: str = "") -> list[MessageSegment]:
    """语音段列表（不发送）。**2026-09-06 自决 + 长话分多段（像文字一样分段，无字数限制）**：
    决策：① raw=True（/语音测试 等诊断路径强制出声）② 回复带 [VOICE] 标记（bot 自决）→ 发声；
    无机器概率/冷却/次数限制。文本：按句切分，每 ~3 句合成一条语音（组间=独立语音段落）。
    2026-09-07 日文封存（用户裁决）：奥汀保留原声线跨语言直出**中文**（日文参考音 + prompt_lang=日文
    + text lang=中文），不再翻译成日文；日文链路（translate_ja / 语音日文指令）留档不删。
    2026-09-07：清死参数 bot/text/group_like（函数体从未使用）；总开关 enabled 接入本链
    （曾只查 warmup/启动——/语音 关 后 [VOICE] 仍会拉起 worker 出声，死门）。"""
    try:
        cfg = load_cfg()
        # 2026-09-08：总开关只门主链路——曾连 /语音测试 诊断通道一起拦死（语音关时诊断恒报
        # "生成失败"且 TTS 一次都不跑）；raw=True 是诊断直通，必须能到达 TTS
        if not raw and not cfg.get("enabled", True):
            return []  # /语音 关 → 主链路不出声
        is_group = isinstance(event, GroupMessageEvent)
        if is_group and not cfg.get("group_enabled"):
            return []
        uid = str(user_id or event.get_user_id())
        marked = "[VOICE]" in str(reply or "")
        if not marked and not raw:
            return []  # 自决：只有 bot 点头才发声（raw=诊断强制通道）
        # 状态：仅统计（不限次数/冷却）
        st_all = _load_json(STATE_FILE, {})
        today = time.strftime("%Y-%m-%d")
        st = st_all.get(uid, {})
        if st.get("date") != today:
            st = {"date": today, "count": 0, "last": 0.0}
        # 2026-09-08：[WRITE:] 协议标记同剥——语音生成先于 brain 发送侧的兜底剥标，
        # stage2 润色幻觉出的标记会被 TTS 念出来（"继续写第三章"读进语音）
        _clean_target = re.sub(r"\s*\[WRITE:[^\]]*\]", "",
                               str(reply or "").replace("[VOICE]", "")).strip()  # 自决/协议标记不进语音文本
        sents = _sentences_for_voice(_clean_target)
        if not sents:
            return []
        _vo = _active_voice()
        if not _vo:
            logger.info("voice: 当前人设无音色包，跳过语音合成（[VOICE] 不出声）")
            return []
        # 情绪链 zh/ja 同构（2026-09-09 深夜用户裁决：zh 包参考音按 bot 自决情绪选，不再钉死「默认」）：
        # stage2 自标【基调】（标准 9 类直通，与文字/配图同源）优先，LLM 分类（classify_emotion）降级为兜底；
        # 分类失败/无情绪 → emo=None → 各语言走各自默认参考与中性参数（安全侧=退化到 A 方案前中性档）
        if tone and tone in EMO_PARAMS:
            emo, _tone_src = tone, "自标"
        else:
            emo = await classify_emotion("".join(sents))
            _tone_src = "classify" if emo else "回退"
        groups = [sents[i:i + 3] for i in range(0, len(sents), 3)]
        # V3（2026-09-11 热修十二）：基调决策一行日志——tone 标记值/来源（自标/classify/回退）/
        # 最终 emo/所选参考名（此前判定零日志，全天 7 段全落「默认」参考不可观测）。
        # 参考名与 _voice_request 同口径取值（zh 包仅由 emo 决定；odin 按文本选，以首个合成组为代表）。
        try:
            _ref_log = _voice_request(_vo, "log", "".join(groups[0]), "中文", emo)["ref"]
        except Exception:  # noqa: BLE001
            _ref_log = "?"
        logger.info("voice tone decision: voice={} tone={!r} src={} emo={!r} ref={}",
                    _vo, tone, _tone_src, emo, _ref_log)
        segs: list[MessageSegment] = []
        async with _tts_lock:
            for gi, g in enumerate(groups):
                tag = f"{int(time.time() * 1000)}g{gi}"
                wav = str(TMP / f"v{tag}.wav")
                silk = str(TMP / f"v{tag}.silk")
                try:
                    ok = await asyncio.to_thread(
                        _run_tts, "".join(g), "中文", wav, emo, _vo)
                    if not ok:
                        continue
                    if _live_audio_enabled():
                        _maybe_play_local(wav)  # B1：默认关；开=后台线程回放同一 wav 供调试亲耳听（发送链不等它，异常只留日志）
                    ok2 = await asyncio.to_thread(_to_silk, wav, silk, emo, _vo)
                    if not ok2:
                        continue
                    with open(silk, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode("ascii")
                    segs.append(MessageSegment.record(file=f"base64://{b64}"))
                finally:
                    for p in (wav, silk):
                        try:
                            os.remove(p)
                        except OSError:
                            pass
        if segs:
            st["count"] = int(st.get("count", 0)) + len(segs)
            st["last"] = time.time()
            st_all[uid] = st
            _save_json(STATE_FILE, st_all)
            logger.info("voice made: user={} segs={} voice={}", uid, len(segs), _vo)
        return segs
    except Exception as e:  # noqa: BLE001
        logger.debug("voice segs skipped: {}", e)
        return []


async def try_make_record_segment(event: MessageEvent, reply: str, user_id: str = "", raw: bool = False,
                                  tone: str = "") -> MessageSegment | None:
    """兼容旧接口（/语音测试 等）：返回多段语音的第一段。"""
    segs = await try_make_record_segments(event, reply, user_id=user_id, raw=raw, tone=tone)
    return segs[0] if segs else None


async def maybe_send_voice(bot: Bot, event: MessageEvent, reply: str, user_id: str = "", raw: bool = False) -> bool:
    """兼容旧接口：生成后单独发送一条语音（debug 指令等使用）。（2026-09-07：清死参数 text）"""
    seg = await try_make_record_segment(event, reply, user_id=user_id, raw=raw)
    if seg is None:
        return False
    try:
        await bot.send(event, seg)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("voice send failed: {}", e)
        return False


