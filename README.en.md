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
- **Skin packs `launcher-skin-v1`** — replace the entire launcher UI (manifest + pages + palette); GAL asset slots are overridable too, and a skin can **own whole views**: set `operaPage` and the top bar's *Operators* entry opens *your* page instead of the built-in one
- **Launcher** — a persistent top bar (Home / Operators / Settings / Content packs / Runtime status / Log / Components / About + ▶ Start / ■ Stop / lamps) over a body that is either your skin page or a framework page; dependency detection, pack management, and headless `-Mode status|deps|packs|logs` (**UI logic is script-verifiable**)
- **Auditable** — ships with a consistency auditor (67 verdict lines: env drift, absolute paths, atomic writes, doc contracts, reference integrity, UI element-id closure, privacy leaks), each with positive/negative self-tests

## Quick start

**Pick an install path first** — both work; the only difference is how the launcher starts:

| Path | What you get | How to launch |
|---|---|---|
| **Lazy bundle** (`qqai-lazybundle-*.tar` from [Releases](../../releases)) | framework + a **pre-built `QQAI-Launcher.exe`** + `安装.ps1` | unzip → run `安装.ps1` → double-click `launcher\Run.bat` |
| **Clone this repo** (`git clone`) | source only — **no exe** (it is a build product and is not committed) | double-click `launcher\Run.bat` (**it falls back to `Launcher.ps1` automatically**), or build one with `launcher\build_exe.ps1` |

```
1. Install Python 3.13 + dependencies (qq-bot/pyproject.toml) and a local inference engine (llama.cpp);
   or skip the engine entirely and point the launcher at any OpenAI-compatible API (the model is optional)
2. Copy qq-bot/.env.example → .env and set at least SUPERUSERS (your QQ number).
   A persona works out of the box: the default is PERSONA=_starter (a neutral placeholder card shipped with the repo)
3. Start the launcher → its "Components" page lists what is missing, what breaks without it, and where to get it
4. Click ▶ Start in the top bar → the engine is ready in ~30-60 s → message your bot
```

> **Three things clone users should know**: ① `QQAI-Launcher.exe` is not in the repo (see the table above);
> ② the **wrapper DLLs** for WebView2 (`launcher\*.dll`, four of them) *are* in the repo, but the **browser runtime
> itself** (Evergreen, ~100 MB) is not — use the system one (usually already present on Win10/11; if it is missing,
> install the Evergreen Runtime from Microsoft; rendering failures are logged to `launcher\webview2_error.log`);
> ③ models, voice weights and NapCat are never shipped (licensing and size) — the "Components" page tells you where to get each one.

Details: [`docs/DEPLOYMENT.en.md`](docs/DEPLOYMENT.en.md) (install / upgrade / relocate / uninstall + the feature→dependency matrix) and [`docs/MAINTENANCE.en.md`](docs/MAINTENANCE.en.md) (start/stop / logs / state / troubleshooting).
The English set is now complete: [`docs/API.en.md`](docs/API.en.md) (contracts) ·
[`docs/SKIN-SPEC-v1.en.md`](docs/SKIN-SPEC-v1.en.md) (skin packs, layering) ·
[`docs/MODDING.en.md`](docs/MODDING.en.md) (build a mod) ·
[`docs/ARCHITECTURE.en.md`](docs/ARCHITECTURE.en.md) (module map) ·
[`CONTRIBUTING.en.md`](CONTRIBUTING.en.md).
The Chinese originals are authoritative if the two ever disagree — the translations are maintained in the same change, not afterwards.

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
| Stable contracts (pack format, GAL protocol, skin format, code hooks) | [`docs/API.en.md`](docs/API.en.md) |
| Skin pack format, `state.json` / `postMessage` / manifest, who owns which view | [`docs/SKIN-SPEC-v1.en.md`](docs/SKIN-SPEC-v1.en.md) |
| Run it, where the switches and logs are, what to check first when it breaks | [`docs/MAINTENANCE.en.md`](docs/MAINTENANCE.en.md) |
| Architecture and module map | [`docs/ARCHITECTURE.en.md`](docs/ARCHITECTURE.en.md) |
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

Issues and PRs are welcome — content packs, skin packs, docs and examples all count. See [`CONTRIBUTING.en.md`](CONTRIBUTING.en.md) (or [`CONTRIBUTING.md`](CONTRIBUTING.md), *Chinese*).
