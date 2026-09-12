# API Reference (the single entry point · the contract surface for third parties and mods)

> **What this document is**: the contract for everything "external code may depend on" — content pack format, skin pack format, GAL page protocol, code extension points, sandbox permissions.
> **What this document is not**: it does not explain how to build a mod (that is `MODDING.en.md`), and it does not cover operations (that is `MAINTENANCE.en.md`).
> **Authoritative statement** (2026-09-12 documentation trim): **any field or protocol a third party depends on is governed by this document**.
> This document takes over the contract status of `PLUGIN_SDK.md` (that file is retained as a Phase 3 historical record and an SDK release artifact);
> for the **deep details** of skin packs (virtual host, the full postMessage flow, the UI asset directory) see [`docs/SKIN-SPEC-v1.en.md`](SKIN-SPEC-v1.en.md) — this document gives only the contract summary table.
> **Compatibility promise**: the schema fields and hook points listed in this document are **append-only, never modified, never removed** within v1.x.

---

## 0. Interface landscape and authoritative ownership

| Interface | Who it is for | Form | Where in this document |
|---|---|---|---|
| Content pack `robot-pack-v1` | Content authors (zero code) | Directory + JSON | §3 (full contract) |
| Skin pack `launcher-skin-v1` | Appearance authors | Directory + manifest + pages | §4 (summary table) / skin spec (details) |
| GAL page protocol | Front-end / staging authors | HTTP + WebSocket + JS hooks | §5 (complete) |
| Code extension `ToolCard` | Python developers | Register in-process | §6.1 |
| Tool pack sandbox | Third-party code packs | pack.json `permissions` | §6.3 |
| CLI / operations tools | Users | Command line | `MAINTENANCE.en.md` |

**The three extension kinds, from cheapest to most expensive**: content pack (edit JSON) → skin pack (edit pages) → tool pack (write Python, **not loaded by default**).
**"No pack = original behaviour"** is the iron rule of fallback for the whole project: the default of any interface must equal "this pack never existed".

---

## 1. Core version and compatibility promise

- Current core interface version: **`CORE_API_VERSION = "1.0.0"`** (defined in `qq-bot/core/packs.py`, extracted at build time by `build/build_release.ps1` and written into the release notes).
- A pack manifest declares the version it adapts to via `compat.core_api` (e.g. `">=1.0"`). **v1 only records it, it does not enforce it**: a version mismatch does not refuse to load.
- When a breaking change is introduced in the future, the minor version number is raised and a migration note is added to this document.

---

## 2. Directory conventions (three-layer separation · who overwrites whom)

```
<install-root>/
├─ qq-bot/plugins/        framework's own plugins  ← **overwritten** when you update a release; do not put your own things here
├─ qq-bot/packs/          bundled example content packs ← overwritten; live examples and test fixtures
├─ data/plugins/          third-party code plugins ← never overwritten; **auto-discovered** (§6.4)
├─ data/packs/            content packs (user-installed) ← never overwritten, **drop it in and it works**
└─ launcher/web/skins/    skin packs               ← never overwritten
```

**A same-named pack in `data/packs/` wins**: each of the two pack roots is scanned one level deep (only directories containing `pack.json` count), and for a same-named manifest first come, first served — a user pack shadows the bundled example.
**Cache**: cached in-process by directory mtime; after changing files inside a pack, **restart the bot**, or call `core.packs.reload()`.

---

## 3. Content pack `robot-pack-v1` (full contract)

### 3.1 `pack.json` fields

```json
{
  "spec": "robot-pack-v1",
  "name": "author.name",
  "version": "1.0.0",
  "type": "card | voice | item | emotion | world | tool | gal",
  "title": "Human-readable title (给人看的标题)",
  "author": "Author (作者)",
  "license": "CC0-1.0",
  "compat": { "core_api": ">=1.0" },
  "permissions": [],
  "limits": { "timeout_s": 5, "memory_mb": 256, "calls_per_min": 60, "result_kb": 64 }
}
```

| Field | Required | Validation |
|---|---|---|
| `spec` | ✓ | Must equal `"robot-pack-v1"` |
| `name` | ✓ | Dotted lowercase `^[a-z0-9][a-z0-9_-]*(\.[a-z0-9][a-z0-9_-]*)+$`; unique across the two roots (data wins) |
| `version` | ✓ | Three-part semantic version `x.y.z` |
| `type` | ✓ | One of the 7 kinds above; **an unknown type is skipped, not a crash** |
| `title` / `author` / `license` | – | Display strings, not validated |
| `compat.core_api` | – | v1 only records it |
| `permissions` / `limits` | – | **Meaningful only for `type=tool`**, see §6.3; default = zero capability |

- **Unknown extra keys**: carrying out-of-spec keys is allowed — the loader reads only the fields above and ignores the rest without erroring. Comment keys meant for machines should use a `_` prefix (e.g. `_comment`), which can never collide with future spec fields.
- Reading tolerates a BOM (utf-8-sig); **publishing without a BOM is recommended**.
- Single-file read cap **8 MB** (idiot-proofing); a relative path inside the pack must still resolve inside the pack directory (**path traversal refuses the whole pack**).

