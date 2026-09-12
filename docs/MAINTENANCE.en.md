# Maintenance Manual (sole authority · for users and on-call maintenance)

> **What this is**: it answers "where does each switch live, does changing it need a restart, what do I check first when
> something breaks, what do I back up before touching a database".
> **What this is not**: it does not cover code structure or decision history (that is `HANDOFF.md` (*Chinese*) and `docs/archive/`).
> **Authority statement** (2026-09-12 documentation slimming): this document supersedes `操作方法-运维手册-2026-09-12.md` (*Chinese*);
> where the two conflict, **this document wins**. For the documentation map, see the documentation navigation table in `README.md`.
> Maintenance convention: **any work that changes user-visible behavior must come back and update the matching section here** — a
> manual that is not updated means the work was not done.
> **Language note**: documents that exist only in Chinese keep their Chinese filenames and are marked (*Chinese*); where an English
> version exists, it is named directly.
> <!-- translator note: the source says only `README.md`; the documentation navigation table referred to here is the one in `docs/README.md` (see §3.1). -->

---

## 0. Five-minute orientation

```
First install        →  see `DEPLOYMENT.en.md` (§1 Fresh install · §2 Feature → dependency matrix · §4 Relocating)
Start                →  double-click start.bat   (engine + embedding + NapCat + bot, one shot)
Watch logs           →  data\bot.log, data\llama-server.err.log
Stop                 →  stop.ps1  or  the launcher's "Stop"
Change settings      →  the launcher's Settings page (writes .env / data\*.json), **restart the bot afterwards**
Something is broken  →  §7.1 quick troubleshooting first; then §3 for logs, §4 for state files
Missing components   →  the launcher's Components page (or `Launcher.ps1 -Mode deps`): which feature needs what, and what breaks without it
Installed packs      →  the launcher's Content packs page (or `Launcher.ps1 -Mode packs`): four kinds — content packs / stage packs / skins / plugins;
                        the detail pane on the right gives author · license · source · directory + **what is installed** (counts read from the files inside the pack) + **activation conditions**
Before touching a DB →  copy data\memory.db to vault\backups\ first (see §8)
```

<!-- translator note: the step list above is an outline of what the scripts print, not a verbatim quotation of their console output. -->

---

## 1. Starting, stopping and process topology

### 1.1 Three start paths (**behavior must be identical, values synced across three sources**)

| Path | Purpose | Notes |
|---|---|---|
| `start.bat` | everyday one-click | hands off to `start.ps1` and then `pause`s (so a double-click leaves the error visible) |
| `start.ps1` | command line / troubleshooting | steps below; carries a double-launch lock |
| `launcher\QQAI-Launcher.exe` | GUI | GUI + settings page + card wall + embedded home/GAL pages; **"Stop" needs administrator rights** (the command line of an elevated process reads as NULL) |

The step order in `start.ps1`:

```
[0/3]   Clean up leftover bot processes (taskkill /T /F kills the process tree; touches only the bot, never the engine/NapCat)
[1/3]   Inference engine llama-server (11434) — skipped if already running
[1.5/3] embedding service (11435, CPU) — semantic memory recall
[2/3]   NapCat (new window; scan the QR code on first run) — the QQ channel
[3/3]   bot (8080) — this window shows the log and also writes data\bot.log; Ctrl+C = full exit
```

> `start.ps1` first probes whether 8080 has been released; if it has not been released after 15 seconds it still tries to start
> and says so explicitly (rather than reporting a blanket "stopped").

### 1.2 Process topology and ports

