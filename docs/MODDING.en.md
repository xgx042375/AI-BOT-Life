# Modding Guide (from zero to working · for content authors)

> **How to use this**: pick the section for what you want to build, follow the steps, then read §8 for activation and troubleshooting.
> **Contract details** (field validation, protocols, permissions) always live in [`API.en.md`](API.en.md) — this guide only covers *how to do it*.
> **Cost of the three extension kinds**: content pack (edit JSON) < skin pack (edit pages) < tool pack (write Python, not loaded by default).
> **Prefer zero code**: 90% of mod ideas are satisfied by a **content pack** — first make sure you really need code.

---

## 0. Five minutes: build a character mod

```bash
# (1) Copy the live example (it is also a test fixture — break it and smoke_test will tell you)
cp -r qq-bot/packs/example.sakura  data/packs/yourname.sakura
```

(2) Edit `data/packs/yourname.sakura/pack.json`:

```json
{ "spec": "robot-pack-v1", "name": "yourname.sakura", "version": "1.0.0", "type": "card",
  "title": "Your character name", "author": "yourname", "license": "CC0-1.0",
  "compat": { "core_api": ">=1.0" },
  "card": { "key": "sakura", "cls": "mascot", "star": 4, "uni": "YOUR-WORLD",
            "desc": "One-line description (<=40 chars, truncated if longer)" } }
```

(3) Write `card.json` (copy the shape of `qq-bot/data/personas/_template.json`, format `chara_card_v2`):

```json
{ "spec": "chara_card_v2", "data": {
  "name": "Sakura", "description": "character setting…", "personality": "…",
  "universe": "your-world",          // recommended: links scene vocabulary / world info / same-universe recognition
  "sprite_expressions": ["proud", "blush"],
  "response_rules": ["…"] } }
```

(4) Add art (**must go into the real portrait slots; `sprites/` inside a pack is *not* auto-applied in v1**):

```
launcher/cards/fullw/<card-key>.webp           full body (detail page / home)
launcher/cards/bust4w/<card-key>.webp          bust (GAL page / card wall)
launcher/cards/bust4w/<card-key>_<word>.webp   bust variants (one per emotion you provide)
```

(5) Export card data and restart:

```bash
cd qq-bot && .venv/Scripts/python.exe tools/export_cards.py     # appends into data/cards.json
```

(6) Restart the bot → switch with `/人设 <card-key>` → the new card appears on the launcher's card wall.

> **Cards without art are filtered out of the launcher grid** (no error, just no card). To preview something,
> drop any image at `launcher/cards/bust4w/<card-key>.webp`.

---

## 1. Mod cheat sheet

| What you want | Pack type | Where | Code needed | Details |
|---|---|---|---|---|
| A new character | `card` | `data/packs/<author.name>/` | No | §0 |
| A new voice | `voice` | Same (or `voice.json` embedded in a card pack) | No | §3 |
| A new GAL stage (background + variants) | `gal` | Same | No | §4 |
| A new launcher look | Skin pack | `launcher/web/skins/<name>/` | **Yes (pages)** | §5 |
| New lines / quotes | card pack `quotes.json` | Same | No | §0 |
| A new item | `item` | Same | No | — |
| World info / lorebook | `world` | Same (or embedded in a card pack) | No | §6 |
| A new emotion tone | `emotion` | Same | No | — |
| A genuinely new capability (network, compute) | `tool` | Same | **Yes (Python)** | §7 |

**One pack can be several kinds at once**: a `type=card` pack may carry `quotes.json` / `voice.json` / `world.json` / `sprites/`.
`type` only decides the **primary identity** (who provides fallbacks, who indexes you).

---

## 2. Three hard rules for content packs (**violating them never errors, but never works either**)

1. **`name` must be dotted lowercase**: `author.name`, e.g. `yourname.sakura`. Uppercase, spaces or a missing dot → the whole pack is skipped (warning in `data/bot.log`).
2. **`version` must be three numeric parts**: `1.0.0`. Writing `1.0` → the whole pack is skipped.
3. **Card files must have no BOM**: when saving as UTF-8 in Notepad, do **not** pick "UTF-8 with BOM". A BOM breaks parsing.

After placing files: **restart the bot** (or call `core.packs.reload()`). `data/packs/` shadows same-named example packs in `qq-bot/packs/`.

---

## 3. A voice pack (`voice`)

```json
{ "key": "mysound", "name": "My voice", "lang": "zh", "personas": ["Sakura"],
  "gpt":   "C:\\GPT-SoVITS\\models\\my.ckpt",
  "sovits":"C:\\GPT-SoVITS\\models\\my.pth",
  "ref_dir": "C:\\GPT-SoVITS\\refs",
  "emo_refs": { "gentle": "reference entry name" } }
```

