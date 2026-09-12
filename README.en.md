# QQAI Companion Framework

A **local-first** AI companion framework with a Windows launcher: QQ integration, a GAL web client,
life-simulation heartbeat, long-term memory, and a two-layer extension system (content packs + skin packs).

> **Don't want to clone?** Grab the **lazy bundle** from [**Releases**](../../releases)
> (`qqai-lazybundle-framework-*.tar`, ~7.5 MB: unzip → run `安装.ps1` → edit `.env`), or just the launcher exe.
> You only need **Python 3.13** plus **a model or an API key**.
>
> This repository contains the **framework only** — no models, no voice models, no third-party assets,
> and no character content. You bring those yourself, or install them as content packs.
> Adult-oriented content is **not shipped here**: the framework provides the capability and interfaces only.

## Highlights

- **Local-first inference** — talks to a local `llama.cpp` endpoint, or to any OpenAI-compatible API (your choice; no data leaves your machine in local mode)
- **Two front-ends, one brain** — QQ (NoneBot2 + OneBot v11) and a GAL web client (HTTP + WebSocket; stage art, sprite diffs and TTS driven by the same signal)
- **"Aliveness" pipeline** — life-simulation heartbeat, proactive outreach, calendar-style appointments, mood/relationship state, daily review and fact extraction
- **Content packs `robot-pack-v1`** — seven pack types (card / voice / item / emotion / world / tool / gal), dual-root discovery, broken-pack isolation
- **Skin packs `launcher-skin-v1`** — replace the entire launcher UI (manifest + pages + palette); GAL asset slots are overridable too
- **Launcher** — one-click start/stop, dependency detection, pack management, log and status views; plus headless `-Mode status|deps|packs|logs` (**UI logic is script-verifiable**)
- **Auditable** — ships with a consistency auditor (60+ checks: env drift, absolute paths, atomic writes, doc contracts, reference integrity, privacy leaks), each with positive/negative self-tests

## Quick start

```
1. Install Python 3.13 + dependencies (qq-bot/pyproject.toml), and an inference engine (llama.cpp) — or point it at an external API
2. Copy qq-bot/.env.example → .env, set at least SUPERUSERS (your QQ number)
3. Double-click launcher\QQAI-Launcher.exe — it tells you what's missing, what breaks without it, and where to get it
```

Details: [`docs/DEPLOYMENT.en.md`](docs/DEPLOYMENT.en.md) (install / upgrade / relocate / uninstall — English).
The fuller operations and API references are currently in Chinese (`docs/维护手册.md`, `docs/接口文档.md`); English
versions are being produced in stages.

## Repository layout

```
qq-bot\       Bot core (bot.py + core\ + plugins\ + agent\ + harness\ + packs\ + tests\ + tools\)
launcher\     Launcher (Launcher.ps1 + build_exe.ps1 + web\ with the default skin and GAL client + deps.json)
build\        Release packaging (build_release.ps1: SDK / Core / launcher bundles + self-checks)
docs\         Documentation (contracts / how-to / operations / deployment / third-party notices)
```

> Runtime directories (`data\`, `tools\`, `launcher\cards\`, `vault\`) are **not** in the repo:
> they hold your local models, voices, assets and state.

## Extending

| Goal | Read |
|---|---|
| Build a content pack (character / voice / stage / world …) | [`docs/MODDING.en.md`](docs/MODDING.en.md) |
| Stable contracts (pack format, GAL protocol, skin format, code hooks) | [`docs/接口文档.md`](docs/接口文档.md) |
| Skin pack format and launcher interfaces (`state.json` / `postMessage` / manifest) | [`docs/皮肤包接口规范-v1.md`](docs/皮肤包接口规范-v1.md) |
| Architecture and module map | [`docs/项目拆解.md`](docs/项目拆解.md) |
| Third-party components and licenses | [`docs/THIRD_PARTY.md`](docs/THIRD_PARTY.md) |

**Compatibility promise**: `CORE_API_VERSION` (currently `1.0.0`) is **append-only within v1.x** —
documented fields and hook points will not break.

## License

- **This repository's code**: [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0).
  Use, modify and distribute freely — but **if you run a modified version as a network service, you must
  offer the source under AGPL as well**.
- **Copyright**: Copyright (C) 2026 **@晓咕咕Max**.
- **Content packs / skin packs**: copyright belongs to **their authors** and is not transferred by using
  this framework's formats; each pack declares its own license in `pack.json` / `manifest.json`
  (e.g. the bundled example pack `example.sakura` is CC0-1.0).
- **Third-party**: models, voice models, UI/game assets and third-party runtimes are **not distributed here** —
  see [`docs/THIRD_PARTY.md`](docs/THIRD_PARTY.md); you must obtain them legally yourself.
- **Disclaimer**: this project is not affiliated with any third-party messaging platform. You are responsible
  for complying with the platform's terms of service and local laws.
- **Platform note**: the launcher is Windows-only today (PowerShell + WebView2). Linux/Docker is not supported yet.

## Contributing

Issues and PRs are welcome — content packs, skin packs, docs and examples all count. See [`CONTRIBUTING.md`](CONTRIBUTING.md).
