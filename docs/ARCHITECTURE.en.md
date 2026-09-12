# Project Breakdown (2026-09-11 · post-generalization architecture)

> Replaces the archived `docs/archive/项目拆解.md` (*Chinese*) — the baseline is the state after the
> "closed core + open SDK" generalization refactor.
> Companion reading: `docs/PLUGIN_SDK.md` (*Chinese*) (content packs robot-pack-v1 + ToolCard),
> `docs/SKIN-SPEC-v1.en.md` (launcher-skin-v1), `docs/GETTING_STARTED.md` (*Chinese*) (assembly walkthrough),
> `docs/架构图-Agent-2026-09-11.md` / `docs/架构图-活人感-2026-09-11.md` (*Chinese*; two mermaid diagrams),
> `docs/公开发布检查单.md` (*Chinese*) (the public-surface boundary).

## 0. Overview

In one line: **NapCat QQ → NoneBot2 → the sequential main chain in `plugins/brain` → `core`
(llm/packs/special/reply) → three outputs, QQ / GAL web / Telegram**; the background agent layer (lifesim
heartbeat / proactive / review) keeps feeding the main chain; the data surface converges on `data/` (runtime) and
`qq-bot/data/` (character cards and config). Releases ship as the "closed core + open SDK" three bundles
(`build/build_release.ps1`); private content goes out separately as local distribution artifacts
(`release/local-content/`).

## 1. launcher (the launcher)

- **Responsibility**: WebView2 shell + PowerShell host; carries the generic neutral home page, the GAL client
  and the three management pages (settings / log / status); the skin pack mechanism (`launcher-skin-v1`);
  bot start/stop and the message channel; the HTTP channel for switching persona live.
- **Key files**:
  - `launcher/Launcher.ps1`: the main host (skin manifest loading, the `skin` key of `data/launcher.json`,
    `state.json` payload assembly, the `postMessage` command channel, virtual-host mapping, the status page's
    `Get-LifeLog` timeline of the last 100 entries (scrolled through with `ScrollViewer`), card-table fallback).
  - `launcher/web/index.html + home.css + app.js`: the generic home page (current activity / mood / card grid /
    detail overlay / management row); `gal.html + gal.js + gal.css`: the GAL client (single WS connection,
    sprite diff frames, line-by-line voice via `POST /gal/tts`, recall-round rendering, the
    `window.GAL_RENDERER` hook).
  - `launcher/web/skins/generic/`: the tracked default skin (zero image assets); `skins/arknights/`: a
    gitignored local skin pack (the Arknights sample; `tools/export_content_packs.py` can turn it into a local
    distribution artifact).
  - `launcher/QQAI-Launcher.exe`: the ps2exe artifact (kept in sync with `Launcher.ps1` in both places).
- **External interface pointers**: `docs/SKIN-SPEC-v1.en.md` §2 manifest / §3 the `state.json` data contract /
  §4 the PowerShell ↔ web command channel / §5 virtual host / §6 the `data/cards.json` card data source /
  §7 GAL skin hooks / §8 the Arknights sample.

## 2. qq-bot core (the closed core, the convergence layer after generalization)