- `gpt` / `sovits` / `ref_dir` **must be absolute paths** (relative entries are skipped with a warning).
- If your `key` collides with a built-in voice, the **built-in wins** and your pack does nothing — pick another key.
- **Voice weights are never distributed with this framework** (they live in `tools/`, gitignored): point `voice.json` at your own weights.
- `emo_refs` keys use the nine-tone vocabulary used across the framework; a missing tone falls back to the "default" reference.

---

## 4. A GAL stage pack (`gal` · backgrounds + variants)

**This is the right place for "changing the stage"** — do not stuff backgrounds into a character card (a card is text;
a background belongs to no character), and not into a skin (a skin is the shell).

```
data/packs/yourname.stage-room/
  pack.json
  bg/room_night.webp                 <- background (the file's base name is its id)
  bg/rooftop.webp
  sprites/diff/sakura_Smile.webp     <- variants: <card-key>_<word>.webp  (same naming as existing assets — pure copy)
  sprites/manifest.json              <- optional: {"map": {"gentle": "Smile"}}
```

`pack.json`:

```json
{ "spec": "robot-pack-v1", "type": "gal", "name": "yourname.stage-room", "version": "1.0.0",
  "title": "A room with a window", "author": "yourname", "license": "private",
  "compat": { "core_api": ">=1.0" },
  "stage": { "default_bg": "room_night", "bg_alt": "rooftop",
             "bg_by_scene": { "rooftop": "rooftop", "classroom": "classroom" } } }
```

**Key points**:

- **Scene words are free-form text generated by the model** (the `scene` field from lifesim). Matching order is
  **exact → longest key contained → default**, so the key `rooftop` also catches "watching stars on the rooftop".
  **Shorter keys over-match** — use specific short words.
- A background id whose file **does not exist** is simply not indexed; the front-end keeps using its own default
  background (you never get a blank block).
- No extension needed (`.png/.jpg/.jpeg/.webp/.gif` are probed); if you add one, only the base name is used.
- Asset resolution priority: `bundled example pack < data/packs/<stage pack> < launcher/cards/` (your local private art wins).
- To fix a "tone word → asset word" mapping for one specific card: add `"card": {"key": "<card-key>"}` to the stage pack's
  `pack.json` and write `{"map": {...}}` in `sprites/manifest.json` — it **overrides** the card pack's map.
  (A stage pack without `card.key` has its map ignored, which avoids "whose map is this anyway" ambiguity.)