### 3.2 `type=card` (character card + quotes + voice + portrait + variants: one pack = one complete character)

```
packs/example.sakura/
  pack.json              type=card, plus "card":{"key":"sakura", ...launcher display fields (optional)}
  card.json              character card (chara_card_v2, same format as data/personas/*.json)
  quotes.json            signature quotes {"quotes":[{"q":"…","by":"…"}]}      (optional)
  voice.json             voice (same format as §3.3)                           (optional)
  sprites/manifest.json  {"expressions":["word",…],"map":{"tone word":"asset word"}}   (optional)
  sprites/full|bust|diff/<key>_<word>.webp                                     (optional, see below)
  world.json             world info block (embeddable, or a standalone type=world pack)   (optional)
```

| Sub-section | Notes |
|---|---|
| `card.key` | Required. Card key `^[A-Za-z0-9_-]+$`: the persona fallback key, the portrait URL key, the key in `cards.json` |
| `card.name/cls/star/uni/desc` | Optional, launcher card wall display fields; defaults `cls="MOD", star=4, uni="PACK"` |
| `card.json` | Required. `{"spec":"chara_card_v2","data":{…}}`, fields per `qq-bot/data/personas/_template.json`. **Carrying `universe` is recommended** (links the scene vocabulary, world info injection, same-universe character recognition) |
| `quotes.json` | Optional. Goes into `/gal/content.json` as `quotesByCard[card.key]` |
| `sprites/manifest.json` | `expressions` = variant menu words (**when the card carries `sprite_expressions`, the card wins**; this is the fallback source); `map` = tone word → asset word (the front-end assembles `<key>_<asset word>.webp` and probes for it) |
| `sprites/full\|bust\|diff/` | Asset directories. **Naming convention**: `<card-key>_<word>.webp\|png` (full body / bust / variants share the name family; the wordless base image is `<card-key>.webp`). **v1 boundary**: the runtime does not move pack assets around on its own — the real portrait slots live in `<install-root>/launcher/cards/` (see §3.9) |

**Card pack wiring points (all backward compatible — no pack = original behavior)**:
1. **persona fallback**: when `data/personas/<key>.json` does not exist, it is loaded from the pack's `card.json` (marked `source:"pack"`); **a local card always wins**.
2. **Externalized quotes**: pack quotes are the base layer → `data/quotes.json` overrides them key by key (the user data layer).
3. **Voice merge**: see §3.3 (`setdefault`, the built-in table wins).
4. **Variant menu**: card has no `sprite_expressions` → fall back to the pack's `sprites/manifest.json` `expressions`.
5. **Launcher card data**: `tools/export_cards.py` scans packs → **appends only** new keys into `data/cards.json` (no need to rebuild the exe).

### 3.3 `type=voice` (standalone voice pack; a card pack may also embed `voice.json`)

```json
{ "key": "mysound", "name": "My voice (我的音色)", "lang": "zh", "personas": ["character display name (角色显示名)"],
  "gpt": "C:\\abs\\model.ckpt", "sovits": "C:\\abs\\model.pth",
  "ref_dir": "C:\\abs\\refs", "refs": "voice_refs.json",
  "emo_refs": {"战斗": "reference entry name"} }
```

<!-- translator note: `emo_refs` keys are the nine tone names defined by the framework itself — Chinese literals the software matches on verbatim, so their names are intentionally left untranslated here. -->

- The key resolves as `voice.json.key` > `card.key` > the last segment of the pack name.
- `gpt` / `sovits` / `ref_dir` **must be absolute paths**; relative-path entries are **skipped + warning** (GPT-SoVITS weights belong to `tools/`, gitignored: **a public release carries only the format and the docs, never the weights**).
- Merge discipline: `setdefault` into `VOICES` — **the built-in table wins**, and a same-named key (such as `amiya`) is not overridden by a pack.
- `emo_refs` keys use the nine-tone vocabulary (战斗/battle, 高冷/aloof, 讥讽/sarcastic, 温柔/gentle, 无语/speechless, 娇嗔/coquettish, 疑问/questioning, 纯H/pure-H, 傲娇/tsundere); a missing tone falls back to the "default" reference.

### 3.4 `type=item` (item mod)

```json
"items": [ { "name": "Magic teacup (魔法茶杯)", "label": "Magic teacup (魔法茶杯)", "note": "Refills itself (会自己续杯)", "ttl_min": 5 } ]
```

| Field | Validation |
|---|---|
| `name` | Non-empty after sanitizing (whitespace and structural characters stripped, capped at 8 characters) |
| `label` | Non-empty and ≤12 characters (display name) |
| `note` | Optional, default effect copy (≤80 characters) |
| `ttl_min` | Number >0 (default time-to-live in minutes) |

- Semantics: a directory entry **provides a default presentation**; it **does not change openness** — items outside the directory are still registered as an open set.
- Built-in items are unaffected by the directory.

### 3.5 `type=world` (world info / lorebook, tavern-style; standalone pack or embedded in a card pack)

```json
{ "universe": "stellar-teahouse",
  "entries": [ { "keys": ["trigger words (触发词)"], "text": "setting text (设定文本)", "always": true, "priority": 0 } ] }
```