| Module | Responsibility | Key files | Interface pointers |
|---|---|---|---|
| llm adapter layer | All LLM client construction converges here: `get_client/resolve_model/thinking_extra/ctx_budget/embed_config`; local `llama-server` by default, one env switch to an external OpenAI-compatible endpoint | `qq-bot/core/llm.py` | `PLUGIN_SDK` §1 (*Chinese*; carries `CORE_API_VERSION`); decision log Phase 1 |
| packs | Content pack loader: robot-pack-v1 dual-root scan (`data/packs/` shadows `qq-bot/packs/`), broken packs skipped, five indexes (content/voice/world/items/tones), tool packs off by default | `qq-bot/core/packs.py` | `PLUGIN_SDK` §2-§3 (*Chinese*) |
| special | The special-marker protocol: state registration/clearing (decided by the agent), item catalog, perception text, `obey` slices | `qq-bot/core/special.py` (ledger: `data/special_state.json`) | `PLUGIN_SDK` §3.4, §5 special-marker protocol (*Chinese*) |
| reply | stage2 polish generation and its hard constraints (length / sentence breaks / near-duplication / drift detection / owner-facts injection / marker stripping) | `qq-bot/core/reply.py` | decision log Phase 1 wiring record |
| atomics | no-BOM atomic writes (text/json) + isolation of corrupt files (kept as `.corrupt` for forensics) | `qq-bot/core/atomics.py` | the single sanctioned path for data writes repo-wide |
| paths | Single data-root derivation (`DATA_ROOT=<install-root>/data`, no dependence on a drive letter) | `qq-bot/core/paths.py` | `PLUGIN_SDK` §2 path convention (*Chinese*) |
| mood | Global mood: the agent self-labels it, the heartbeat drifts it toward calm; stored in `life_state.json` | `qq-bot/core/mood.py` | `架构图-活人感` (*Chinese*) |
| perception | Perception assembly building blocks: time / long absence / intimacy / language / structure / memes / being named | `qq-bot/core/perception.py` | — |

## 3. plugins (the bot plugin surface)

