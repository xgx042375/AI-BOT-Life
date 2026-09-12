# Skin Pack Interface Specification v1 (`launcher-skin-v1`)

> Output of the 2026-09-10 Phase 2 generalisation work. This document describes the complete interface
> between the QQ AI launcher (`Launcher.ps1` / `QQAI-Launcher.exe`) and a WebView2 home-page skin pack:
> directory layout, manifest, the `state.json` data contract, the PS↔web command channel, virtual host
> mappings, the card data source, and the GAL page skin hooks.
> Every field is extracted from real code (`Launcher.ps1` / `web/app.js` / `skins/arknights/bridge.js`) — no speculative fields.

## 1. Positioning and directory layout

A skin pack = a self-contained directory under `launcher/web/skins/<name>/` (pages + assets + manifest).
The launcher picks the active skin from the `skin` key in `data/launcher.json`; if the skin directory is
missing it falls back to `generic` automatically.

```
launcher/web/                          ← root of the WebView2 virtual host app.local
├─ index.html / home.css / app.js      ← generic default home page (the skin body lives at the web root — see the entry note)
├─ gal.html / gal.js / gal.css         ← GAL page (served by the bot: http://127.0.0.1:8080/gal, skin-independent)
├─ state.json                          ← runtime state (gitignored, wholesale rewrite by Push-Web)
└─ skins/
   ├─ generic/                         ← built-in default skin (tracked): manifest.json + palette.css + README.md
   └─ arknights/                       ← Arknights sample skin (gitignored, not shipped in public releases): full SPA + ui/ assets + manifest.json
```

- **entry / statePath relative path rule**: both resolve relative to the skin directory itself.
  - The generic skin's page body sits at the web root (legacy layout), so `entry:"../../index.html"` and
    `statePath:"../../state.json"` point back to the web root;
  - Arknights is fully self-contained: `entry:"index.html"`, `statePath:"state.json"` point inside the skin directory.
- **Current state of the `entry` field (honestly)**: actual launcher navigation is derived by `Get-SkinEntryUrl`
  (generic → `https://app.local/index.html`; self-contained skin → `https://app.local/skins/<name>/index.html`) —
  **the value of `entry` is currently ignored**; the entry file must be named `index.html`. `entry` is kept as a
  declaration (v2 candidate: actually read `entry`, decide on one or the other).
- **The generic sample manifest is a pure sample**: the generic skin is rendered by built-in code, and at runtime
  `Get-SkinManifest` reads the `manifest.json` at the **web root** for generic (no such file there → falls back to the
  built-in default hash) — `skins/generic/manifest.json` itself is **never read**, it exists only so skin authors can
  compare the field set; changing it will not change how generic looks.
- **`app.local` virtual host**: `https://app.local/…` is the `launcher/web/` directory, so the `skins` subtree is
  reachable by construction (e.g. `https://app.local/skins/arknights/index.html`).
- Pages must reference assets either via absolute `app.local` addresses or via paths relative to the skin directory;
  `http://127.0.0.1:8080` is used only by the GAL page and the bot HTTP channel.

## 2. manifest schema (`launcher-skin-v1`)

The manifest is the `manifest.json` in the skin directory, UTF-8 without BOM. The field set is fixed (both built-in
samples have exactly identical fields):