| Field | Notes |
|---|---|
| `universe` | World info key — aligned with the persona card's `universe`, `data/universe_roles.json`, and `qq-bot/data/scenes.json` |
| `entries[].keys` | Trigger word list (**v1 does not implement keyword triggering**, reserved by the schema) |
| `entries[].text` | Setting text (included only when non-empty) |
| `entries[].always` | true = always-on injection |
| `entries[].priority` | Integer, stable descending sort (equal priority keeps file order; sorted globally after cross-pack merging) |

- **v1 behavior**: only `always:true` entries are injected, formatted `\n\n【世界观设定】\n- text` (【世界观设定】 = "World info setting"), with a whole-block cap of **800 characters** (truncated + annotated when over). No world pack / card has no universe = zero injection.
- **v2 roadmap**: `keys` keyword-triggered matching (injected only when it hits recent context); `always` entry behavior is unchanged — a purely additive change.

### 3.6 `type=emotion` (extra emotion mod)

```json
"tones": [ { "word": "得意", "sprite_word": "proud", "voice": { "temp": 1.0, "speed": 1.05, "semi": 0.5 } } ]
```

| Field | Notes |
|---|---|
| `word` | Emotion tone word (≤6 characters, aligned with the 【基调：两三字】 (tone: two or three characters) convention) <!-- translator note: 【基调：两三字】 is the in-prompt bracket protocol that asks the model to self-label a tone in two or three characters; the framework matches that bracket text verbatim. --> |
| `sprite_word` | Optional: variant asset word → goes into `spriteMap[card.key]` (only for tones from a card pack) |
| `voice` | Optional: `temp` temperature / `speed` speech rate / `semi` semitone offset; **an empty `{}` is allowed** = extend only the sprite/copy layer |

- A tone word with a complete voice is **merged into `EMO_PARAMS` via `setdefault`** (the built-in nine tones win); an empty voice does not enter the tone table.
- **Convention boundary**: the 9-class LLM emotion classification vocabulary is **not extended for now** — new words take the 【基调】 (tone) self-labeling pass-through path.

### 3.7 `type=tool` (tool pack = code, **not loaded by default**)

```json
"tools": [ { "entry": "relative.py", "cards": ["ns.name"] } ],
"permissions": ["fs.read:./assets/**", "net:api.example.com"],
"limits": { "timeout_s": 5, "memory_mb": 256, "calls_per_min": 60, "result_kb": 64 }
```

- `entry` is a path relative to the pack; `cards` declares the ToolCard names this entry provides (`namespace.name`).
- **No python is executed by default**: only an explicit `PACKS_ENABLE_PY` loads them. The default path has **zero imports, zero execution, zero child processes**.
- Permissions and the sandbox contract: see §6.3.

### 3.8 `type=gal` (GAL stage pack · settled 2026-09-12)

Pulling "**what the stage looks like**" out of the character card and the skin — variant pixels and backgrounds belong to the stage, the word list belongs to the character, the shell belongs to the skin.

```json
{"spec":"robot-pack-v1","type":"gal","name":"author.stage-room","version":"1.0.0",
 "title":"","author":"","license":"private (私用)","compat":{"core_api":">=1.0"},
 "stage":{"default_bg":"room_night","bg_alt":"room_day","bg_by_scene":{"天台":"rooftop"}}}
```

<!-- translator note: the `bg_by_scene` key "天台" is a scene word matched against free-form model output, so it stays Chinese; it means "rooftop". -->

```
<pack>/
  pack.json
  bg/<bg-id>.<png|jpg|webp>          ← background (variation axis = scene)
  sprites/diff/<card-key>_<word>.webp  ← variants (variation axis = card key × word)
  sprites/manifest.json              ← optional: map overrides the card pack's same-named key
```

| Field | Notes |
|---|---|
| `stage.default_bg` | Default background id when no scene matches (corresponds to `bg/<id>.<ext>`) |
| `stage.bg_alt` | Alternative when the default background fails to load |
| `stage.bg_by_scene` | `{scene word: background id}`; scene words come from lifesim's `scene` (free-form text generated by the model) |

- **Scene matching must be three-level**: `exact → contains → default`. `scene` is free text — `天台上看星星` (watching stars on the rooftop) will never equal `天台` (rooftop), so doing equality matching alone is the same as doing nothing.
- **Asset resolution priority** (following "pack as base → local machine overrides"): `bundled example pack (zero-IP placeholder art) < data/packs/<stage pack> < launcher/cards/ (local private assets)`.
- **Naming convention reused** — `sprites/full|bust|diff/<key>_<word>.webp`: existing assets are **copied into the pack verbatim, zero renaming**.
- The three variation axes of character cards are orthogonal: card packs provide the **words** (`sprite_expressions` / `map`), the stage pack provides the **images**, and the skin pack provides the **shell** (palette / controls / layout).

