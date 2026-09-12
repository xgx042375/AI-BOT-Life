# 语音包接入规范（2026-09-06）

## 语言标注（必须！）
每个语音包必须标明**包语言**（`lang`），且必须与**参考音频的语言**一致：

| 包 key | 角色 | lang | 参考音频 |
|---|---|---|---|
| amiya | 阿米娅 | zh（中文） | 阿米娅原声台词（多参考 8 条） |
| kaltsit | 凯尔希 | zh | 凯尔希原声台词（8 条） |
| exusiai | 能天使 | zh | 能天使原声台词（9 条） |
| skadi | 斯卡蒂 | zh | 斯卡蒂原声台词（9 条） |
| lappland | 拉普兰德 | zh | 默认 1 条（台词库未收录，待补） |
| phibia | 菲比 | zh | 默认 1 条（非方舟角色，待补） |
| odin | 奥汀 | ja（日文） | 奥汀日文台词（内建） |

> **教训（2026-09-06）**：中文参考音频曾按 `prompt_language="日文"` 处理（从奥汀旧服务继承的硬编码），
> 导致 s1（GPT）拿到语义错位的 prompt → 随机丢段（丢头/丢尾）、全静音。实测丢段率 75%；
> 修复为「参考语言=包语言」后 5/5 完整稳定（2.24~2.60s，零重试）。

## 语言传递链（如何保证不出错）
1. `VOICES` 注册表（`plugins/voice/__init__.py`）：每个音色带 `lang`（zh/ja/en）+ 注释说明语言
2. `voices/<key>/voice_meta.json`：包元数据（name/lang/source/refs 数）
3. worker 请求带 `prompt_lang`（"中文"/"日文"/"英文"）——插件从包属性取：
   `{"zh":"中文","ja":"日文","en":"英文"}[VOICES[voice]["lang"]]`
4. daemon `get_tts_wav(prompt_language=prompt_lang)`——参考音频按其真实语言走 BERT/清洗管道

## 接入新包流程
1. 解压（zip 内文件名 GBK 编码 → 用 python zipfile + `encode('cp437').decode('gbk')` 还原）
2. `python tools/install_voice_packs.py`（PACKS 表加 `(角色名, lang)`；幂等）
3. `python tools/build_voice_refs.py <key> <char_id>`（仅方舟角色有台词库；参考音 3-10s、自动语速归一 4.5 字/s）
4. 注册表 VOICES 加条目（personas/lang/ref_dir）；**lang 与参考音频语言一致**
5. 协议测试（文件重定向，确认合成）+ selftest 发送用户终审

## 已知参数（中文包）
- temp 0.7 / speed 1.0（语速由参考音归一控制）
- 中性 EQ（EQ_ZH：12k 低切 + 70 高切 + 限幅；**不用奥汀 F3 高透链**——那是日文音色调音）
- 不打情绪变调（EMO_PARAMS 仅奥汀；中文音色跳情绪分类）
- 语义选参考（embedding 余弦 top1；来源 `plugins/memory/embeddings.py`，11435）——**2026-09-06 已禁用**（用户试听判定语义匹配音色偏尖）；当前 daemon 默认参考 = `refs/voice_refs.json` 首个非"默认"条目，稳定为先