| Plugin | Responsibility (key points) |
|---|---|
| `plugins/brain` | The sequential main chain: the `handle` entry (perception routing for image-only / voice-and-text / text-only, group-chat gating, burst aggregation, command routing) → perception assembly (`memory.build_context` semantic recall, replay of suspended topics, open threads / weekly review) → **stage1 content** (thinking is self-decided; the "serious" marker (【认真】) opens chain-of-thought) → parallel tasks: appointment self-decision + **owner-facts pre-parsing** (triggered by full-width parentheses in the owner's private chat) → **stage2 polish** (`core/reply.generate`: tone target / self-decided length / appellation substitution / drift fallback) → hard-boundary fallback → send + consume (TTS / stickers / appointment ledger / tone → voice and sprite diffs from the same source). `PERSONA_ALIAS` alias table; `_SCENES_FILE=qq-bot/data/scenes.json` |
| `plugins/memory` | The memory store `data/memory.db` (messages/facts/events/emotions/summaries/open_threads/fact_corrections) + semantic recall (`memory/embeddings.py` vector service on :11435, qwen3-embedding; switchable off with `EMBED_ENABLED`); `build_context` assembles the L1-L4 context; the group meme store |
| `plugins/voice` | TTS: subprocess pool over the GPT-SoVITS bundle (`tools/`, gitignored), the built-in `VOICES` table merged with a pack's `voice.json` via `setdefault`, nine emotion classes in `EMO_PARAMS` (tone parameters), a serial lock on `tts_wav`; nothing fires without a voice key |
| `plugins/webgal` | The GAL web output: single WS connection (token auth, `data/webgal_token.txt`), frame protocol (auth/msg/mode/ping ↔ auth_ok/reply_start/seg/sprite/state/reply_end/err), `/gal/content.json` (`core.packs.content_index`), `/gal/tts`, the `CaptureBot` for synthetic rounds, recall rounds written to `data/gal_history.jsonl` (Phase 2b), the gal gate disabling the QQ channel |
| `plugins/telegram` | The international IM channel (added 2026-09-12): Bot API long polling (`getUpdates`, token from `.env`), owner-only private chat gate, drain-on-startup (the offline backlog is counted, never replayed), each update turned into a synthetic OneBot event and pushed through `nonebot.message.handle_event` into the *same* pipeline — so Telegram shares the owner's identity and memory with QQ and the GAL page; `TgBot.call_api` translates outgoing segments into Bot API calls (text/photo) and no-ops everything else. Boundaries: groups/strangers ignored, voice inbound-only; see `DEPLOYMENT.en.md` §2.6 |
| `plugins/fiction` | Long-form writing (write a novel / continue / revise chapter N / outline); meta + outline + per-chapter persistence under `data/fiction/` |
| `plugins/emotion` | Emotion engine: 9-class LLM classification (rule-based fallback on failure) → VTube Studio Live2D parameter injection (silent fallback while it is not running) → persisted to `emotions` |
| `plugins/correction` | Conversation correction: the LLM decides whether a correction was intended → `data/corrections.json` (≤10 entries per user, owner only) → `build_context` injects "the way of speaking this person has corrected" |
| `plugins/sticker` | Sticker integration: the local variant directory wins; the LLM assesses the reply's emotion and sends immediately (no API, no usage cap) |
| `plugins/persona` | Character card (`chara_card_v2`) parsing and system-prompt assembly: the `/人设` list (local cards + pack cards merged), switching, the colloquial constraints in `colloquial_style.txt` / `common_phrases.txt`, `persona.list_persona_entries` source tagging |
| `plugins/debug` | Ops and background-loop host: log/status commands, the engine watchdog, and `start_background_loops_async`, which registers the **proactive loop** (45-min cadence: candidate-owner whitelist, life sharing / dropped-conversation facts / appointments coming due — after perceiving, the agent itself decides whether to speak, and the ledger entry is written only after sending, to prevent repeats) and the **review loop** (hourly: catch-up daily review, weekly inventory, checkpoint prune, `_sweep_stale`) |
| `plugins/livesource` | Live-stream danmaku source: `register_handler(fn)` registers a ToolCard through `agent/registry` (the B2 wiring); once normalized, danmaku go to the registered hook, which decides whether to consume them |
| `plugins/qq_avatar` | NapCat extension for avatar swapping (kind / fallen avatar slots, automatically linked to brain mode switching) |
| `plugins/search` | Bing web search (web pages + direct image links, no key, returns empty on failure) |

## 4. The agent layer (`qq-bot/agent/`)

- **registry.py**: the `ToolCard` capability bus (name/description/fn/failure/needs_llm), self-registration via
  the `@register` decorator plus imperative registration (duplicate names rejected), the entry point to the
  controlled execution environment of iron rule #1; the anchor that plugins such as livesource hang off.
  Interface pointer: `PLUGIN_SDK` §5 (*Chinese*; the ToolCard row).
- **graph.py**: the agent main loop (`_perceive → _decide → _act → _reflect`) + langgraph assembly +
  `agent_checkpoints.db` persistence and `prune_checkpoints` (keep 30 days); reused by non-message chains such
  as proactive and review.
- **lifesim.py**: heartbeat (self-decided cadence `wake_min` ±10% jitter, restart recovery tick) — "doing"
  generation at `max_tokens=120` → `data/life_state.json` (doing/scene/mood/appointment traces) +
  `data/life_log.jsonl` (capped at 400 entries, about 8 days of the status page's scrollable timeline); night
  sleep protection, injection of "what happened during this time" (「这段时间的事」) after a gap >2h, whole-group
  archiving of `data/life_states/` on card switch, the diary `life_diary.jsonl`, and conversation micro-updates
  through `micro_update_from_conversation` / `apply_interaction`.
- **llm.py / tools.py / state.py / guardrails.py**: the agent-side client delegates to `core.llm` (purpose
  `"agent"`), plus the tool set, `AgentState`, and guardrails.

## 5. The data surface (all gitignored)

### 5.1 Runtime data root `<install-root>/data/` (`core/paths.py` `data_path()`)

| File | Contents |
|---|---|
| `memory.db` | Memory store (messages/facts/events/emotions/summaries/threads/corrections) |
| `agent_checkpoints.db` | Agent checkpoints (persisted by graph, pruned daily) |
| `life_state.json` / `life_log.jsonl` / `life_states/` / `life_diary.jsonl` | Heartbeat current state / timeline (≤400 entries) / card-switch archive / daily diary |
| `gal_history.jsonl` | GAL recall rounds (webgal Phase 2b) |
| `quotes.json` / `cards.json` | Per-card quote layer (whole-key override of pack quotes) / launcher card-grid data (`export_cards.py` only appends) |
| `special_state.json` | Ledger of special markers / items / emotion layers (managed by the framework itself) |
| `persona_select.json` / `persona_mode.json` / `persona_switch_ts.json` / `launcher.json` | Startup persona choice / persona mode / switch throttling / launcher config (the `skin` key) |
| `proactive_state.json` / `pending_tasks*.json` | Proactive-loop ledger (anti-repeat) / appointment tasks |
| `webgal_token.txt` / `webgal_mode.json` | GAL WS auth / the qq↔gal mode switch |
| `voice_state.json` / `voice_mode.json` / `voice_data.json` / `voice_refs/` | TTS state / switch / voice data / reference audio |
| `corrections.json` / `memes.json` / `universe_roles.json` / `group_members.json` | Corrections / group meme store / same-universe character recognition / group member snapshot |
| `bot.log` and friends | Engine and bot logs (managed by the debug watchdog) |

### 5.2 Bot-directory data `qq-bot/data/`

| File / directory | Contents |
|---|---|
| `personas/*.json` | Local character cards (`chara_card_v2`, no BOM; `_template.json` template; local cards always win over pack cards; gitignored private content, `export_content_packs.py` can produce a local distribution artifact) |
| `scenes.json` | Scene vocabulary (brain; aligned on the `universe` key) |
| `scale_baseline.txt` | Scale fallback baseline (the generic fallback when a card leaves `scale_rules` empty) |
| `colloquial_style.txt` / `common_phrases.txt` | persona colloquial-style constraints |
| `stickers/` / `avatars/` | Local sticker variants / avatars |
| `*.jsonl` (train/rp/compare and so on) | Training corpora and comparison sets (offline, consumed by `tools/` scripts) |

## 6. build / release

- `build/build_release.ps1`: three bundles — **SDK** (specs / web surface / example.sakura / _template /
  export_cards.py; self-check: 0 images / 0 data state files / a 6-word IP needle, any violation refuses to ship
  the bundle), **Core** (`bot.py` + core/agent/plugins/harness; pyarmor preferred, falling back on failure to a
  source bundle plus `ENCRYPTED_SKIPPED.txt`), **Launcher** (exe + README); `manifest.json` (SHA256). Optional
  `-LocalContent`: after building, calls `qq-bot/tools/export_content_packs.py` to produce three private content
  distribution artifacts under `release/local-content/` (gitignored; a failure does not block the main build).
- Interface pointers: `docs/公开发布检查单.md` (*Chinese*) (must pass before release); `PLUGIN_SDK` §8
  (*Chinese*; local content distribution boundary).

## 7. vault boundary (the gitignored private area)

`vault/` is never committed: `backups/` (snapshots), `sensitive-docs/` (R18 briefs and the like),
`unrelated-assets/` (unrelated material), `content-packs/README.md` (the registry of where private content
distribution artifacts live). The public surface (repo / SDK / release bundles) never references anything
inside `vault/`; items IP-9/IP-10 of the public-release checklist are its boundary entries.

## 8. Documentation map (standing documents)

`README.en.md` (navigation) → `docs/HANDOFF.md` (*Chinese*; handoff) → `docs/GETTING_STARTED.md`
(*Chinese*; getting started) → `docs/PLUGIN_SDK.md` (*Chinese*; content packs / SDK) →
`docs/SKIN-SPEC-v1.en.md` (skins) → `docs/公开发布检查单.md` (*Chinese*; release boundary) →
`docs/开发者指南.md` (*Chinese*) / `docs/ROADMAP.md` (*Chinese*); dated historical documents live in
`docs/archive/`.