| Field | Type | Description | generic sample value | arknights sample value |
|---|---|---|---|---|
| `spec` | string | Spec version, fixed `launcher-skin-v1` | same as left | same as left |
| `name` | string | Skin identifier (= directory name, the value of the `skin` key in launcher.json) | `generic` | `arknights` |
| `title` | string | Display name (settings-page dropdown) | `Simple Console` | `Sample Skin` (sanitised sample value — see the note below) |
| `entry` | string | Entry page, relative to the skin directory | `../../index.html` | `index.html` |
| `palette.bg` | string | Base colour (HEX; the WPF window Background takes this colour as a solid brush) | `#0f1216` | `#0D0F12` |
| `palette.fg` | string | Primary text colour | `#e8eaed` | `#E6E1D4` |
| `palette.accent` | string | Accent colour | `#4da3ff` | `#F5A623` |
| `palette.muted` | string | Secondary text colour | `#8a919c` | `#8A93A0` |
| `statePath` | string | Runtime state.json, relative to the skin directory (informational field — polling goes by the page's own directory) | `../../state.json` | `state.json` |
| `operaPage` | bool | **Whether the skin brings its own "Operators" page** (optional, defaults to false). true = both the top bar's "Operators" entry and `cmd:"operators"` open the skin's own operator view (pushes `page:"opera"`, rendered by the skin); false/default = the framework's native operator page is the fallback. A skin-drawn operator UI **takes precedence over** the framework's | default (false) | `true` |
| `author` | string | Author (may be empty) | `""` | `""` |
| `license` | string | License identifier | `MIT` | `MIT` |
| `note` | string | Note (may be empty) | built-in default skin description | points at this document |

Readers: `Get-SkinManifest` (reads no-BOM UTF8; on parse failure or a missing file it falls back to the built-in
default hash); `Get-SkinDir` returns the active skin directory; `Get-SkinEntryUrl` returns the entry-page navigation
URL (`https://app.local/index.html` or `https://app.local/skins/<name>/index.html`).

> Note (sample-value convention): in this document the right-hand column (the arknights sample) is always written as
> **sanitised sample values** (`Sample Skin` / `Sample Character` / `EXAMPLE-UNI` and so on).
> That sample skin is a replica of a third-party game UI: gitignored and **not distributed with any public release**;
> the real field values are whatever the gitignored manifest on your machine says.

## 3. state.json data contract

Push-Web is the only writer: it rewrites the file wholesale (`WriteAllText`, non-atomic; on the polling side a JSON
parse failure silently skips that frame and recovers naturally 3 s later; UTF-8 without BOM) to `state.json` under the
**active skin directory**, roughly every 3 seconds (plus immediately after an operation step);
the page script polls **page-relative** `state.json?t=<millisecond timestamp>` once **per second**
(`?t=` busts the cache and cannot be omitted).

Full field set (a single-record JSON example follows):

| Field | Type | Description |
|---|---|---|
| `__push` | bool | Always `true`; push marker (identical in shape to the PS-side `PostWebMessageAsString`, so a page can filter uniformly on `__push`) |
| `stamp` | int | Incremented on every push (diagnostics) |
| `pid` | int | Launcher process PID (a changed pid = two instances) |
| `time` | string | Launcher local time `yyyy/MM/dd HH:mm` |
| `scene` | string | Current scene (life_state.json) |
| `doing` | string | What it is doing right now (verbatim, including the 【】 segment) |
| `dialog` | string | Home dialogue line = `doing` with the `【…】` part removed |
| `mood` | string | Mood |
| `plan` | string | Today's plan |
| `wake` | string | Next heartbeat description (e.g. `next heartbeat: in about 12 minutes`) |
| `name` | string | Owner nickname (read from the bot config at runtime; sample `@owner`) |
| `star` | string | Current persona star rating (`★`×n) |
| `idtext` | string | Current persona identifier (`<card name> · <faction>`; `Doctor · local instance` when there is no persona) |
| `portrait` | string | Current persona portrait URL (`https://cards.local/<file name>`; empty string if none) |
| `page` | string | Page command: `home`/`opera`/`detail` (pushed back from web actions); `cfg`/`log`/`state` are native WPF pages, invisible to web |
| `op` | object | `{ active: bool, text: string }` — operation-in-progress flag (drives the overlay + banner) |
| `svc` | object | Four service lamps `{ engine, embed, napcat, bot }`, four bools (true = running; in real code `Launcher.ps1` Push-Web writes this around :1116-1120 (`Test-Engine`/`Test-Embedding`/`Test-NapCat`/`Test-Bot`), and the consumer `web/app.js` renders the lamps one by one around :159-163; with an older state.json that lacks this field the page keeps the lamps grey) |
| `cards` | array | Persona card payload (see the table below; cards with no usable portrait are filtered out) |

Single `cards` record (produced by `Get-CardsPayload`):

| Field | Type | Description |
|---|---|---|
| `key` | string | Card key = personas file basename (e.g. `example`) |
| `name` | string | Display name |
| `cls` | string | Class |
| `uni` | string | Faction / franchise |
| `desc` | string | One-line blurb |
| `starText` | string | Star text (`★`×n — note: **not** the `star` value) |
| `img` | string | Half-body close-up `https://cards.local/bust4w/<key>.webp` |
| `big` | string | Full-body portrait `https://cards.local/fullw/<key>.webp` |
| `rar` | string | Star rarity icon `https://app.local/skins/<skin>/ui/ark/rarity_<n>.png`; **skin-aware**: an empty string when the active skin is generic (the front end checks for empty and skips rendering — no broken image; skins such as arknights get the full URL) |

Example (excerpt):

```json
{"__push":true,"stamp":42,"pid":12345,"time":"2026/09/10 21:00","scene":"office",
 "doing":"handling reports at the desk【work】","dialog":"handling reports at the desk","mood":"gentle","plan":"—",
 "wake":"next heartbeat: in about 12 minutes","name":"@owner","star":"★★★★★★","idtext":"Sample Character · EXAMPLE-UNI",
 "portrait":"https://cards.local/example_home.png","page":"home",
 "op":{"active":false,"text":""},
 "svc":{"engine":true,"embed":true,"napcat":true,"bot":true},
 "cards":[{"key":"example","name":"Sample Character","cls":"Caster","uni":"EXAMPLE-UNI","desc":"leader of a sample organisation · gentle and firm",
           "starText":"★★★★★★","img":"https://cards.local/bust4w/example.webp",
           "big":"https://cards.local/fullw/example.webp",
           "rar":""}]}
```

<!-- translator note: the sample record's prose values (`scene`, `doing`, `dialog`, `mood`, `wake`, `name`, `cls`, `desc`) are runtime data, not identifiers; they are shown translated so this file contains no CJK. On a Chinese-configured machine those strings arrive in Chinese — as does `doing`'s 【】 segment. -->

## 4. PS↔web command channel

### 4.1 JS → PS (postMessage, the only reliable command channel)

```js
window.chrome.webview.postMessage({ cmd: '<cmd>', data: '<string payload>' });
```

On the PS side `WebMessageReceived` converts the message to JSON, then takes `[string]$m.cmd` and `[string]$m.data` and
hands them to `Handle-WebCmd`.
**`data` is always a string** (the setpersona card key goes here — do not nest an object).
The fallback URL channel (`https://…?cmd=<cmd>&d=<urlencoded>`, parsed in NavigationStarting/SourceChanged) is used
only by the ark sample; new skins need not depend on it.

The full cmd set (real `Handle-WebCmd` code):

| cmd | data payload | Behaviour |
|---|---|---|
| `start` | empty | Start everything (engine → memory → NapCat → bot step machine), overlay banner `⚠ (starting everything…)` |
| `stop` | empty | Stop everything (including the LLM, releasing VRAM) |
| `stopbot` | empty | Stop the bot only (LLM stays up) |
| `exit` | empty | Stop everything, then close the window and exit (window closed = `Exit(0)`) |
| `opera` | empty | Switch back to the home web view and push `page:"opera"` (**left to the skin** to handle: e.g. scroll to the persona section; a skin must not draw a second persona surface — see §9) |
| `operators` | empty | Open "Operators": with a skin-provided operator page (manifest `operaPage: true`) it opens the **skin's own** view (equivalent to `opera`), otherwise the framework's native page is the fallback (§9) |
| `deps` | empty | Open the framework's native **Components page** (capability detection / dependency matrix / one-click download) |
| `about` | empty | Open the framework's native **About page** (framework / current skin / provenance, three sections, including dual attribution) |
| `cfg` | empty | Open the native WPF settings page (the web view is covered entirely) |
| `log` | empty | Open the native WPF log page |
| `state` | empty | Clear the probe cache and open the native WPF runtime status page |
| `home` | empty | Switch back to the home web view and push `page:"home"` |
| `setpersona` | **card key string** | POST `http://127.0.0.1:8080/launcher/persona`, body `{"name":"<card key>","uid":"<owner QQ>"}`; the bot switches persona live; if the channel is down, the fallback writes `persona_select.json` (takes effect on bot restart) |

There are also reserved words: `diag` (drops the whole message JSON into web_diag.log) and `hb` (heartbeat no-op) —
a skin has no need for either.

### 4.2 PS → JS (two channels)

1. **state.json polling** (the main channel, see §3, every second);
2. **Immediate push**: `PostWebMessageAsString('{"__push":true,"dialog":"…"}')` (instant feedback for operation hints) and
   direct calls `ExecuteScriptAsync("showWebBlock('<text>')") / ExecuteScriptAsync("hideWebBlock()")`.
   Therefore **a skin must provide globals of the same names**:

```js
window.showWebBlock = function (text) { /* full-screen overlay + ⚠ banner; display text with the ⚠/bracket decoration stripped; use the default copy for an empty value */ };
window.hideWebBlock = function () { /* retract both the overlay and the banner */ };
```

`op.active` polling and the direct PS call are redundant with each other (whichever arrives first takes effect); the
banner-text decoration (`⚠（…）`) is stripped by the skin itself, following the regex handling in `web/app.js`.

### 4.3 Handling the page field on the skin side

Only the three values `home`/`opera`/`detail` ever show in the web view (the rest are native WPF pages, covering the
web view entirely); a `page` change applies once, to avoid jitter from replayed pushes.

**What to do when you receive `opera`**: a skin that **draws its own operator roster** maps it to its own view
(arknights: `#view-opera`, with `operaPage: true` written in the manifest); a skin that **does not** hands it to the
framework's fallback page (`postMessage({cmd:'operators'})` — this is what the generic sample does). Both forms are
correct — §9 only requires "don't lay out two interfaces for the same batch of data", it does not require the
ownership to belong to the framework.

## 5. Virtual host table (WebView2 SetVirtualHostNameToFolderMapping)

| Host | Directory (after Phase 2) | Purpose |
|---|---|---|
| `app.local` | `E:\robot\launcher\web` | Skin pages + the skins subtree + gal.css/gal.js (loaded as the gal page fallback) |
| `cards.local` | `E:\robot\launcher\cards` | Persona portraits (`bust4w/`, `fullw/`, `<key>_home.png` etc.; gitignored assets) |
| `ui.local` | `E:\robot\launcher\web\skins\arknights\ui` | **Backward compatibility**: the original `launcher\ui` moved along with the skin pack, and repointing it to the new location keeps ark's existing `ui.local/ark/…` references unchanged; new skins always go through `app.local/skins/<skin>/ui/…` (the rarity icons already switched) |

## 6. Card data source: data/cards.json

`Get-Cards` reads in this order: `data/cards.json` exists and parses → use it; otherwise the built-in table
`$CARDS_FALLBACK` (17 entries kept as-is), and on first run it exports an initial `data/cards.json` from the built-in
table (UTF-8 without BOM, `ConvertTo-Json -Depth 4`).

Format: a JSON array whose elements have exactly six fields —

```json
[
  { "key": "example", "name": "Sample Character", "cls": "Caster", "star": 6, "uni": "EXAMPLE-UNI", "desc": "leader of a sample organisation · gentle and firm" }
]
```

| Field | Type | Description |
|---|---|---|
| `key` | string | Card key (personas file basename; the portrait file names are derived from it too) |
| `name` | string | Display name |
| `cls` | string | Class |
| `star` | int | Star rating 1-6 (`starText`/`rar` icons are derived from it) |
| `uni` | string | Faction / franchise |
| `desc` | string | One-line blurb |

**New cards need no recompile**: only the fallback table is compiled into the exe; at runtime cards follow
`data/cards.json` — to add a persona card you only need to
① append one JSON record following the table above; ② drop in the portrait assets such as
`launcher/cards/<key>_full.png` (full body) (see launcher/README §9);
③ restart the launcher (the web card grid and the WPF operator page share the same `Get-Cards` source).

## 7. GAL page skin hooks (reserved interface)

The asset slot `ASSETS` at the top of gal.js (backgrounds / portrait baseURL / scene→background mapping etc.) can be
overridden by a skin: **before gal.js loads**, define

```js
window.GAL_SKIN = { assets: { bgDefault: 'https://app.local/skins/<skin>/img/x.png', /* …same shape as ASSETS… */ } };
```

gal.js executes this as soon as it loads (already implemented in Phase 2):

```js
if (window.GAL_SKIN && window.GAL_SKIN.assets) { Object.assign(ASSETS, window.GAL_SKIN.assets); }
```

When `GAL_SKIN` is undefined it uses the built-in defaults (Arknights backgrounds, with an `onerror` gradient fallback
for the images). Phase 2 only establishes the convention; there is no built-in caller.

Two clarifications on the current state (honestly):

- **Override order relative to `/gal/content.json`**: gal.js runs the `Object.assign` of `GAL_SKIN.assets` as soon as it
  loads, whereas `/gal/content.json` is fetched asynchronously after startup and therefore **arrives later and
  overrides** — the three keys `quotesByCard` / `quoteFallback` / `spriteMap` carried by content.json override the skin
  values (those three keys only; the remaining asset keys such as backgrounds are never fetched, so skin values for them
  are unaffected). **The three keys land in different places (correction 2026-09-12)**: `quotesByCard` and `spriteMap`
  live right on `ASSETS`, so the `Object.assign` takes effect; `quoteFallback` is read into a module variable (in
  `rollQuote`: `ASSETS.quotesByCard[key] || QUOTES_FALLBACK`), so writing it used to be **silently ineffective** — it is
  now adopted on a line of its own (when `GAL_SKIN.assets.quoteFallback` is an array it is assigned to that variable).
  A skin that wants to take over these three keys needs to know this order; there is currently no exemption mechanism.
- **Current state of the channel by which a skin injects `GAL_SKIN` into the GAL page: there is no built-in channel**.
  gal.html is served by the bot (`http://127.0.0.1:8080/gal`), it does not read the skin manifest, and there is no
  skin → page `<script>` injection mechanism; `GAL_SKIN` is for now a **reserved convention** (a host with WebView2
  local-page script injection capability can define its own). When the bot-serve side will provide a skin wizard channel
  is a separate future project.

## 8. The Arknights sample skin

On this machine `launcher/web/skins/arknights/` is the **sample skin** laid out per this specification
(.gitignore, **not included in public releases**):
it contains the upstream arknights-ui SPA (index/detail/opera.html, css/, js/, bridge.js, zz_autorun.js),
the upstream LICENSE/CNAME/.gitattributes/README/screenshot, the `img/` scene images, and `ui/`
(the former `launcher/ui/ark/*` rarity icons and top-bar images + the `home_bg.png` window background + two template jpgs).

Minimal steps for your own skin:

1. Create `launcher/web/skins/<name>/` and write `manifest.json` (the §2 field set, not a character out of place);
2. Implement the §3/§4 contracts in your pages: poll the same-directory `state.json?t=` once per second, provide
   `window.showWebBlock/hideWebBlock`, and send commands with `postMessage({cmd,data})` (for reference the sample uses
   `web/app.js`, roughly 230 lines of vanilla JS);
3. Set the `skin` key in `data/launcher.json` to `<name>`, or switch via the launcher's "Settings → Skin" dropdown
   (saving re-navigates immediately);
4. When the skin directory is missing the launcher falls back to generic automatically (leaving a trace in boot_err.log)
   — no white screen.

## 9. Layering: who owns what (the boundary between skin and framework)

**This section is specification, not style advice.** A user's real-world report on 2026-09-12: "the top buttons and the
home-page buttons are duplicated, and a differently-looking operator page was added" — both symptoms share one root
cause: **the framework and the skin were both implementing the same thing**.

### 9.1 Division of labour

| Layer | Owns | Does not do |
|---|---|---|
| **Framework top bar** (persistent, skin-independent) | The only navigation (Home / Operators / Settings / Content packs / Runtime status / Log / Components / About) + global actions (▶ Start / ■ Stop / ✕ Exit) + four service lamps + clock | Does not decide how a skin looks; **does not override a page a skin built itself** |
| **Framework second-level pages** (native WPF) | Settings / Content packs (four categories + detail pane) / Runtime status (service lamps + heartbeat timeline + stop-bot-only) / Log / Components / About; the operator page is a **fallback only when the skin does not provide one** | Does not take over a view the skin already provides |
| **Skin home page** (WebView2) | Read-only ambience and overview (portrait / scene / mood / plan / dialogue / service lamps) + its own views (e.g. a self-drawn operator roster, a GAL page) + entry points that **navigate** to framework pages (`postMessage({cmd})`) | Does **not duplicate** the framework's global actions (start/stop/exit); one thing is not done twice (either do it yourself or hand it over to the framework entirely) |

### 9.2 Three hard rules

1. **One action has exactly one implementation.** Global actions such as start / stop / exit are "first-class citizens"
   in the top bar; putting another button of the same name in a skin = two entry points whose states will eventually
   disagree (one spinning, the other still saying "start"), and whether "it works" then changes when you switch or
   delete a skin — which is exactly why the top bar exists (`启动器UI规划.md` §1 P1). <!-- translator note: `启动器UI规划.md` — "Launcher UI plan", a Chinese planning document with no English version; the filename is kept verbatim. -->
2. **The same batch of data does not appear in two interfaces, but the skin decides which one is used**:
   if a skin brings its own view (e.g. an operator page) → **the skin's is the real one** (manifest `operaPage: true`,
   and both the top bar's "Operators" entry and `cmd:"operators"` open it); if the skin does not provide one → the
   framework's native page is the fallback.
   Conversely: **if the skin provides it, do not show the user the framework's version as well** — two
   differently-looking "operator pages" are precisely what the user reported.
3. **Framework capabilities do not depend on a skin to exist.** Any feature that exists "only in some skin" is a defect:
   `stopbot` (stop the bot only) was originally like that, and has since been added to the framework's "Runtime status"
   page (`BtnStopBotOnly`).
   ⚠️ This does not conflict with rule 2: rule 2 is about **ownership of views/pages**, rule 3 is about **a feature not
   being allowed to have a skin entry point as its only entry point**.

### 9.3 Why it is worth adding a channel instead of drawing one set each

The three cmds `operators` / `deps` / `about` used to be gaps (the top bar could click them, but a skin sending the name
was **silently ineffective**): if a skin wanted to do anything it could only draw another set itself, and that second set
would inevitably drift away from the framework's. Adding the channel costs 3 lines of dispatch + two lines of docs; not
adding it costs **every skin author**, over and over.

> **Correction (2026-09-12, the same day)**: when I first added the channels I had the direction backwards — I wrote
> "keep only the framework's copy on the data side; a skin may only read and navigate", and on that basis I changed the
> entry point of the Arknights skin's self-drawn operator page to open the framework's native page instead.
> The user's operator page is **a UI he designed himself**, and having the framework page override it was wrong (the
> user pointed it out on the spot).
> The rule is now "**if the skin brings one, use the skin's; the framework page is the fallback**", and neither skin
> needs a patch for this.
> The lesson in one sentence: **layering exists to eliminate duplication, not to unify ownership** — judging "which one
> to delete" means looking at whose work it is and who is using it.

### 9.4 What a skin is allowed to do (compliance list)

- Read-only display of any `state.json` field (§3);
- Use `cmd` to open any framework page (the full set in §4.1, including `operators` / `deps` / `about`);
- Present its own ambient assets, motion and layout; **draw its own views** (declare them in the manifest and the framework will prefer them);
- Put entry buttons such as "Operators" on its home page: if it has its own page, link to its own page; if not, use
  `cmd:"operators"` and let the framework provide the fallback.
