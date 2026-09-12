# Contributing Guide

Thanks for contributing. All three kinds of contribution are welcome: **content packs / skin packs**, **code**, and **docs**.

## 1. Filing an issue

Bring these along — they save half the back-and-forth:

- **Version**: launcher version (About page / `QQAI-Launcher.exe` metadata) and `CORE_API_VERSION` (`docs/API.en.md`, or the About page)
- **Symptom and reproduction**: what you did, what you expected, what actually happened (a screenshot helps)
- **Logs**: the relevant excerpt from the launcher's log page (or `-Mode logs`); on the bot side, `data/bot.log`
- **Environment**: Windows version, VRAM/RAM, and whether you run a local engine or an external endpoint

> Suggested triage order: first run `launcher\Launcher.ps1 -Mode status` and `-Mode deps` (the components page /
> dependency detection tells you outright "what is missing, and what breaks without it"), then run
> `python qq-bot\tools\dev\audit_consistency.py` (the consistency audit, 60+ verdicts — it pinpoints most
> environment problems directly).

## 2. Filing a PR (code / docs)

1. **Open an issue first and align**: anything involving behavior changes, a new interface or a new field — say clearly what you mean to do before you start (so the work is not wasted).
2. **Run the gates before submitting** (this is the project's hard discipline; see [`docs/MAINTENANCE.en.md`](docs/MAINTENANCE.en.md) §7.2):
   ```
   cd qq-bot
   .venv\Scripts\python.exe tests\smoke_test.py                      # must not lower the pass count: ≥949 PASS / ≤4 FAIL
   .venv\Scripts\python.exe -m compileall -q core plugins agent bot.py tests harness tools   # exit code 0
   .venv\Scripts\python.exe -m pyflakes core plugins agent bot.py    # ≤12 findings
   .venv\Scripts\python.exe tools\dev\audit_consistency.py            # 0 ❌
   ```
3. **Changing an interface means changing the docs in the same batch**: `docs/API.en.md` is the **contract** for third-party authors — every route / field / key the docs promise must really exist in the code (audit item K marks any inconsistency ❌).
4. **New checks come with self-tests**: adding a verdict to the audit tool requires **positive and negative** self-test samples as well (a checker that always reports 0, or one that always cries wolf, is worse than none).
5. **Commit in small steps**: one change, one thing; the commit message states what changed, why, and how it was verified.

## 3. Contributing content packs / skin packs

- Content packs: see [`docs/MODDING.en.md`](docs/MODDING.en.md) (the seven kinds: character / voice / item / emotion / world / stage / tool)
- Skin packs: see [`docs/SKIN-SPEC-v1.en.md`](docs/SKIN-SPEC-v1.en.md)
- Every pack **declares its own license** (the `license` field of `pack.json` / `manifest.json`); copyright stays with the author, and using this framework's formats does not transfer it.

## 4. Do not commit these things

| Do not commit | Why |
|---|---|
| Model weights, voice models, third-party UI / game assets | Neither the size nor the license belongs in this repo (see [`docs/THIRD_PARTY.md`](docs/THIRD_PARTY.md) for the third-party list) |
| Real QQ numbers / nicknames / local machine paths / any personal information | Audit item **O** scans committed text and the exe binary; a hit means ❌ |
| Adult content (prompts / scripts / assets) | This repo provides **capability and interfaces** only; ship adult content as a separate content pack outside the repo (see `docs/API.en.md`) |
| Local state (`data/`), backups (`vault/`), build output (`release/`) | These are runtime data; `.gitignore` already excludes them |

## 5. License

- Code contributions are licensed under **AGPL-3.0** by default (see [`LICENSE`](LICENSE)).
- Content pack / skin pack contributions use the **author's own license** (just declare it inside the pack), but make sure your assets come from legal sources.