> **Implementation status (implemented since 2026-09-12)**: `gal` is already in `VALID_TYPES`, and both indexing and parsing have landed.
> Service endpoint: **`GET /gal/stage/<pack-name>/<path-inside-pack>`** (see §5.1) — it serves both backgrounds and variant pixels;
> a background id whose **file does not exist never appears in the index** (so the front-end falls back to a CSS gradient instead of getting an address that is guaranteed to 404).
> Variant vocabulary: `sprite_words()` **scans multiple roots** in the order `stage pack sprites/diff/` → `launcher/cards/bust4w`, pack first.
>
> **An off-the-shelf way to produce local private assets**: `qq-bot/tools/export_content_packs.py` packs the portraits from
> `launcher/cards/` (variants / bust / full body) and the backgrounds from the skin directory into
> `release/local-content/arknights-stage-<date>.zip` — **a byte-for-byte pure copy, zero renaming**;
> unzip it and drop `packs/local.stage/` into `data/packs/` and it works.
> It **deliberately does not write the `map` in `sprites/manifest.json` and does not declare `card.key`**: card packs already declare
> their own "word → asset word" mapping, so a stage pack writing another copy would be a second source of truth (which is exactly what this section exists to avoid).

### 3.9 Bad pack self-check (look here first when a pack does not take effect)

- **bot side**: the reason a bad pack was skipped goes through a loguru warning → the bot console + `<install-root>/data/bot.log` (grep by pack name).
- **launcher side**: launcher script exceptions (including a missing skin directory falling back to generic) are recorded in `<install-root>/launcher/boot_err.log`.
- Common causes: `pack.json` is not JSON / `spec` is missing or wrong / `name` is not dotted lowercase / `version` is not three parts / unknown `type` / path traversal. **The whole pack is always skipped — never half-loaded**.
- **Portraits do not show**: in-pack `sprites/` assets have **no runtime consumer in v1** — the real portrait slots are `<install-root>/launcher/cards/` (full body `<key>_full.png`, bust `bust4w/<key>.webp`, variants `bust4w/<key>_<word>.webp`). Assets must be named accordingly and placed in that directory. Missing art is covered by the front-end fallback chain (variant → default bust → full body → first-character placeholder), and **cards without a portrait are filtered out of the launcher grid** (no error).

---

## 4. Skin pack `launcher-skin-v1`

> This section is the **contract summary table**; for how the virtual host is stood up, the full postMessage
> flow, the `ui/` asset directory and other details, see [`docs/SKIN-SPEC-v1.en.md`](SKIN-SPEC-v1.en.md).

### 4.1 `manifest.json` fields (UTF-8 without BOM)

| Field | Type | Description |
|---|---|---|
| `spec` | string | Fixed `launcher-skin-v1` |
| `name` | string | Skin identifier (= directory name, the value of the `skin` key in `data/launcher.json`) |
| `title` | string | Display name (Settings page dropdown) |
| `entry` | string | Entry page, relative to the skin directory |
| `palette.bg/fg/accent/muted` | string | Four colors (HEX); `bg` is also used for the WPF window background solid-color brush |
| `statePath` | string | Runtime-relative path of state.json (informational; polling goes by **the directory the page lives in**) |
| `operaPage` | bool | **Whether the skin ships its own "Operators (干员)" page** (optional, defaults to false). true = both the framework top bar's "Operators (干员)" and `cmd:"operators"` open the **skin's own** operator view (push `page:"opera"`, rendered by the skin); false/default = fall back to the framework's native Operators page. A skin-drawn operator UI takes priority over the framework's |
| `author` | string | Author (optional) |
| `license` | string | License identifier |
| `note` | string | Note (optional) |

### 4.2 Virtual hosts (the only absolute address form usable inside a page)

| Host name | Maps to | Purpose |
|---|---|---|
| `https://app.local/…` | `launcher/web/` | Launcher pages and assets; the skins subtree is reachable as a matter of course |
| `https://cards.local/…` | `launcher/cards/` | Character portraits (`bust4w/` bust+variant, `fullw/` full body) |

> Assets referenced inside a page must always use the `app.local` absolute address or a path relative to the skin
> directory; `http://127.0.0.1:8080` **is only for the GAL page and the bot HTTP channel**.

### 4.3 `state.json` data contract (the primary PS → page channel)

Push: the launcher rewrites `state.json` in the active skin directory wholesale about every **3 seconds**
(non-atomic write; a polling side that fails to parse it silently skips that frame).
Poll: the page script fetches `state.json?t=<millisecond timestamp>` from **the page's own directory** every
**1 second** — **the `?t=` cache-buster is not optional**.

| Field | Type | Description |
|---|---|---|
| `__push` | bool | Always `true` (push marker) |
| `stamp` | int | Auto-incrementing sequence number (diagnostics) |
| `pid` | int | Launcher PID (a change = two instances are running) |
| `time` | string | `yyyy/MM/dd HH:mm` |
| `scene` / `doing` / `mood` / `plan` | string | From `life_state.json` |
| `dialog` | string | The `doing` dialogue line with `【…】` (state-title brackets) stripped |
| `wake` | string | Description of the next heartbeat |
| `name` / `star` / `idtext` | string | Owner nickname / star rating `★×n` / `<card-name> · <faction>` |
| `portrait` | string | URL of the current character portrait (empty string when there is none) |
| `page` | string | Page command: `home`/`opera`/`detail` (`cfg`/`log`/`state` are WPF-native pages, not visible to web) |
| `op` | object | `{active: bool, text: string}` operation-in-progress flag |
| `svc` | object | Four service lamps `{engine, embed, napcat, bot}`, four bools (true=running); on older versions without this field the page keeps grey lamps |
| `cards` | array | Card wall payload; **cards with no usable portrait are filtered out** |