| Port | Process | Role | What breaks without it |
|---|---|---|---|
| **11434** | `llama-server.exe` | main chat model (Gemma 4 12B heretic Q4_K_M) | **everything is down** (unless you use an external API: see the §2.1 presets) |
| **11435** | `llama-server.exe` (a second instance) | embedding: semantic memory recall | degrades to keyword search, nothing is down |
| **8080** | `python` (NoneBot2) | the bot itself + GAL page HTTP/WS | everything is down |
| **3000/other** | NapCat (`tools\napcat\`) | QQ protocol side (OneBot v11 reverse connection) | **only the QQ channel disappears**; GAL/web mode still works |

> **The Python-side port constants already have a single source** (2026-09-12 item E): `ENGINE_PORT` / `EMBED_PORT` /
> `ENGINE_URL` / `ENGINE_BASE_URL` / `EMBED_URL` / `EMBED_BASE_URL` in `qq-bot\core\paths.py`
> — `core\llm.py`, `plugins\brain` and `plugins\debug` all read them from there, so changing a port means changing one place.
> **The PS side** (`start.ps1` / `stop.ps1` / `Launcher.ps1` / `build_release.ps1`) cannot import Python constants and can only
> **align the values** → `smoke_test.py` §36 pins them parameter by parameter (change a value and you must change those three
> places too, or smoke fails outright).
> Machine check: item E of `qq-bot\tools\dev\audit_consistency.py` (see §7.3).

### 1.3 Engine start parameters (**the current authoritative values**)

```
-c 32768 -ngl 99 --parallel 1 -ctk q8_0 -ctv q8_0
--reasoning on --reasoning-budget 200
--repeat-penalty 1.15 --repeat-last-n 256 --spec-type ngram-simple
```

- `--parallel 1`: a single slot (measured 2026-09-08: decode 16.7→32-45 tok/s)
- `--spec-type ngram-simple`: lossless speculative decoding, zero VRAM cost
- **Changing parameters requires syncing three sources**, or `smoke_test.py` fails: ① `start.ps1` ② `MODELS["gemma"]["args"]` in `plugins\brain` (shared by watchdog restarts and `/模型` switching) ③ `plugins\debug`
- ⚠️ `tools\start-llama-server.ps1` is an **old script** (it writes `--reasoning off` and points at a deleted Qwen3-14B) — **do not use it to start the engine**

---

## 2. Launcher settings reference

> **General rule**: every item that writes `.env` **needs a bot restart** (the Python process reads its config at startup and
> does not hot-reload).

| UI location | Control | Written to | How it takes effect |
|---|---|---|---|
| Start options | Startup voice (TTS) | `data/voice_mode.json` | immediate |
| Start options | Live danmaku / playback switch | `.env` `LIVE_DANMAKU_ENABLED` / `LIVE_AUDIO_PLAYBACK` | restart |
| Start options | Keep chat-app mode across restarts | `.env` `WEBGAL_CHAT_PERSIST` | restart |
| Start options | Skin | the `skin` key of `data/launcher.json` | **re-navigates on save** (no restart needed) |
| Start options | UI language (Chinese / English) | the `lang` key of `data/launcher.json` | **immediate** (chosen in Settings; the skin page and GAL page follow) |
| Model & API | Backend type (10 presets) | `.env` `LLM_PROVIDER` (`local` / `openai_compat`) | restart |
| Model & API | Apply preset ↦ the three boxes | only **fills base_url + the suggested model name into the input boxes** (nothing is persisted; you still have to save) | —— |
| Model & API | **Profile** (local / online, new 2026-09-12) | the two sets live in `data/launcher.json` as `llmProfiles.{local,online}`; switching a profile only loads its values into the three boxes and **leaves `.env` alone** | —— |
| Model & API | Activate this profile ↦ write `.env` | writes that profile's four keys **explicitly** into `.env` (the local profile writes an empty URL/model → the 11434 + gemma fallback chain applies) | restart |
| Model & API | Boxes ↦ save into this profile | stores the current box values into the selected profile (`launcher.json`), **not** `.env` | —— |
| Model & API | **Fetch models** (button, new 2026-09-12) | asks the endpoint for its list (OpenAI-compatible `GET {base}/models`) using the current base URL + API Key, fills the dropdown on the right; picking one writes it into the model-name box. **Nothing is persisted** | —— |
| Model & API | API base URL / API Key / model name | `.env` `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | restart |
| Identity | Owner QQ | `.env` `SUPERUSERS` (**a JSON array**) | restart + **you must run the uid migration** (§3.3) |
| Identity | Owner nickname | `.env` `OWNER_NICKNAME` | restart |
| Generation & thinking | Thinking three-state | `data/think_mode.json` | restart |

> **Changing the QQ number has a second confirmation**: the Identity section of the settings page has a confirmation checkbox;
> if you do not tick it, **the QQ number is not written** (the other items save as usual).

<!-- translator note: the source points at §3.3 for the uid migration; the migration procedure actually lives in §4.2. The reference is kept verbatim. -->

### 2.1 Backend presets (10 items, **all over the OpenAI-compatible protocol**)

| Display name | base_url | Writes `LLM_PROVIDER` |
|---|---|---|
| Local engine (local · llama-server) | `http://127.0.0.1:11434/v1` | `local` |
| OpenAI-compatible (generic) | *left blank to fill in* | `openai_compat` |
| DeepSeek | `https://api.deepseek.com/v1` | `openai_compat` |
| Alibaba Cloud Bailian DashScope (compatible mode) | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `openai_compat` |
| Ollama | `http://127.0.0.1:11434/v1` | `openai_compat` |
| vLLM / LM Studio (self-hosted) | *left blank to fill in* | `openai_compat` |
| OpenRouter | `https://openrouter.ai/api/v1` | `openai_compat` |
| Zhipu GLM | `https://open.bigmodel.cn/api/paas/v4` | `openai_compat` |
| Moonshot Kimi | `https://api.moonshot.cn/v1` | `openai_compat` |
| Custom / through a protocol proxy | *left blank to fill in* | `openai_compat` |

> ⚠️ **Protocol boundary**: for native **Anthropic Messages / Gemini generateContent** this project **writes no conversion
> code** — `core/llm.py` only splits "local / non-local". If you need a native protocol: **run a protocol proxy on your own
> machine** (LiteLLM / llm-rosetta / api-protocol-converter) and put **the proxy's address** in "API base URL".
> ⚠️ Non-local endpoints do **not** get `chat_template_kwargs` (`thinking_extra`) injected — deliberate design, do not change it.
>
> **Model names have a shelf life (corrected 2026-09-12 from a user report)**: the preset used to say `deepseek-chat`,
> which has long been retired; DeepSeek's current **version-less** id is `deepseek-flash` (V4.1 Flash). The older idea of
> "a separate model name for the thinking tier" (`deepseek-reasoner`) no longer applies either — the thinking tier is now
> a **parameter** (`LLM_THINKING_PARAM` and friends). The policy here is therefore:
> **a preset only guarantees the endpoint address** (the right-hand column of the table above is the single source for that),
> and any model name we cannot verify is **left empty** — use the settings page's **Fetch models** button to pull the live
> list from the endpoint. Maintenance rule: writing a model name into the presets requires a date **and** a source
> (official docs or a real `GET /models`), otherwise nobody will dare touch it six months from now.
> Changing the presets means changing `Get-ProviderPresets` in `launcher/Launcher.ps1`.
>
> **Local / online profiles (2026-09-12, from a user report: "I switched to online, then switched back, and nothing changed")**:
> there is only one `.env`, so switching to online overwrote the local set of values — switching back therefore changed
> nothing at all. Now the two sets live in `data/launcher.json` (`llmProfiles.{local,online}`, machine-local and gitignored)
> and `.env` carries only the **activated** set. A status line under the profile row prints "what `.env` actually says" next
> to "what this profile says" and, when they differ, tells you which button to press (activate, or save into the profile).
> The local profile **stores empty values**: empty means "unset", and the `core/llm.py` fallback chain then yields
> `http://127.0.0.1:11434/v1` + `model=gemma` + `key=ollama`. Hard-coding those instead would be the risky choice —
> it would silently stop working the day the engine alias changes.
>
> ⚠️ **The silent trap (the old implementation actually hit it)**: in `core/llm.py`, `llm_provider` **only decides whether
> thinking gets injected**; the endpoint comes from `llm_base_url`. So with `LLM_PROVIDER=local` while `LLM_BASE_URL` still
> points at a cloud endpoint, **requests keep going to the cloud** (burning API credit without telling you). The old save
> path only wrote non-empty values, which is exactly why it could never clear that state. Activating a profile now writes
> empty values explicitly, and the status line raises an alarm for that combination.
>
> **Full-API mode (no local engine needed)**: point `LLM_BASE_URL` at an external endpoint and 11434 can stay off entirely.
> This is how "the model is not mandatory" is realized (releases are designed around it: model, QQ and voice can all be missing).

### 2.2 Config file write matrix

| File | Who writes it | Is hand-editing safe |
|---|---|---|
| `qq-bot\.env` | the launcher's settings page / by hand | ✅ but `SUPERUSERS` must be a JSON array |
| `data\launcher.json` | the launcher | ✅ the `skin` key |
| `data\think_mode.json` | the launcher / the `/思考` command | ✅ deleting the key = self-decide |
| `data\persona_select.json`, `persona_switch_ts.json` | the bot at runtime | ⚠️ do not hand-edit (§4.2) |
| `qq-bot\data\personas\*.json` | **by hand** (character cards) | ✅ must be **BOM-free** |
| the remaining `data\*.json` | the bot at runtime | ❌ do not hand-edit; stop the bot first |

---

## 3. Directory, log and state-file map

### 3.1 Directory map

```
E:\robot\
├─ start.bat / start.ps1 / stop.ps1      start / stop
├─ LICENSE / README.md / .gitignore
├─ qq-bot\                               ★ our own core (Python)
│  ├─ bot.py  pyproject.toml  .env  .env.example
│  ├─ core\       paths / atomic writes / content packs / LLM / protocol stripping
│  ├─ agent\      LangGraph graph + life simulation + tool registry
│  ├─ plugins\    brain/persona/memory/voice/sticker/webgal/fiction/debug/...
│  ├─ harness\    offline scenario driver (regression guardrail)
│  ├─ tests\      smoke_test.py (945-assertion gate) + focused tests
│  ├─ tools\      project scripts (export_cards / migrate_uid / dev\ diagnostics)
│  ├─ packs\      example content packs shipped in-repo (robot-pack-v1 live sample)
│  └─ data\       character cards / sticker assets / training corpus (**bot-private** legacy location)
│                 ↑ note: this is the **content root** (not gitignored), not the user-data root below
├─ launcher\                              ★ the launcher
│  ├─ Launcher.ps1  QQAI-Launcher.exe    main program (ps2exe output)
│  ├─ web\         home page + GAL client + state.json
│  │   └─ skins\   skin packs (generic built in / others you install)
│  ├─ cards\       character portraits and variant assets (**private assets**, gitignored)
│  ├─ ref\ webview2\ webdata*\           reference material / WebView2 runtime / browser user data
├─ tools\                                 ★ third-party runtimes (gitignored, 62 GB)
│  ├─ llama.cpp\ llama-server.exe (used by both 11434/11435)
│  ├─ napcat\      QQ protocol side
│  ├─ GPT-SoVITS*\ speech synthesis
│  └─ cudart\ wheels\ conversion\ and so on
├─ data\                                  ★ user-data root (the only one)
│  ├─ memory.db    memory store (SQLite)
│  ├─ models\      gemma4-12b / embedding / lora
│  ├─ voices\ voice_refs\  voice assets
│  ├─ life_states\ per-card life-state archive
│  ├─ *.json       state files (see §3.3)
│  └─ *.log        logs (see §3.2)
├─ docs\           documentation (entry = docs\README.md)
└─ vault\          gitignored private area: backups\ db-surgery\ verify-tools\ ...
```

<!-- translator note: the tree says 945 assertions here while §7.2 records the 949 PASS baseline; both figures are copied verbatim from the source. -->

> **Moving to another install disk (relocating `E:\robot`)**: every core path is derived by `core\paths.py`; on 2026-09-12 the
> hard-coded spots in `brain` / `debug` / `webgal` were closed. The known leftovers are in §9.

> ⚠️ **Two `data` roots with different semantics — this is the single easiest line in this repo to misread (audit item I checks
> exactly this):**
>
> | Root | Nature | What goes in it |
> |---|---|---|
> | `E:\robot\data` (i.e. `core.paths.DATA_ROOT`, **gitignored**) | **state root** | memory store / state JSON / logs / models / voice assets |
> | `qq-bot\data` (**not ignored**) | **content root** | in-repo content: `memes.json` / `scenes.json` / `colloquial_style.txt` / `common_phrases.txt` / `scale_baseline.txt` / `odin_style_sample.txt`, card templates |
>
> So in `Path(__file__).resolve().parents[N] / "data"` **an off-by-one in N switches the root — and reports no error**
> (it just silently reads the other copy, or reads nothing). Always write state through `core.paths.data_path()`; when you really
> do need the content root, write why on that line (audit item I lists the unexplained ones as ❌). Legacy history and
> distribution decisions: `docs\遗留任务.md` (*Chinese*) A9.
>
> **2026-09-12 close-out**: all 33 `parents[N]` derivations under the canonical root were changed to `DATA_ROOT` (one mechanical
> migration + four rounds of re-verification); **only the 8 content-root exceptions remain** (brain 2 / persona 4 / correction 1 /
> memory 1), each carrying a comment or `# audit-ok:`.
> Writing `parents[N] / "data"` to point at the canonical root now earns a ❌ from item I — that idiom is exactly where
> "wrong depth → silently reads empty" comes from.

### 3.2 Log map

| File | Content | When to look at it |
|---|---|---|
| `data\bot.log` | bot main log (loguru) | **look here first** (the launcher's Log page shows it by default too, see below) |
| `data\bot.err.log` / `bot.out.log` | startup stdout/stderr | when the bot will not start |
| `data\llama-server.log` / `.err.log` | engine log | engine will not start, thinking has no effect, VRAM problems |
| `data\llama-server.diag.log` | **historical snapshot (not live)** | ❌ do not use it to judge the current parameters |
| `data\embedding.log` / `.err.log` | embedding service | when memory recall degrades |
| `data\bot_hook_trace.txt` | hook trace (for diagnostics) | digging into the plugin chain |
| `launcher\webstep.log` | launcher WebView step log | launcher blank page / home page will not load |
| `launcher\boot_err.log` / `web_cmd.log` | launcher crash trace / command trace | when the launcher exits abnormally |

### 3.2.1 Will the logs fill the disk? (**checked item by item**)

| Log | How it is written | Bounded? |
|---|---|---|
| `data\bot.log` | loguru (`rotation=10MB, retention=3`) | ✅ bounded |
| `data\bot_hook_trace.txt` | truncated with `"w"` on bot start | ✅ bounded |
| `data\llama-server.log` / `.err.log` / `embedding.*.log` | `Start-Process -Redirect*` (**truncated on every start**) | ✅ bounded within a single session |
| `launcher\webstep.log` | the launcher's `WEBLOG` | ✅ **fixed 2026-09-12**: over 2 MB it keeps the last 2000 lines (before that it had **no limit at all**; measured at 9.3 MB) |
| `launcher\boot_err.log` / `web_cmd.log` | appended only on a crash/command | ⚠️ unbounded, but written extremely rarely (~10 KB each) |

**To clean them up** (all safe to delete; recreated automatically at runtime):

```powershell
# logs only — leaves state and memories alone
Remove-Item <install-root>\data\*.log, <install-root>\data\bot_hook_trace.txt -ErrorAction SilentlyContinue
Remove-Item <install-root>\launcher\*.log -ErrorAction SilentlyContinue
```

> ⚠️ **Do not delete**: `data\memory.db` (memories), `data\*.json` (state), `qq-bot\data\personas\` (character cards).
> Logs and state live in the same directory — look at the extension when cleaning up.
>
> **Why this section exists**: unbounded log growth is a hidden failure that "keeps growing and is outside the view of every
> test". During the 2026-09-12 audit `webstep.log` measured 9.3 MB and grew on every operation — no assertion will ever tell you
> this; only a human can check "who writes it, and is there a limit".

### 3.3 State-file map (`data\`)

| File | Semantics | Who writes it |
|---|---|---|
| `memory.db` | **the only memory store** (messages/facts/events/emotions/relations/profiles…) | the memory plugin |
| `agent_checkpoints.db` | LangGraph decision replay cache (**rebuildable**, not memory) | agent |
| `life_state.json` + `life_states\<card>.json` | life state (doing/scene/mood) | lifesim |
| `special_state.json` | special-play state (bucketed per card under `cards.<card-key>`) | core.special |
| `special_content.json` | **display names and default item-effect text for the special tier** (content side; externalized out of the code on 2026-09-12, see §7.3 K/O and `R18分层-spec`). A missing file = a neutral default, no error | core.special |
| `persona_select.json` / `persona_switch_ts.json` | the current card + the **memory filter baseline** | persona |
| `think_mode.json` | thinking three-state (`on`/`off`/absent = self-decide) | the launcher / `/思考` |
| `model_mode.json` / `voice_mode.json` / `webgal_mode.json` | engine model / voice / GAL mode | the respective plugins |
| `intimate_whitelist.json` / `group_ban.json` | private whitelist / group ban | the respective plugins |
| `cards.json` / `quotes.json` | launcher card-wall data / GAL quote overlay | export_cards / by hand |
| `gal_history.jsonl` / `life_log.jsonl` / `life_diary.jsonl` | GAL recall / life trail / diary (append-only) | webgal / lifesim |
| `sticker_state.json` / `echo_state.json` / `proactive_state.json` | sticker / echo / proactive-speech state | the respective plugins |
| `slang_*.json` / `vocab_base.json` / `net_slang.json` | word lists and corpora (generated artifacts; deleting them rebuilds them) | the respective plugins |
| `webgal_token.txt` | GAL page WebSocket auth token | webgal |

<!-- translator note: `R18分层-spec` is kept verbatim; the source gives it neither a path nor a file extension. -->

---

## 4. Identity, memory and facts

### 4.1 Owner QQ (`SUPERUSERS`)

**It must be a JSON array** — not a bare string, and not a bare number:

```
SUPERUSERS=["10001"]
```

The launcher writes this format **automatically** when saving (it self-validates before writing). When hand-editing `.env`,
follow this format exactly.

> ⚠️ **The two wrong forms fail asymmetrically**:
>
> | Form | Bot side (nonebot `Config.superusers: Set[str]`) | Launcher side |
> |---|---|---|
> | `["10001"]` | ✅ `{'10001'}` | ✅ |
> | `10001` | ❌ **pydantic validation fails → the bot will not start** | parses fine (no error) |
> | `"10001"` | ❌ **pydantic validation fails → the bot will not start** | parses fine (no error) |
>
> **The danger**: the launcher reports no error for either wrong form, so nothing looks wrong in the GUI — but the bot will not
> start. **Always write an array**.

### 4.2 ⚠️ Changing the QQ number = changing identity (must read)

**Every memory / relation / fact in `memory.db` is isolated by `user_id`**, and the JSON state is keyed by uid too. Editing
`.env` directly gets you: the whole memory store unreadable (equivalent to a brand-new user), and relation values / card
selection / thinking mode / the agreement ledger all de-linked.

**Correct procedure**:

1. Launcher → Settings → Identity → change the QQ number → **tick the confirmation box** → save
2. Run the migration tool (**dry-run only reports, by default**):
   ```
   cd E:\robot\qq-bot
   .venv\Scripts\python.exe tools\migrate_uid.py --from <old-QQ> --to <new-QQ> --dry
   .venv\Scripts\python.exe tools\migrate_uid.py --from <old-QQ> --to <new-QQ> --apply
   ```
3. Restart the bot
4. Spot-check: `/状态` (*status*) showing the old relation values = the migration succeeded

Tool coverage: **every** table in `memory.db` that has a `user_id` column (found by `PRAGMA table_info`; currently 13 tables)
+ uid keys in `data\*.json` (only files that actually have such a key). It explicitly **does not touch**
`data\agent_checkpoints.db` (the uid is encoded inside `thread_id` and checkpoints are msgpack BLOBs — flip one byte and the
thread is corrupt).

Safety constraints (built into the tool): `--dry` is the default; `--apply` first takes a full backup to
`vault\backups\memory.db.bak_before_uidmigrate_<timestamp>`; in-database changes run in a single transaction and roll back
entirely on failure; if the target uid already has data it prints a warning (this is a merge, not an overwrite); when nothing
needs migrating, the no-op does not back up.

### 4.3 The two semantics of facts (**by design, not a bug**)

| Prefix | On a card switch | What may be stored under it |
|---|---|---|
| `user_*` | **kept permanently** | user profile + **RP role positioning, relationship promises, orientation preferences** (the "simulated-companion cheat") |
| anything else | filtered by `persona_switch_ts` | this card's setting, relationship state |

> After a card switch, facts of the "deity / identity" kind belonging to the old card are filtered out automatically, but RP
> preferences under `user_*` survive across cards — intentional.

### 4.4 Memories wrong after switching cards? Check `persona_switch_ts.json`

That file is the **filter baseline for memories and facts**. If a switch was not recorded → the filter window is off by one
(possibly letting the previous card's setting through).

```
type E:\robot\data\persona_switch_ts.json
```

The `ts` of the last entry should be the time of the **most recent switch**. If it lags → the switch was not recorded; run
`/人设` (*persona*) once more to trigger a record.

### 4.5 Memory-store backup habit

`data/memory.db` is the **only memory store**. Copy it to `vault\backups\` before any of the following: any database-surgery
script, a uid migration, large-scale fact cleanup. Naming reference: `memory.db.bak_before_<reason>_<date>`.

### 4.6 2026-09-12 memory-store cleanup (executed, archived · fully reversible)

| Batch | What was deleted | Before → after |
|---|---|---|
| One | **Group data** (independent of dates): `messages` group rows / `topic_knowledge` / `group_user_meta` / `group_style` | 15826 / 680 / 497 / 13 → **all 0** |
| Two | **Private-chat derived data before `2026-09-01`**: `messages` / `facts`(+embeddings) / `events`(+embeddings) / `emotions` / `daily_reviews` (judged by the `date` column) | messages 20054→**2508**; facts 291→**137**; emotions 2377→**1219**; daily_reviews 9→**5** |
| Three | synthetic test uids in the `relation` table (`u-sc-*` / `u-sw-*` / `u-f-*` / `u-dbg-*`, all `priv=0 grp=0`) | 585 → **8** (the real identities) |
| Result | the database file | 15.2 MB → **7.1 MB** |

- Script `vault\db-surgery\db_cleanup_20260912.py` (dry and apply run the same SQL)
- Backups `vault\backups\memory.db.bak_before_cleanup_20260912` + `vault\backups\pre_plan_exec_20260912\`
- Zero-loss verification: all 8 real QQ identities kept, the owner's `intimacy=100.0 / peak=100.0` unchanged

---

## 5. Chain-of-thought (reasoning)

### 5.1 How it is actually enabled today

| Layer | Explanation |
|---|---|
| Engine capability | `llama-server` is started with `--reasoning on --reasoning-budget 200` (the budget = the maximum tokens for a single thought) |
| Per-request switch | each turn the bot decides whether to inject `chat_template_kwargs.enable_thinking` (`core/llm.py`) |
| Who can turn it on | ① the user's `/思考 开` (*thinking: on*) (writes `data/think_mode.json`, persistent) ② the bot self-tagging 【认真】 (*serious*) (a 30-minute TTL) ③ **a machine criterion hit** (one-way on, see below) |

### 5.2 Machine criteria (**one-way on**: it only turns thinking on, never off; when nothing matches, the agent still decides for itself)

Meeting **any one** of these turns thinking on for the current turn:

1. the user message this turn is **> 120 characters** (★ counted as **characters** — see the i18n caveat in §5.5)
2. this turn mentions a **worldview character** (a hit in the character-relation store)
3. **the previous turn triggered a hard-boundary retry** (content acceptable, but the format kept failing)

### 5.5 What language the chain-of-thought uses (2026-09-12, adjusted along with the full open-sourcing / i18n push)

- **Before**: the `THINK_FLOW_GENERIC` prompt hard-coded "**use Chinese** … English forbidden" → English users got a Chinese
  inner monologue.
- **Now**: changed to "**use the language you and they are using right now**" — it follows the conversation language (the
  framework already has language awareness: `_lang_note` → the 【消息语言】 (*message language*) note in the system prompt),
  **no new switch was added**.
- **Why there is no language switch**: following the conversation is more natural than a switch; if someone wants "always
  answer in Chinese", that is a **card / content** matter (one line in `response_rules`), consistent with "capability stays in
  the framework, content follows the card".
- ⚠️ **Unhandled, awaiting a decision**: §5.2 criterion 1 is a **character-count** threshold — 120 English characters is only
  about 20 words, so English users will trip thinking on very often by accident (each hit costs 10–20 s on the local engine).
  Internationalizing it would require a per-language threshold, but that is a **length-type machine limit**, and the project's
  iron rule states "no new length-type limits without user confirmation / stop loop-tuning parameters" → **left untouched**.

### 5.3 Three-state switch

Launcher → Settings → Generation & thinking: **self-decide** (the default, equivalent to deleting that uid key) / **always on**
/ **always off**.
The `/思考 开|关|auto` command is equivalent to it (`auto` = self-decide).

### 5.4 Changing reasoning-budget (all three sources must be synced)

The value in service is **200**. Changing it requires changing all three places (§1.3), and `smoke_test.py` has an assertion
pinning "all three sources are 200 and 400 must not linger" — **if you change the value, change the assertion too**.

**Historical values for reference**: `400` (early) → `120` (measured 09-08 as **actively harmful**: thinking is a "filter", and
cutting it too short means not filtering at all and pouring the output straight out, which made it more exaggerated) → `200`
(the settled value after 09-05).

**Cost**: enabling thinking locally costs about **+5~6 seconds** of latency (far cheaper over a network API).

### 5.5 Troubleshooting: why thinking never turns on

1. Does `data/think_mode.json` have `"off"` for that uid → release it with `/思考 auto`
2. In the log, `[思维链] user=... 思考=关` (*[chain-of-thought] user=... thinking=off*) → confirms it is off
3. Search `data/llama-server.err.log` for `reasoning-budget` → if `activated` and `deactivated` appear **immediately adjacent, as
   a pair**, the channel is open but the model produced no reasoning content (i.e. no thinking happened)
4. Do not use `llama-server.diag.log` to judge the current parameters (a historical snapshot)

<!-- translator note: the source numbers both this section and the language section §5.5, and orders them 5.1, 5.2, 5.5, 5.3, 5.4, 5.5. Both numbers and the order are preserved so that cross-references from other documents keep resolving. -->

---

## 6. Stickers (two libraries · 9 emotion classes)

> **The layout changed on 2026-09-12** (owner decision D10). The old 24-emotion + `{persona}-{state}-{emotion}`
> three-dimensional directory scheme is **fully retired** — do not place images according to the old structure again.

### 6.1 Current layout

| Library | Directory | Purpose |
|---|---|---|
| Common library (cross-card) | `qq-bot\data\stickers\_common\<emotion>\` | **the main library**, usable by any card |
| Card-specific (optional) | `qq-bot\data\stickers\<card-key>\<emotion>\` | the card field `sticker_dir` points at that card's root |

**`<emotion>` must be one of the 9 classes from the emotion classifier, character for character**:

```
开心 / 难过 / 生气 / 惊讶 / 害怕 / 厌恶 / 平静 / 委屈 / 害羞
```

> Those nine are, in order: *happy / sad / angry / surprised / afraid / disgusted / calm / aggrieved / shy* — the directory
> names are what the code matches, so keep them in Chinese.
>
> Directory names only work if they match **character for character** — "震惊" (*shocked*), "得意" (*smug*) and "撒娇" (*coy*)
> are not in the vocabulary and will **never be selected**.
> Asset rules: **no size or format restriction** (png/jpg/jpeg/webp/gif all work), **no naming rule** (emotion comes from the directory).

**Image resolution order** (`plugins/sticker/_pick_file`):

```
card-specific (that emotion) → common (that emotion) → card-specific (any emotion) → common (any emotion) → send nothing
```

Failing to resolve one **silently sends nothing** (no error, the reply is not blocked). When an `emotion_hint` exists, **that
emotion is used as-is, not re-picked**.

> ⚠️ **Of the two sources of a hint, only one takes effect**:
> the model writing `[STICKER:开心]` (**the 9 emotion words**) ✅ works; a self-tag 【基调：温柔】 (*tone: gentle*) on this
> message (**the 9 `voice.TONE_CLASSES`**: 高冷/傲娇/讥讽/战斗/温柔/无语/娇嗔/疑问/纯H, i.e. *aloof / tsundere / sarcastic /
> combat / gentle / speechless / coquettish / questioning / pure-H*) ❌ **resolves to empty → falls back to the judge picking
> one**. The two tables intersect only on 「无语」 (*speechless*).
> This is an intentional state from the **2026-09-12 owner decision "keep as is, do not fix"**: 【基调】 (*tone*) only drives
> **voice delivery**, not image selection.

### 6.2 Known state of things

- The common library's 9 emotion directories **have all been created but hold not a single image** → **the sticker feature
  currently sends no images at all** (to enable it, see §6.3)
- The old lexical directories (13 of them, 156 images) sit at the **root level** of `data\stickers\`; the new code only
  recognizes `_common\` or `<card-key>\` → **unreachable**.
  **Owner decision: keep them in place, do not migrate, do not add alias mapping, do not delete** — deliberate, not an omission.
- The Odin-era variant library is at `E:\comfyui\lib\emotes` (528 subdirectories / 1584 images): **an external artifact, this
  project does not hook it up** — if it cannot be resolved, nothing is sent, silently
- The `ALAPI 斗图` (*meme-battle*) API **has been removed from the code** — stickers are **purely local premade assets**, not a
  ComfyUI generation pipeline

### 6.3 Adding stickers for one card

**The least work** (no card edit needed): drop the image into the matching emotion directory of the common library.

```
qq-bot\data\stickers\_common\开心\whatever.png
```

**Card-specific**: ① put images in `qq-bot\data\stickers\<card-key>\<emotion>\`; ② point the card's `"sticker_dir"` at that
card root; ③ restart.
> The card file must be **BOM-free** (a BOM breaks parsing). An **empty `sticker_dir` no longer cuts the whole chain**
> (fixed 2026-09-12): leave it blank and the common library is used.

---

## 7. Troubleshooting and self-test

### 7.1 Quick troubleshooting

| Symptom | Check first | What to do |
|---|---|---|
| Changing a setting has no effect | —— | **did you restart the bot** (Python does not hot-reload config) |
| Thinking never turns on | `data/think_mode.json` | release the lock with `/思考 auto`; see §5.5 |
| Replies become `唔……` (*"mm…"*) / extremely short | search `data/bot.log` for `hard-boundary retry exhausted` | the fallback after hard-boundary retries are exhausted; if it happens often, report it to the dev side |
| Memories cross over after a card switch | `data/persona_switch_ts.json` | see §4.4 |
| All memories/relations gone after changing the QQ number | `.env` `SUPERUSERS` | **you forgot the uid migration**; see §4.2 |
| Stickers never send | `sticker_dir` + `_common\<emotion>\` | directory names must be the 9 classes **character for character**; failing to resolve an image is silent |
| Group @ gets no reply | search the log for `scene` / the private-scene gate | scene field lagging (fixed 09-11; if it recurs, report it) |
| Engine will not start | `data/llama-server.err.log` | check VRAM usage and whether the model file exists |
| The launcher's "Stop" does nothing | —— | the launcher must run **as administrator** (the command line of an elevated process reads as NULL) |
| Launcher blank page | `launcher\webstep.log` | is the WebView2 runtime complete (`launcher\webview2\`) |
| GAL page has no background / no portrait | open the browser console and look at `cards.local` / `app.local` | the virtual-host mapping is set up by the launcher; missing assets fall back to a CSS gradient |
| A tool pack does nothing | `.env` `PACKS_ENABLE_PY` | **off by default** (no code inside a pack is imported); running tool packs requires turning it on explicitly |

### 7.2 The three gates (all must be green after any change)

```
cd E:\robot\qq-bot
.venv\Scripts\python.exe tests\smoke_test.py                    # expect ≥949 PASS / ≤4 FAIL
.venv\Scripts\python.exe -m compileall -q core plugins agent bot.py tests harness tools   # expect exit code 0
.venv\Scripts\python.exe -m pyflakes core plugins agent bot.py  # expect ≤12 lines
```

> **`tools` was added to the compileall scope on 2026-09-12**: audit tools, migration tools and dev tools all live in
> `qq-bot/tools/`, and it **was not inside any gate** before — "the tools that check everyone else had nobody checking them"
> (in practice `compileall -q tools` measured 0 that day, so adding it cost nothing).
> The `pyflakes` scope deliberately excludes `tools`: that is the scope the 12-line baseline is defined against — moving it
> would make the gate numbers incomparable (the existing noise was not produced by this change).

**Current baseline (re-checked 2026-09-12)**: 949 PASS / 4 FAIL; compileall 0; pyflakes 12.

**The 4 FAILs are known pre-existing mismatches** (voice-parameter assertions, the output of a parallel session — do not touch):
`语音zh-中性参数` / `语音zh-情绪参考映射` / `语音zh-娇嗔参考` / `语音zh-未知情绪回退默认`
(*voice zh-neutral params / voice zh-emotion reference mapping / voice zh-coquettish reference / voice zh-unknown-emotion
falls back to default*).
**A new FAIL = revert that change**.

### 7.3 The consistency auditor (new)

```
cd E:\robot\qq-bot
.venv\Scripts\python.exe tools\dev\audit_consistency.py          # human-readable
.venv\Scripts\python.exe tools\dev\audit_consistency.py --json    # machine-readable
```

It targets exactly the class of real historical bugs in this project — **one fact with multiple sources**:

| Item | What it checks |
|---|---|
| A | env drift: the keys the code reads ⟷ `.env.example` ⟷ `.env` (covering nonebot lowercase attributes, helper-function quoted keys and dynamically templated keys — three consumption styles) |
| B | hard-coded absolute paths (the kind that break on relocation) |
| C | writes that bypass `core.atomics` — **reported in two columns: "dangerous (raw state-file writes)" and "benign (append-only logs)"** |
| D | exact AST checks: bare except / mutable default arguments / blocking calls inside async / `except: pass` with no comment (**auto-graded**, see below) |
| E | multi-source constants: where ports, model filenames, variant-directory segments and the like appear — **graded** (definition / comment / docstring / declared / parallel-session artifact / scattered); only "scattered literals on the Python side" and "cross-file duplicate definitions" are judged ❌, while the PS side is cross-language and is only listed, not counted |
| F | BOM drift: compares the first three bytes of every file against `git HEAD` (a PS script that loses its BOM = PS5.1 crashes outright; actually hit on 2026-09-12) |
| G | dependency-matrix consistency: `launcher/deps.json` ⟷ the generated block in `docs/部署指南.md` (*Chinese*) |
| H | genuinely silent write failures: `try: <write> except: pass` with not even a comment (should be 0 outside the whitelist) |
| I | **data-root derivation**: which root `Path(__file__).resolve().parents[N] / "data"` resolves to (canonical root = state / content root = in-repo content), plus filesystem evidence to catch real splits where "a file of the same name exists in both places" |
| J | **launcher reference integrity** (three slices): ① control names — the names referenced by `FindName("X")`/`BindClick "X"`/`Set-Lamp` ⟷ the `x:Name` declarations in the XAML (a typo = `$null` + guard = **silently does nothing**); it also checks "zombie names" and `BindClick` calls that name a control but attach no handler (the button silently does nothing — really hit), plus `-Mode` usage-string consistency ② **page-name closure** — a static argument to `Show-Page "x"` must match a page container in `$script:PAGES` (**a typo makes every page Collapsed at once = the whole window goes blank with zero errors**, worse than a wrong control name), `Set-NavActive`'s `$map` must cover every reachable page (a missing key = no nav highlight), and the values of `$map` must be declared controls (it fetches them via `FindName($map[$k])`, which falls in slice one's "dynamic reference" bucket and is by design not an error, hence pinned separately) ③ **skin web-page closure** — the framework's built-in generic template is the `web/index.html` + `web/app.js` pair (no XAML, no FindName, so the first two slices do not cover it): the ids app.js uses as literals (`$()`/`setText()`/`setLamp()`/`bind()`/`querySelector('#x')`) must exist in the HTML (otherwise state updates go into thin air), and every `id="btn-*"` button in the HTML must be referenced by app.js at least once (otherwise clicking does nothing; the reverse case is a deleted button that JS still binds → a top-level throw → **every page binding and the 1 s poll die together**). Non-button ids are never judged (layout anchors are legitimately unreferenced; deliberate noise control) |
| K | **document contract** (six faces, both directions reported): ① **route level** — the HTTP/WS routes declared in `接口文档.md` (*Chinese*) and the top-level keys of `/gal/content.json` must really exist (anything that 404s when you follow the doc is ❌) ② **path level** — in-repo paths in the docs (`qq-bot/ docs/ launcher/ build/` prefixes, 30 of them across the three authoritative documents) must really exist ③ **field level** — the §4.4 command channel ⟷ `Handle-WebCmd` dispatch, the §5.3 asset slot `ASSETS` ⟷ `gal.js` keys, the skin `manifest.json` fields from §4.1 + `皮肤包接口规范-v1.md` (*Chinese*) §2 (**union semantics**: the index defines the two as "master table + details", so reading only one would mis-report `palette` as "missing from the docs") ④ **value level** — the `type` value set in §3.1 ⟷ `core/packs.py`'s `VALID_TYPES` ("unknown type skipped, never crashes" = **the whole pack is silently skipped**, so both directions must agree) ⑤ every face carries its own positive/negative self-test (**10 cases** in total) ⑥ an empty extraction is always treated as **failure**, never passed silently. The docs are the **contract** for third-party mod authors, so "the doc says it, the code does not have it" must be reported ❌ |
| L | **documentation-index consistency** (three slices): ① the files registered in `docs/README.md` ⟷ the files actually in `docs/` — dead links (a file the index lists does not exist), unregistered files (a file exists but the reader cannot tell whether it counts as authoritative), and whether the file count the index claims for itself is right ② the index **contradicting itself** — the same document may not be both in §2 "sole authority" and in §3.3 "historical archive" (the machine form of the index's own §4 discipline 5, "do not establish a second authority"), and the "superseded by" target in §3.3 must really exist (otherwise it is **a signpost pointing people at an empty lot**). Only the corresponding column is read, not the remark column: a §2 remark saying "this file has been demoted to history" is intentional — counting it as a conflict would be noise ③ **naming-convention self-consistency** — §3.1 "permanently maintained live documents" may not contain date-suffixed files (the index's own §3.3 footer says: with a date = a snapshot, never tracked and updated; without a date = permanent, continuously maintained). Only this one direction is checked: date-free names appearing in §3.3 are normal (those are "retired permanent documents"); checking the reverse would spew noise |
| M | **control-character scan**: TAB and C0 control characters in source (`.py/.ps1/.md/.json/.txt` forbid TAB; `.js/.css/.html` forbid only non-TAB). The **generated-artifact** half lives in the SDK self-check (`build/build_release.ps1` item 4) — two layers each watching one slice, because two real incidents happened, one in source (0x0B posing as the letter v) and one in an artifact (`` `t `` eaten by PS as an escape) |
| N | **timeouts and task references** (three parts): ① `subprocess.run/call/check_*` missing `timeout=` (a hang permanently hangs the caller; `Popen` has no such parameter, so it is not reported) ② bare `asyncio.create_task` (the return value is not kept → only a weak reference is held → **may be silently eaten by the GC**). Convention: background tasks always go through `core.bgtasks.spawn(...)`; if you must call it bare, write `# audit-ok: <reason>` ③ `open(...)` not inside a `with` (handle leak: on Windows this **locks the file**, and the worst symptom is "you cannot delete the log/model file"; `plugins/voice/**` is a parallel-session artifact and is exempt) |
| O | **local-identity leakage**: the owner's QQ number / nickname appearing in **in-repo text** or in an **exe binary** ⟶ measured 2026-09-12: the launcher package's exe contains the real number in **5 places** (ps2exe embeds the script, **comments included**, into the exe, so writing "example: SUPERUSERS=['14…09']" in source really does ship with the release), the core package's `harness/driver.py` falls back to the real number, and `brain`'s default nickname is the real nickname too. **Key design**: the values are **derived at runtime from `qq-bot/.env`** (the first number in `SUPERUSERS` + `OWNER_NICKNAME`), and the report only prints the **masked** form — so after you change the owner's number the check follows automatically, and the tool's own output cannot leak a second time. The nickname half is **listed, not judged**: a signature (`# © @someone`) and a "code default value" have the same shape, and whether it is intentional is a human decision (`遗留任务.md` (*Chinese*) A6/A7) |

**Both D and C come with a "grader + self-test"**: the tool separates the mechanically provable-harmless from the real signal,
and **proves on the spot that it recognizes the bad patterns** (the self-test samples include positive and negative cases).
Why it has to be this way: a checker that "always reports 0" is indistinguishable from no checker at all, while a checker that
mixes 12 harmless noise items into ❌ makes people **start ignoring its output** — both are fooling yourself with a tool.

Item D's three verdict buckets: `清理类` (*cleanup-kind*: closing handles / deleting temp files only) · `观测类`
(*observation-kind*: logging only) · `待查` (*to-investigate*: **must** either change the code to log, or comment on the
`except` or `pass` line why the failure is harmless). Hits under `plugins/voice/**` are listed separately as a
"parallel-session artifact" and do not count toward this session's `待查` (see `docs/遗留任务.md` (*Chinese*) C4). The criteria
for a finished round: **待查 0 and self-test PASS**.

Item E is graded the same way: `定义 / 注释 / 文档串 / 已声明 / 另会话产物 / 散落` (*definition / comment / docstring /
declared / parallel-session artifact / scattered*), and only **scattered literals on the Python side** and **cross-file
duplicate definitions** (the same constant defined once in each of two files = a breeding ground for "changed A, forgot B")
are judged ❌. The PS side (`start.ps1` and friends) cannot import Python constants, so it is only listed, not counted —
cross-language consistency is pinned by smoke §36 using **actually constructed parameters**.
If a constant genuinely **must** have a second copy in Python (for example a URL segment agreed with the front end's
`launcher/web/gal.js`, which the Python side cannot reach), write `# audit-ok: <reason>` on that line or the line above: it
moves from "scattered" to "declared" and **is still listed in the report** (not hidden, just no longer a ❌).

**Item N's boundary (deliberately not checked — gather evidence first, then decide)**: three kinds are **not** in N:
`sqlite3.connect()` missing `close()`, `wave.open()`, `Image.open(BytesIO)`. The reason is that handles in a one-shot process
are reclaimed by the OS on exit, and in this repo all three appear almost entirely in short-lived scripts under `tools/` and
`tests/` (20+ places); the only one in a long-running process is `agent/graph.py`, and it already writes
`try/finally: conn.close()` correctly (evidence re-checked 2026-09-12). Adding them to the checker would only produce a
screenful of noise, and "a checker that mixes 20 harmless noise items into ❌ makes people start ignoring its output" — that
kind of expansion, "higher coverage, worse signal-to-noise", is a net negative. So N covers only `open()` (a critical path in
long-running processes, and it really did hit the Windows file-lock problem); the boundary is written here instead of being
left for the reader to guess.

**Item K's matching boundary (also pulled back after gathering evidence)**: the **field-by-field** tables for `pack.json`
(the first column of each table in §3.1–3.8) are **not** machine cross-checked. Evidence: the first column of those tables
**mixes three kinds of things** — bare field names (`spec`), field paths (`card.name/cls/star`, `compat.core_api`,
`entries[].always`), and **in-pack filenames** (`card.json`, `sprites/manifest.json`); and this contract is **consumed by both
Python and PowerShell** (§3.1 states outright that `title`/`author`/`license` are for launcher display and are read by
`Launcher.ps1` rather than by `core/packs.py`). Forcing a comparison table out of it measurably produced **46 false ❌** (all
of them "the doc says it, the code does not have it", while in fact they are read by the PS side or are marked by the doc
itself as "record only / only meaningful to the tool"). **Pulling back was more correct than shipping it**: field level is
reserved for truly flat contracts (the §4.4 command channel, the §5.3 asset slot, the §4.1 skin manifest, and the `type` value
set in §3.1). If `pack.json` fields really must be checked in the future, first model it as "what py reads ∪ what ps reads",
then give fields the doc marks as record-only **a machine-parsable marker in the doc** — do not use a hard-coded whitelist.

---

## 8. Backup and rollback

| Scenario | How |
|---|---|
| Before changing code | `git archive HEAD \| tar -x -C vault/backups/pre_<reason>_<date>/` + record `git rev-parse HEAD` |
| Before changing data | copy `data/memory.db` and the relevant JSON to `vault/backups/` |
| Rolling back code | `git checkout -- <file>` (when uncommitted); or restore from the archive |
| Rolling back data | overwrite with the backup (**stop the bot first**, to avoid write races) |
| **Before changing the owner QQ** | the migration tool's `--apply` backs up **automatically** (see §4.2) |
| Wanting to undo after changing QQ | change `.env` back to the old number + overwrite `data\memory.db` with that backup |

**2026-09-12 cleanup archive**: `vault\backups\memory.db.bak_before_cleanup_20260912` (the full copy from before the cleanup),
`vault\backups\pre_plan_exec_20260912\` (code/data/card snapshot + `MANIFEST.txt` SHA256),
`vault\db-surgery\db_cleanup_20260912.py` (re-runnable dry-run).

> `vault/` is gitignored and **not part of the public surface** — safe to keep sensitive backups there.

---

## 9. Known remainders (kept on purpose — do not "fix" them as bugs)

| Item | Status | Reason |
|---|---|---|
| hard-coded `E:\robot\...` inside `plugins\voice\**` | not closed | that directory is a parallel-session artifact and is left alone this round; **after relocating, voice stops working** |
| `tools\start-llama-server.ps1` | an old script with 4 hard-coded places | recorded as unused; see §1.3 |
| the 13 old lexical directories at the root of `data\stickers\` (156 images) | unreachable | owner decision: keep them in place |
| `data\legacy\{brainwash,hypno,lust}_state.json` | referenced by no code | old special-play state; disposition awaits a human decision (see `docs\遗留任务.md` (*Chinese*)) |
| `tools\llama-new\` (1.5 GB, containing b10520 and turbo) | unused | an experimental engine copy; cleanup awaits a human decision |
| the 4 voice-assertion FAILs | known mismatches | see §7.2 |
| the 8 `parents[N]` derivations for the content root `qq-bot\data` (memes / scenes / colloquial samples / style samples / scale baseline) | **deliberately kept** | that is the **in-repo content** root, distinct from the state root; switching them to `data_path()` would read no file = **silent empty strings**. Audit item I lists them as "declared" (see the two data roots in §3.1) |
| `plugins\correction\corrections.json` written under the content root | awaiting a decision (already marked `# audit-ok:`) | it is accumulated **state**, but moving it means migrating the existing data along with it → `docs\遗留任务.md` (*Chinese*) A9 |
| ~~the 33 `parents[N]` derivations under the canonical root (values correct)~~ | **✅ closed 2026-09-12** | all 33 were changed to `core.paths.DATA_ROOT`; audit **item I** now judges "the canonical root using `parents[N]` again" as ❌ (anti-regression). Reason: N depends on how deeply the file itself is nested, and the archive really did produce a bug where `parents[3]` pointed at the wrong place → the scene library was **silently empty** |
| `data\webgal_token.txt` | the GAL WS auth token | a normal artifact, not a leak |