> **Already working** (2026-09-12): `type=gal` is implemented — backgrounds are served via
> `/gal/stage/<pack-name>/bg/...`, and variant vocabularies are scanned across `stage pack sprites/diff/` →
> `launcher/cards/bust4w` (**pack first**: the same word prefers the image inside the pack).
> **Live example**: `qq-bot/packs/example.stage/` (placeholder art generated by `qq-bot/tools/dev/gen_example_stage.py`,
> zero IP, obviously fake). ⚠️ It is **not in the SDK bundle** — the SDK distribution surface forbids any image
> (see the SDK's RELEASE-NOTES self-check), and a stage pack is inherently made of images. To copy a real shape, read it
> from the **Core bundle** or from the source repo path above.

---

## 5. A skin pack (`launcher-skin-v1` · the only mod kind that needs pages)

```
launcher/web/skins/<your-skin-name>/
  manifest.json        required
  index.html           entry page (manifest.entry points at it)
  ...                  your css/js/images
```

**Minimal working start**: copy `launcher/web/skins/generic/` (zero IP, zero images, a handful of files).

**Three musts** (otherwise your page will be half-dead):

1. Define these two globals — the launcher calls them directly to show its overlay/banner:
   ```js
   window.showWebBlock = function (text) { /* … */ };
   window.hideWebBlock = function () { /* … */ };
   ```
2. **Poll `state.json?t=<ms>` every second** (the `?t=` cache-buster is not optional) for run state; the launcher
   rewrites `state.json` wholesale about every 3 seconds.
3. Reference assets via `https://app.local/...` (= `launcher/web/`) or paths relative to your skin directory;
   **`http://127.0.0.1:8080` is only for the GAL page and the bot API**.

**Who owns what (the layering rule — read this before designing your pages)**

The launcher window is: a **persistent top bar** (navigation: Home / Operators / Settings / Content packs /
Runtime status / Log / Components / About — plus ▶ Start, ■ Stop, ✕ Exit, service lamps, clock) over a **body**
that is either your **skin page** (a WebView showing your entry file) or a **framework page** (native).

| Layer | Owns | Must not |
|---|---|---|
| Top bar + framework pages | navigation, global actions (start/stop/exit), and every framework-owned view (settings, packs, runtime status, log, components, about) | decide your look; **override a page you built** |
| Your skin page | ambience and read-only status (portrait / scene / mood / plan / dialogue / lamps), **your own views**, and buttons that call the framework via `postMessage({cmd})` | duplicate the global actions (start/stop) — two entry points for one action drift apart; and don't ship two UIs for the same data |

Two consequences you will actually hit:

- **Your skin may own a view.** If you build your own operator roster, set `"operaPage": true` in `manifest.json`:
  then the top bar's **Operators** entry (and `cmd:"operators"`) opens **your** view — the launcher pushes
  `page:"opera"` and you render it. Without that flag the framework's native operator page is used instead.
  Either way only **one** operator page is visible to the user.
- **Home always comes back to your entry file.** The top bar's **Home** re-navigates the WebView to your
  `manifest.entry` if it is showing something else (GAL page, or a failed load). Don't assume your page stays
  resident for the whole session — keep state in `state.json` polling rather than in-page globals.

**The GAL page needs a running bot.** `http://127.0.0.1:8080/gal` is served by the bot, so a GAL button on your
skin can only work while the bot is up. The launcher **blocks** that navigation when port 8080 is closed and shows
"机器人未启动 …" instead of letting the WebView land on an error page (a dead end: error pages don't poll
`state.json` and don't respond to Home). You can add the same pre-check from your page — the `svc.bot` field in
`state.json` tells you whether it is up:

```js
if (state.svc && state.svc.bot === false) { /* show "start the bot first" in your own UI */ return; }
```

**Optional but common**: `window.GAL_SKIN = { assets: {...} }` overrides GAL page assets (backgrounds, etc.).
Note that keys from `/gal/content.json` (`quotesByCard` / `quoteFallback` / `spriteMap`, plus `stage` for stage packs)
**arrive later and override** your values — including `quoteFallback`, which is read from its own variable rather
than from `ASSETS` (handled in `gal.js`; see the skin spec §7).

> Full field table (including `operaPage`), the whole command channel (`start` / `stop` / `stopbot` / `exit` /
> `opera` / `home` / `operators` / `cfg` / `plugins` / `log` / `state` / `deps` / `about` / `setpersona`),
> the state.json fields and the layering section: [`SKIN-SPEC-v1.en.md`](SKIN-SPEC-v1.en.md), plus [`API.en.md`](API.en.md) §4.

---

## 6. A world pack (`world`) — let characters "know" their world

```json
{ "spec": "robot-pack-v1", "type": "world", "name": "yourname.world", "version": "1.0.0",
  "world": { "universe": "your-world",
             "entries": [ { "text": "In this world…", "always": true, "priority": 0 } ] } }
```

Or embed it in a card pack (a `world.json` with the same fields, minus `spec`/`type`).

- `universe` must **match** the card's `universe` field, otherwise nothing is injected.
- **v1 only injects entries with `always: true`** (whole block capped at ~800 chars). `keys`-based triggering is reserved for v2 — filling it in today does nothing.
- Layers: a `world` pack = **setting text**; `data/universe_roles.json` = **character relations**; the scenes vocabulary = **scene words**. All three align on the `universe` key; they complement, not overlap.

---

## 7. A tool pack (`tool` · Python, **not loaded by default**)

Take this path only when you need **real code capability** (fetching data, heavy computation). Think first:
most needs are content-pack needs.

```json
{ "spec": "robot-pack-v1", "type": "tool", "name": "yourname.weather", "version": "1.0.0",
  "permissions": ["net:api.example.com"],
  "limits": { "timeout_s": 5, "result_kb": 64 },
  "tools": [ { "entry": "entry.py", "cards": ["weather.now"] } ] }
```

In `entry.py`, register against the `ToolCard` contract:

```python
from agent.registry import register, FAIL_RETURN_NONE

@register(name="weather.now", description="Current weather", failure=FAIL_RETURN_NONE)
async def now(city: str): ...
```

**Three hard constraints**:

1. **Not loaded by default**: the user must explicitly set `PACKS_ENABLE_PY`. **Do not make ordinary-user packs depend on it.**
2. **Permissions must be declared**: the default `permissions: []` means **zero capability** (no file reads, no network, no model calls).
   Enforcement lives in the kernel (AppContainer + ACL): undeclared files cannot be read or written; **a child process never
   gets network** — to go online you must declare `net:` and let the main process fetch on your behalf.
3. **Do not rely on the parent process environment**: the child process does not inherit `.env`, cannot see API keys,
   cannot read the parent's memory.

> ⚠️ The sandbox described above is **not implemented yet** in this version — today a tool pack only becomes loadable when
> `PACKS_ENABLE_PY` is on, without permission enforcement. Design notes: [`API.en.md`](API.en.md) §6.3.

---

## 8. Activation, testing, troubleshooting

### 8.1 What needs what

| You changed | How it takes effect |
|---|---|
| Any file inside a pack | **Restart the bot** (or `core.packs.reload()`) |
| `data/personas/*.json` | Restart the bot |
| Portrait art (`launcher/cards/`) | **Refresh/restart the launcher** (bot restart not needed) |
| A skin | Switching skins in settings = **immediate re-navigation**, no restart |
| `.env` | **Bot restart required** (Python does not hot-reload config) |

### 8.2 Pre-flight checklist

- [ ] `pack.json` parses as JSON (validate in an editor — no trailing commas)
- [ ] `name` is dotted lowercase / `version` is three numeric parts
- [ ] Card file has no BOM
- [ ] Card key does not collide with an existing card (a local card wins; your pack's card is then ignored)
- [ ] Portrait filenames match the card key (`<card-key>.webp`)
- [ ] Variant filenames are `<card-key>_<word>.webp`, and `<word>` appears in `sprite_expressions` or the stage pack's `map`
- [ ] Ran `tools/export_cards.py` (card packs)
- [ ] Restarted the bot and checked `data/bot.log` for warnings mentioning your pack

### 8.3 Troubleshooting

| Symptom | Cause |
|---|---|
| Pack does nothing at all, `bot.log` mentions it | `spec` / `name` / `version` / `type` failed validation — the log text states why |
| Your card is missing from `/人设` | The pack was not scanned (restarted?) or a local card with the same key shadowed it |
| Card exists but the card wall stays dark | **No portrait art** (the grid filters artless cards), or `export_cards.py` was not run |
| Portraits do not show | Filename is not `<card-key>.webp` / not under `launcher/cards/bust4w/` / launcher not refreshed |
| Variants never switch | Filename is not `<card-key>_<word>.webp`, or `<word>` is missing from the mapping table (soft validation: unknown words are dropped) |
| Quotes never appear | `quotes.json` is not at the pack root / not shaped `{"quotes":[{"q","by"}]}` / overridden wholesale by `data/quotes.json` |
| Voice does nothing | `key` collides with a built-in voice (built-in wins) / `gpt` or `sovits` is not an absolute path |
| World info not injected | The card's `universe` differs from the pack's / no entry has `always: true` |
| Tool pack does not run | `PACKS_ENABLE_PY` is off / no `permissions` declared / sandbox unavailable (not implemented yet) |

---

## 9. Publishing your mod (**read this first**)

### 9.1 License and IP red lines

- A pack **must declare its own `license`** (`CC0-1.0` / `MIT` / `private` are all fine — but declare it).
- **Never include third-party IP assets** (anime/game character art, game UI). The project checks automatically:
  `build/build_release.ps1` scans an IP word list during its SDK self-check, and **a hit fails the build**.
- Assets you drew or generated yourself: make sure you have the right to redistribute them.
- **Adult content**: distribute it **privately** (`license: private`; keep it out of public repositories).

### 9.2 Packaging and sharing

A content pack is **just a directory** — zip that directory and hand it over; the other side unzips it into `data/packs/`.
Do **not** put your pack into `qq-bot/packs/` (that location is for bundled examples and is overwritten by updates).

### 9.3 Make your pack readable

- Fill in `title` / `author` / `note` (the launcher and docs display them).
- Add a `README.md` inside the pack: dependencies, how to install, known issues.
- Use a `_` prefix for comment keys meant for machines/yourself (e.g. `"_note"`), so they never collide with future spec fields.

---

## 10. Going deeper

| Topic | Where |
|---|---|
| All fields and protocols | [`API.en.md`](API.en.md) |
| Skin pack details | [`SKIN-SPEC-v1.en.md`](SKIN-SPEC-v1.en.md) |
| Running the framework, where logs live | [`MAINTENANCE.en.md`](MAINTENANCE.en.md) · [`DEPLOYMENT.en.md`](DEPLOYMENT.en.md) |
| Live examples (breaking them is caught by tests) | `qq-bot/packs/example.sakura/` (card) · `qq-bot/packs/example.stage/` (stage) |
| Where to put your own code | `data/plugins/` (yours, never overwritten) — framework plugins live in `qq-bot/plugins/` |