A single `cards[]` entry: `key` / `name` / `cls` / `uni` / `desc` / `starText` (the ★ text, **not** the `star`
number) / `img` (bust `https://cards.local/bust4w/<key>.webp`) / `big` (full body `fullw/<key>.webp`) /
`rar` (star-rating corner image `https://app.local/skins/<skin>/ui/ark/rarity_<n>.png`; **skin-aware**: empty
string for the generic skin, and the front-end skips it when empty).

### 4.4 Command channel (JS → PS)

```js
window.chrome.webview.postMessage({ cmd: '<cmd>', data: '<string payload>' });
```

**`data` is always a string** (the card key for `setpersona` goes right here, do not nest an object).

| cmd | data | Behavior |
|---|---|---|
| `start` | empty | Start everything (engine → memory → NapCat → bot stepping machine) |
| `stop` | empty | Stop everything (including the LLM, freeing VRAM) |
| `stopbot` | empty | Stop the bot only (the LLM stays up) |
| `exit` | empty | Stop everything, then close the window and quit |
| `opera` / `home` | empty | Switch the Home web view and push `page:"opera"` / `page:"home"` |
| `operators` / `deps` / `about` | empty | Open the **Operators / Components / About** pages (the web view is covered entirely). ⚠️ Operators is **not** "always the framework page": when the skin ships its own operator page (`manifest.operaPage: true`) that view opens instead, and the framework's native page is only the fallback (layering decision: [`docs/SKIN-SPEC-v1.en.md`](SKIN-SPEC-v1.en.md) §9) |
| `cfg` / `plugins` / `log` / `state` | empty | Open the WPF-native Settings / Plugins / Log / Status pages (the web view is covered) |
| `setpersona` | **card-key string** | POST `http://127.0.0.1:8080/launcher/persona` (body `{"name":"<card-key>","uid":"<owner-QQ>"}`); switches the persona while the bot is online; when the channel is down it falls back to writing `persona_select.json` (takes effect on restart) |

> Reserved words `diag` (the whole message lands in `web_diag.log`) and `hb` (heartbeat) — a skin never needs them.
> The alternative URL channel (`?cmd=<cmd>&d=<urlencoded>`) is used only by the bundled Arknights sample skin;
> new skins need not depend on it.

### 4.5 The two global functions a skin **must** provide

The launcher calls them directly via `ExecuteScriptAsync("showWebBlock('…')")`:

```js
window.showWebBlock = function (text) { /* full-screen overlay + ⚠ banner; strip the ⚠ (…) ornament from the text yourself, use the default text when empty */ };
window.hideWebBlock = function () { /* retract overlay and banner together */ };
```

Polling `op.active` and calling them directly are **mutually redundant** (whichever arrives first takes effect).
Reference implementation: `launcher/web/app.js`.

### 4.6 The skin override chain for the GAL asset surface

A skin may define `window.GAL_SKIN` **before the GAL client script loads** to override the asset surface:

```js
window.GAL_SKIN = { assets: { bgDefault: 'https://app.local/skins/<skin>/img/x.png' } };  // same shape as ASSETS
```

**Override order (important)**: `GAL_SKIN.assets` is applied with a synchronous `Object.assign` when the script
loads; `/gal/content.json` is **fetched asynchronously after startup, and what arrives later overrides** — the
`quotesByCard` / `quoteFallback` / `spriteMap` **three keys** carried by content.json override the same-named keys
in `GAL_SKIN.assets` (**those three keys only**; backgrounds and the remaining asset keys are unaffected).

---

## 5. GAL page protocol (HTTP + WebSocket + JS hooks)

### 5.1 HTTP routes

| Method | Path | Description |
|---|---|---|
| GET | `/gal` | The GAL page itself (`FileResponse` of **launcher/web/gal.html**; a missing file returns explanatory text rather than a 500) |
| GET | `/gal/assets/{name}` | Static assets from the same directory, **whitelist** `{gal.js, gal.css}` (to prevent path traversal) |
| GET | `/gal/stage/{name:path}` | **Stage assets** (the `bg/`, `sprites/diff/` inside a `type=gal` pack). Shaped like `<pack-name>/bg/room.png`; three checks: the pack must exist and be `gal` + resolve blocks path traversal + the file must exist. A non-gal pack, traversal, or a missing file is always a 404 |
| GET | `/gal/content.json` | Content index: `{quotesByCard, quoteFallback, spriteMap, stage}`. **The semantics of the three existing keys are unchanged character for character**; `stage` is an incremental key from 2026-09-12 T14 (stage pack backgrounds and scene mapping, see §3.8). The top-level key list is verified by consistency-audit item K against `_build_content()` in `core/packs.py` — change the payload and you must change this line in step |
| GET | `/gal/state` | Current life/persona status snapshot |
| GET | `/gal/lifelog` | Life trajectory |
| GET | `/gal/history` | GAL recollection rounds (JSONL) |
| POST | `/gal/tts` | body `{text, tone}` → speech (base64); lets the page request it per page |
| WS | `/gal/ws` | Frame protocol, see §5.2 |
| POST | `/launcher/persona` | body `{"name":"<card-key>","uid":"<owner-QQ>"}` (used by the launcher's `setpersona`) |
| GET | `/launcher/diag` | **For local-machine troubleshooting** (`bot.py`, implemented in the nonebot driver's app): returns the current persona name / card name / `owner_mode` / the contents of `persona_select` / the selection file path / `superusers` / the persona policy. ⚠️ **No authentication, and the return value contains the owner QQ and local paths** — use it on the local machine only, never expose 8080 to the LAN or the public internet; for permission hardening see [`遗留任务.md`](遗留任务.md) *(Chinese)* A7 |

### 5.2 WebSocket frame protocol (`/gal/ws`, single connection + token authentication)

**Authentication**: after connecting you must send `auth` within the time limit, or you receive
`err{reason:"auth timeout"}`; when a connection already exists, a new connection receives `err{reason:"busy"}`
(**single connection** semantics).

| Direction | Frame | Description |
|---|---|---|
| In | `{type:"auth", token}` | Must be sent first; the token is in `data/webgal_token.txt` |
| In | `{type:"msg", text}` | User utterance (equivalent to speaking in chat mode) |
| In | `{type:"mode", mode}` | Switch mode `gal` / `qq` / `chat` |
| In | `{type:"ping"}` | Heartbeat |
| Out | `{type:"auth_ok"}` | Authentication passed |
| Out | `{type:"reply_start"}` | A round begins (the front-end clears its queue / stops the previous round's audio) |
| Out | `{type:"seg", kind, text, …}` | Body segment; `kind ∈ text \| action \| slice` |
| Out | `{type:"sprite", emotion}` | **Sent every round**: a word = switch to that variant; empty = the client returns to the default bust (prevents the previous round's leftover) |
| Out | `{type:"state", …}` | Status snapshot |
| Out | `{type:"reply_end", mode}` | A round ends |
| Out | `{type:"err", reason}` | `busy` / `auth timeout` / `idle timeout` / `bad_json` / an exception within the round |

> Value priority of the `sprite` frame: `_SPRITE_LAST` (the agent's own 【立绘：词】 (portrait: word) self-decided
> slot) > `_TONE_LAST` (【基调】 (tone)).

### 5.3 The asset surface `ASSETS` (top of the GAL client, the only place to swap assets)

| Key | Meaning |
|---|---|
| `bgDefault` | Default background URL |
| `bgAlt` | Alternative for when the default background fails |
| `bgByScene` | `{scene: background-URL}`; filled from the stage pack's `stage.bg_by_scene` (**no longer an empty hook** since 2026-09-12 T14) |
| `charColor` | Name plate / primary button color |
| `bustBase` | Bust portrait baseURL (`https://cards.local/bust4w/`) |
| `fullBase` | Full body portrait baseURL (`https://cards.local/fullw/`) |
| `spriteMap` | `{card-key: {tone-word: asset-word}}`, the front-end probes `<card-key>_<asset-word>.webp` by concatenation |
| `quotesByCard` | Per-card quotes (**mounted on ASSETS at runtime**, which is why `GAL_SKIN.assets` can override them); then merged and overridden by `quotesByCard` from `/gal/content.json` |

> ⚠️ **`quoteFallback` is deliberately not in the table above**: it is the fourth key of `/gal/content.json`, and
> it lands in the module variable `QUOTES_FALLBACK`, **not going through `ASSETS`** — so a skin writing
> `GAL_SKIN.assets.quoteFallback` **has no effect** (the read site is in `rollQuote`:
> `ASSETS.quotesByCard[card-key] || QUOTES_FALLBACK`). To swap the fallback quotes go through a
> content pack / `data/quotes.json`. For whether this capability gap is opened to skins, see
> [`docs/遗留任务.md`](遗留任务.md) *(Chinese)* A10.
> This table and the key set of `launcher/web/gal.js` are cross-checked both ways by
> consistency-audit **item K at field level** (the code side also collects the runtime `ASSETS.x =` assignment keys).

**Fallback chain**: variant `bust4w/<key>_<word>.webp` → default bust `bust4w/<key>.webp` → full body
`fullw/<key>.webp` → first-character placeholder; background failure → CSS gradient fallback. **Missing art is the
norm and is not an error**.

**Scene matching is three-tiered (landed 2026-09-12 T14)**: `exact → longest key contained → default image`.
Because `scene` is text the model **generates freely** via lifesim (e.g. 「天台上看星星」 *(Chinese: watching stars on
the rooftop)*), while the keys of `bg_by_scene` are short human-written words (「天台」 *(Chinese: rooftop)*) —
equality matching alone amounts to not matching at all. The longest contained key wins (the longer the key, the
more specific; 「天台」 beats 「天」 *(Chinese)*).

### 5.4 Live2D / external renderer hooks

The page defines this **before the GAL client script loads**:

```js
window.GAL_RENDERER = { init(stage), onFrame(seg), onStop() };
```

- `init(stage)` is called once at startup (`stage` = the `#stage` DOM; the renderer builds its own layer / loads
  its model)
- `onFrame(seg)` is called as each segment arrives; returning `false` or throwing = **that segment falls back to
  the built-in typewriter/page queue** (conservative fallback, never stalls the round)
- `onStop()` is called when switching rounds / clearing the queue
- **Undefined = everything takes the original path**, behavior identical to previous versions

---

## 6. Code-level extension

### 6.1 The `ToolCard` contract (`agent/registry.py`)

```python
from agent.registry import register, ToolCard, FAIL_RETURN_NONE, FAIL_RAISE

@register(name="ns.name", description="capability description", failure=FAIL_RETURN_NONE, needs_llm=False)
async def my_tool(arg): ...
```

| Field | Description |
|---|---|
| `name` | Globally unique, dotted namespace; registering the same name raises `ValueError` (`unregister` first) |
| `description` | Written for humans and for perception assembly to read |
| `fn` | The execution function (async or sync both fine; `None` = placeholder / already unregistered) |
| `failure` | `FAIL_RETURN_NONE` (failure counts as "did not get it") / `FAIL_RAISE` (failure raises, the caller decides) |
| `needs_llm` | Whether execution depends on the LLM (used for trigger decisions, **not** a routing basis) |

> **Iron rule #1 (the precondition for the registry to exist)**: the registry is a **capability list, not a router**.
> Triggering is still decided by message decisions / agent instruction; **the model is forbidden to pick tools freely
> from the registry** (no LLM function-calling).
> `all_tools()` returns a copy, for perception assembly to iterate over.

### 6.2 Index of stable hook points

| Hook | Location | Description |
|---|---|---|
| Background loops | `_start_background_loops` in `bot.py` / `plugins/debug.start_background_loops_async` | Boilerplate for registering custom loops (idempotency discipline) |
| special marker protocol | `apply_agent_markers` / `apply_owner_facts` in `core/special.py` | Registering/clearing state = **an agent-decided marker**; the machine only stores facts and does not write the performance for you |
| GAL frame protocol | `plugins/webgal` (§5.2) | The only performance channel between frontend and backend |
| `/gal/content.json` | `core.packs.content_index()` | Data source for quotes / variant mapping |
| Skin pack | §4 + `SKIN-SPEC-v1.en.md` | manifest / state / postMessage / virtual host / `GAL_SKIN` |
| Live2D renderer | §5.4 | Performance takeover point |
| ToolCard | §6.1 | Code-level capability registration |
| Content pack discovery | `core/packs.py` (`discover` / `by_type` / `content_index` / `voice_index` / `world_index` / `items_index` / `tones_index`) | Index views |

**Iron-rule alignment**: the machine does only five things — protocol marker stripping, control code parsing,
**permission boundaries**, time-window fallback, and incident-level red lines.
Everything else (what to say, how to do it, whether to attach an image, whether to enable thinking) is **entirely agent-decided**.

### 6.3 Tool pack sandbox contract (`type=tool`)

**Current state**: `PACKS_ENABLE_PY` is **off by default** — while off, not even an import happens, so an ordinary user
installing data packs carries zero risk.
Once enabled, tool packs execute inside an **isolated AppContainer child process** (work item S4, feasibility already measured in practice).

**Permission vocabulary** (`permissions`, default = `[]` = zero capability):

| Capability word | Meaning | Enforcement |
|---|---|---|
| `fs.read:<pattern>` | Read-only; after resolve it must land inside the pattern | **kernel (ACL)** |
| `fs.write:<pattern>` | Writable; undeclared means refused | **kernel (ACL)** |
| `net:<host>[:<port>]` | **Declares "who you want to reach"**; a child process's network is always 0, so the request is **fetched on its behalf by the main process** and checked against the whitelist | structural (main-process check) |
| `llm` | Allows calling the model through a main-process proxy (**credentials never leave the main process**) | structural |
| `env:<NAME>` | Allows reading the named environment variable | structural |

- Pattern rules: a leading `./` is relative to the pack directory; `*` wildcards within a segment, `**` across segments; **whitelist only, no negation operator**; before loading, patterns are resolved to absolute paths and ACLs are applied (no string comparison at runtime, so `..` and symlink bypasses are inherently immune).
- `limits`: `timeout_s` (default 5) / `memory_mb` (256) / `calls_per_min` (60) / `result_kb` (64). **The core side sets hard ceilings; a pack cannot relax them for itself**.
- **Not provided**: `exec` (spawning external programs) / `process` / `registry` / `gui` / `clipboard` / `inject` — such needs should be served by a controlled channel in the main process.
- Failure semantics: when the sandbox is unavailable, **always fail-closed** (the tool pack does not execute), and **never fall back to in-process `exec_module`**.

### 6.4 Directory conventions: where third-party code plugins go

| Kind | Location | On a release update | How it is loaded |
|---|---|---|---|
| Framework plugin | `qq-bot/plugins/` | **Will be overwritten** | The explicit 13-item list in `pyproject.toml` |
| **Third-party code plugin** | `data/plugins/` | **Never overwritten** | **Auto-discovered** (since 2026-09-12 S3): subpackages (directories containing `__init__.py`) and single-file `.py` are both supported |
| Content pack | `data/packs/` | Never overwritten | `core.packs` dual-root scan |
| Skin pack | `launcher/web/skins/` | Never overwritten | The launcher selects it by the `skin` key in `data/launcher.json` |

Loading discipline for `data/plugins/` (three rules, **all of them pits we fell into**):

1. **A bad plugin must not crash the bot**: load one by one, isolate one by one, log a warning for anything that fails to load and keep going.
2. **`nonebot.load_plugin()` does not raise** — internally it swallows the import error, prints just one line
   `[ERROR] nonebot | Failed to import "x"`, and then returns as usual. So deciding "did it actually load" **requires
   comparing the before/after difference of the loader registry**, not a try/except. (The first version of this framework
   accordingly reported bad plugins as `loaded`; the log was lying.)
3. **The path is appended, not inserted at the front**: inserting at the front would let a plugin named `json.py`
   **shadow the standard library** — that is the hardest class of failure to trace. The cost is only "a plugin that
   shares a name with the standard library does not take effect", which is acceptable.

> A plugin name is taken from the **last segment of the module** (a plugin placed in `data/plugins/my_thing/` is named
> `my_thing`, not `data.plugins.my_thing`). Directory names starting with `.`/`_` are skipped (convention: a leading
> underscore means private/temporary).
>
> **To start a background task inside a plugin, use `core.bgtasks.spawn(coro)`, do not call `asyncio.create_task` bare**:
> asyncio holds only a **weak reference** to tasks — if you do not keep the return value, the task may be garbage-collected
> between two awaits, showing up as **silently no longer running** (no exception, no log). `spawn` keeps the reference
> until the task finishes and records one line of debug clues for a failed background task. This framework fell into this
> pit itself (six resident loops — watchdog / life simulation and others — were all called bare at one point), and the
> consistency-audit **item N** will scan out bare calls.

### 6.5 Local content override table: display names and effect text for special states

**The dividing line between capability and content** (settled after the 2026-09-12 decision to open-source everything):

| Side | Content |
|---|---|
| **Framework (code, public)** | The state kind **whitelist** `core.special.STATES` (`hypno`/`brainwash`/`no_fake`/`vibe_mood`; items go through the open prefix `item:<name>`), the marker protocol regexes (`【催眠：…】`/`【洗脑：…】`/`【道具：…】`/`【不许装了】`/`【解除：…】`)<!-- translator note: in order these mean hypnosis / brainwashing / item / stop faking it / release. These Chinese strings are exactly what the code matches on, so they are never translated. -->, TTL policy, perception injection format |
| **Content (local files, travels with your content pack)** | Display names (display words such as "催眠" *(hypnosis)*) and the default item effect text |

Content-side file: **`data/special_content.json`** (UTF-8, a missing file = neutral defaults, **no error**):

```json
{
  "labels":     { "hypno": "display name (显示名)", "item:ball": "item display name (道具显示名)" },
  "item_notes": { "item:ball": "default effect text when the owner gives no brackets (主人未给括号时的默认效果原文)" }
}
```

Read chain (from highest to lowest priority): **the owner's bracket text → `item_notes` → the content pack directory's `note` → empty**;
display name: **`labels` → the content pack directory's `label` → the kind itself**.

> **Why split it this way**: the state whitelist and the marker protocol are **a contract third parties must copy verbatim**
> (cards and prompts all interoperate through it) and must be public; whereas "what word this state should be displayed as,
> what effect to write by default" is **presentation** and follows the content — this is also how this project lands
> "R18 content does not travel with the public repo, capability and interfaces are public"
> (see `docs/R18分层-spec-2026-09-12.md` *(Chinese)*).
> Note: the `state kind whitelist` is **not** a list of display names — externalizing it as display names would break the
> entire state machine on a third party's install (`set_state` relies on it to decide validity).

---

## 7. Common misconceptions (**read this before asking**)

| Misconception | Reality |
|---|---|
| "The `sprites/` assets inside a pack take effect automatically" | ❌ v1 **does not auto-move them**; the real portrait slot is `launcher/cards/` (§3.9) |
| "Dropping a pack into `data/packs/` makes it take effect hot" | ❌ You need to restart the bot, or call `core.packs.reload()` |
| "A tool pack runs as soon as you drop it in" | ❌ `PACKS_ENABLE_PY` is off by default; and you must declare `permissions` |
| "A skin can change the GAL background" | ✅ It can (`GAL_SKIN`), but `/gal/content.json`'s **existing three keys arrive later and override** (§4.6; `stage` is an incremental key and does not override them) |
| "A local character card will be overridden by a content pack" | ❌ **Local always wins** (if `data/personas/<key>.json` exists it is used) |
| "A world pack's `keys` triggers setting injection" | ❌ v1 only injects `always:true`; `keys` is reserved for v2 |
| "Expressions pick images by 【基调】 (tone)" | ❌ 【基调】 (tone) only drives **voice tone**; images are picked by the 9-class emotion judge (the two tables hand over only one 「无语」 *(speechless)*) |
| "The registry lets the model choose tools by itself" | ❌ **Iron rule #1 forbids it explicitly** (§6.1) |
