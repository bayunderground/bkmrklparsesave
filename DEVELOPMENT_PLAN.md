# DEVELOPMENT_PLAN.md — bkmrklparsesave

Companion to `CLAUDE.md` (standing rules). This file: architecture review, contracts, phases, tests.
Mark checkboxes as work is completed.

## 0. Assumptions (change here if wrong)

| Item | Default |
|---|---|
| Project slug | `bkmrklparsesave` (service name, NGINX site name, userscript name, message prefix) |
| Install dir | chosen by `DEPLOY_ENV`: `dev` → `/var/www/mini/bkmrklparsesave`, `live` → `/root/bkmrklparsesave` (`APP_DIR` overrides). `DEPLOY_ENV` has no default; the installer aborts if it is unset, so it can't install to the wrong place. |
| Python app port (127.0.0.1) | `8765` |
| NGINX public port | `8088` (installer aborts if busy) |
| NGINX bind address | `0.0.0.0` by default, overridable with `NGINX_BIND` (e.g. LAN/VPN IP) |
| Data dir | `$APP_DIR/data`, files `0644`, dir `0755` |
| Service user | `root` (default). `/root` is mode 700, so a dedicated unprivileged user could not read the app there. `SERVICE_USER` overrides (fine for the dev path if you prefer e.g. `www-data`). |
| Browsers | Firefox and Chromium-based (Chrome/Edge/Brave/Vivaldi), desktop, with Tampermonkey |
| Server address in userscript | one `CONFIG.server` constant, e.g. `http://192.168.1.10:8088`, with matching `@connect` line |

## 1. Architecture review: technical problems and how the design handles them

None of these is a blocker; the architecture stands as specified.

| # | Problem | Decision |
|---|---|---|
| 1 | **Bookmarklet runs in the page context; the userscript runs in Tampermonkey's sandbox** (required for `GM_xmlhttpRequest`). They share the DOM and `window` events but not JS variables. | Bridge via `window.postMessage` with **string** payloads. Message events cross the page/sandbox boundary reliably in Firefox and Chromium. The userscript checks `event.source === window` and `event.data === 'bkmrklparsesave:run'`. |
| 2 | Passing objects (e.g. `CustomEvent.detail`) from page to sandbox can fail on Firefox because of cross-context wrappers. | Strings only. The bookmarklet needs no parameters, because the userscript picks the rule by URL. |
| 3 | **Silent failure** if Tampermonkey is missing/disabled/not allowed to run scripts. | Userscript replies `'bkmrklparsesave:ack'`; bookmarklet alerts after ~1 s without an ack, listing likely causes. |
| 4 | **Chrome 138+/Edge: Tampermonkey needs the per-extension "Allow User Scripts" toggle** (or Developer Mode on older versions), otherwise no userscript runs. | Document in README (Chromium install steps) and in the bookmarklet's no-ack alert text. |
| 5 | **CSP vs. `javascript:` URLs.** Chrome ignores page CSP for bookmarklets. Firefox behavior on strict `script-src` pages is inconsistent across reports (long-standing bug, reportedly fixed in some versions). | Bookmarklet is a tiny inline snippet with no external loads, so it is as CSP-safe as possible. As a fallback the userscript registers a **Tampermonkey menu command** ("Extract this page") that runs the same code path. Document as a known limitation. |
| 6 | `GM_xmlhttpRequest` to an `http://` server from an `https://` page. | Works: the request is made by the extension, not the page, so no mixed-content or CORS rules apply. Requires `@connect <server-host>`; Tampermonkey also asks for permission on first use. |
| 7 | Userscript must match every site (`@match http(s)://*/*`) so the bookmarklet works everywhere, yet must do nothing on load. | Only registers listeners at load. `@noframes` so iframes don't answer. `@run-at document-idle`. |
| 8 | Page CSS can wreck the preview, and page JS could read it. | Preview built in a closed **Shadow DOM** overlay; high z-index; no page-visible globals. |
| 9 | Dynamically loaded content (infinite scroll, SPAs) may not be in the DOM at click time. | Extraction runs on the DOM as it is when clicked. Rules may use a function `items` for custom logic. Document: scroll/expand first, then click. |
| 10 | Duplicate saves from double clicks/timeouts. | Disable Save All after the first click; no auto-retry; server dedupe makes manual re-save harmless. On timeout, the UI says the write may or may not have happened, and that re-saving is safe. |
| 11 | Concurrent requests corrupting the file. | Single server process, global `threading.Lock` around read-ids → filter → append → flush/fsync. |
| 12 | User text forging `item-id` markers or breaking section structure. | See Markdown rules (§2.5): values always indented under a list item; `<!--` neutralized; title is a single sanitized line; marker ID is a fixed-format hex digest. |
| 13 | **Install paths.** `/root/...` is unreadable to non-root users; `/var/www/mini/...` may be a static web root of another NGINX site, which could make `data/*.md` and `server.py` downloadable. | Service runs as root by default (hardened as above; it only listens on 127.0.0.1 and writes only to `DATA_DIR`). Our NGINX site only proxies to the Python server and never serves files from disk. The installer checks existing NGINX configs for a `root`/`alias` pointing at or above `APP_DIR` and prints a warning (does not edit them). |
| 14 | HTTP is unencrypted and unauthenticated. | Keep per request. Python binds to 127.0.0.1; README explains `NGINX_BIND` and firewall use; never expose to the internet. |

## 2. Contracts

### 2.1 Bridge protocol

```text
page (bookmarklet)  --postMessage('bkmrklparsesave:run','*')-->  userscript (listener on window)
page (bookmarklet)  <--postMessage('bkmrklparsesave:ack','*')--  userscript, sent immediately on receipt
```

Bookmarklet (single line in `bookmarklet.txt`, shown readable here):

```js
javascript:(function(){
  var t=setTimeout(function(){alert('bkmrklparsesave: userscript did not respond. Check Tampermonkey is enabled (Chrome/Edge: "Allow User Scripts"), and the script is installed.')},1000);
  addEventListener('message',function h(e){if(e.source===window&&e.data==='bkmrklparsesave:ack'){clearTimeout(t);removeEventListener('message',h)}});
  postMessage('bkmrklparsesave:run','*');
})();void 0
```

### 2.2 Userscript header

```js
// ==UserScript==
// @name         bkmrklparsesave extractor
// @namespace    bkmrklparsesave
// @version      1.0
// @description  Bookmarklet-triggered extraction, preview, and save to a Markdown server
// @match        http://*/*
// @match        https://*/*
// @noframes
// @run-at       document-idle
// @grant        GM_xmlhttpRequest
// @grant        GM_registerMenuCommand
// @connect      192.168.1.10   // EDIT: host of your NGINX server (must match CONFIG.server)
// ==/UserScript==
```

File layout inside `extractor.user.js`: `CONFIG` → `RULES START/END` block → helpers → engine → preview UI → transport → boot (listeners only).

### 2.3 Rule format

```js
rules.push({
  name: 'products',                       // required; ^[a-z0-9][a-z0-9_-]{0,63}$; becomes data/products.md
  match: /example\.com\/shop\//,          // required; string (substring of URL), RegExp, or (url, doc) => boolean
  items: '.product-card',                 // required; CSS selector, or (doc) => Element[]
  id: (el) => el.dataset.sku,             // optional; explicit ID (string); empty/missing → fallback chain
  filter: (item, el) => item.Price > 0,   // optional; return false to drop the item (runs after fields)
  fields: {                               // required; insertion order = output order; FIRST field = title + ID fallback
    Title:     '.title',                  // string selector → trimmed textContent of first match
    Price:     (el) => parseFloat(el.querySelector('.price')?.textContent.replace(/[^\d.]/g, '')),
    Available: (el) => !el.querySelector('.sold-out'),   // boolean → "Yes"/"No"
    URL:       { sel: 'a.title', attr: 'href', abs: true },  // attribute; abs → resolved to absolute URL
    Tags:      { sel: '.tag', all: true },                  // all matches → array of trimmed text
    Discount:  (el, item) => item.Price && item.Old ? item.Old - item.Price : null,  // computed; sees earlier fields
  },
});
```

Field value spec is exactly one of: a selector string, `{ sel, attr?, all?, abs? }`, or a function `(el, itemSoFar) => value`.
Allowed result values: string, number, boolean, `null`/`undefined`, or an array of those.
`null`, `undefined`, `''`, `NaN`, and empty arrays mean **missing**: the field is omitted (not an error).
A throwing field function or a selector that matches nothing never aborts extraction: a thrown error is recorded as a warning (rule, item index, field), and the field is treated as missing.
Helper available to rules: `$text(el, sel)`, `$attr(el, sel, name)`, `$all(el, sel)` (small, documented in the spec; no more than these).

Matching: all rules whose `match` is true for `location.href` are candidates. 0 → informative panel (URL + list of rule names/matchers). 1 → run. >1 → simple list of buttons to choose which.

### 2.4 Request/response JSON

`POST /save` (Content-Type: application/json):

```json
{
  "rule": "products",
  "url": "https://example.com/shop/page/2",
  "items": [
    { "id": "SKU-123", "fields": { "Title": "Example Product", "Price": 1250, "Available": true, "Tags": ["a", "b"] } }
  ]
}
```

- `id` optional. `fields` required, non-empty object; values: string, number, boolean, or flat array of those (nulls already removed by the client; the server also drops them).
- `url` optional, informational, not written to the file.

Success `200`: `{ "ok": true, "rule": "products", "file": "products.md", "saved": 3, "skipped": 7 }`
Errors `400/404/405/413/415/500`: `{ "ok": false, "error": "human-readable message" }`
Extra: `GET /health` → `{ "ok": true }` (used by tests/installer).

### 2.5 Duplicate detection and Markdown format (server)

Identifier text, first available:
1. `item.id` if it is a nonempty string after trim.
2. `str(first field value)` if nonempty after trim (arrays: joined with `\n`).
3. Canonical JSON of `fields`: `json.dumps(fields, sort_keys=True, ensure_ascii=False, separators=(",", ":"))`.

Normalize with Unicode NFC + `strip()`. Marker ID = first 16 hex chars of `SHA-256(identifier text as UTF-8)`. Because the marker is a fixed hex digest, no user text can appear in it.

On each request (under the lock): read the target file if present, collect IDs with the regex `^<!-- item-id: ([0-9a-f]{16}) -->$` (multiline), then for each item skip if its marker ID is already known or was already added earlier in the same request; otherwise render and append.

Section layout (every section ends with `---`; sections separated by blank lines; file has no header):

```markdown
## Example Product

<!-- item-id: 3f2a9c1d7b0e4a55 -->

- Price: 1250
- Available: Yes
- Description:
  First line
  second line

  after a blank line
- Tags:
  - a
  - b

---
```

Rendering rules:
- Title = first field's value, first line only, whitespace collapsed, ≤ 100 chars, leading `#` characters stripped, fallback `Item`.
- Booleans → `Yes`/`No`. Numbers via `str` (integers without `.0`). Arrays → nested `  - ` bullets.
- Single-line values stay on the bullet line. Multiline values: `- Label:` then every continuation line indented by 2 spaces (blank lines become two spaces) so `---`, `## ` or a forged marker inside a value can never start a section or sit at column 0.
- Field labels: single line, newlines/`:` handled (replace newline with space).
- In all user text (title, labels, values) replace `<!--` with `&lt;!--` so HTML-comment markers can't be forged.
- Append with `open(path, "a", encoding="utf-8")`, `flush()`, `os.fsync()`. If the file is non-empty and doesn't end with a newline, add one first. Create `data/` if needed.

The README states: the first-field fallback treats different items with identical first-field values as duplicates; use an explicit `id` (or put a unique field first) when that matters.

## 3. Phases

Each phase ends runnable and verified before the next starts.

### Phase 0 — Skeleton
- [x] Repo layout per `CLAUDE.md`; `.gitignore` (`data/`, `__pycache__/`)
- [x] `tests/fixtures/` with `shop.html` (≥5 product cards, one sold out, one with multiline description, Unicode/emoji text, one without price) and `empty.html`

### Phase 1 — `server.py`
- [x] Config via env: `BKMRKLPARSESAVE_HOST` (default `127.0.0.1`), `BKMRKLPARSESAVE_PORT` (`8765`), `BKMRKLPARSESAVE_DATA` (`./data`)
- [x] `ThreadingHTTPServer`; `POST /save`, `GET /health`; others → 404/405
- [x] Validation: content-type JSON, size ≤ 5 MB, valid UTF-8 JSON, structure per §2.4, rule-name regex, path containment check, ≤ 1000 items
- [x] ID computation, dedupe, rendering, append per §2.5 under one global lock
- [x] Clear JSON errors; no tracebacks to clients; one-line stderr log per request (journald picks it up)
- [x] Target: ~150–200 lines

### Phase 2 — Server tests (`tests/test_server.py`, `unittest` + `urllib`, server started on a random port with a temp dir)
- [x] Creates file; appends new; second identical POST → `saved=0, skipped=N`
- [x] Explicit id, first-field fallback, hash fallback (including key-order independence)
- [x] First-field collision case behaves as documented
- [x] Unicode + multiline + values containing `---`, `## x`, `<!-- item-id: 0123456789abcdef -->` → no forged marker, no extra sections
- [x] Invalid rule names (`../x`, `a/b`, `A`, empty, 65 chars, `.hidden`, `x.md`) → 400, nothing written outside the data dir
- [x] Malformed JSON, wrong types, missing fields, empty items, oversized body → 4xx JSON
- [x] Concurrency: 20 threads post overlapping items; final file has each ID exactly once, intact sections

### Phase 3 — Userscript engine (no UI yet)
- [x] Header per §2.2, `CONFIG`, empty RULES block with the sample `products` rule for `tests/fixtures/shop.html`
- [x] Rule matching (string/RegExp/function), selection of candidates
- [x] Item discovery (selector or function), field resolution (selector string / object / function), missing-value handling, `filter`, explicit `id`
- [x] Error collection (rule-level and per-field) returned alongside items
- [x] Output: `{ rule, url, items:[{id?, fields}], errors:[...] }` matching §2.4
- [x] Console-only trigger for development, removed or hidden behind the real trigger by Phase 5

### Phase 4 — Preview UI
- [x] Closed Shadow DOM overlay; fixed header (rule name, item count, error count) and footer; scrollable body
- [x] Toggle JSON ⇄ Markdown (Markdown is a client-side approximation of the server format; label it as such)
- [x] Errors panel at top, visible by default when non-empty
- [x] No-rule panel and multi-rule picker
- [x] Buttons: **Save All**, **Cancel** (and Esc closes); Cancel removes the overlay and transmits nothing
- [x] Usable on pages with hundreds of items (render text in a single `<pre>`, not per-item DOM nodes)

### Phase 5 — Transport and bookmarklet
- [x] `GM_xmlhttpRequest` POST to `CONFIG.server + '/save'`, `Content-Type: application/json`, 15 s timeout, no retry
- [x] Save All disabled after click; result panel shows saved/skipped/file, or the exact error (HTTP status, network error, timeout with the "may have been written; re-saving is safe" note)
- [x] `message` listener (`bkmrklparsesave:run` → ack → start) and `GM_registerMenuCommand('Extract this page')`
- [x] `bookmarklet.txt` with the `javascript:` URL (URL-safe, single line) and install notes for Firefox (bookmarks toolbar → Add bookmark; or drag) and Chromium (bookmark manager → Add new bookmark; paste into URL), including the Chrome/Edge "Allow User Scripts" step and the CSP fallback

### Phase 6 — `extractor-spec.md` (written for an LLM reader)
- [x] Task statement: "Output exactly one `rules.push({...});` block and nothing else"
- [x] Full rule API from §2.3 with the allowed forms, helper functions, and value rules
- [x] 3 worked examples (simple list; computed + filter; conditional/optional fields with explicit `id`)
- [x] Limitations: runs on the live DOM, no network calls, no shadow-DOM/iframe access unless the sample shows it, no pagination
- [x] Hard rules: use only selectors present in the supplied HTML; no invented classes; prefer stable attributes over `nth-child`; first field is title and ID fallback, so put the most identifying field first; add `id` when a stable one exists (e.g. data attribute or URL slug)
- [x] "Uncertainties" section: after the rule, list anything the HTML sample couldn't confirm (missing states, truncated HTML, ambiguous selectors) and ask for more HTML instead of guessing
- [x] A copy-paste prompt template (see §5)

### Phase 7 — NGINX and `install.sh`
- [x] `nginx.conf` template: `listen ${NGINX_BIND}:${NGINX_PORT}; client_max_body_size 6m;` `location / { proxy_pass http://127.0.0.1:${APP_PORT}; ... }` (only `/save` and `/health` need to work; keep it minimal)
- [x] Installer steps: root check → detect Python 3 (install `python3` if missing) → detect NGINX (apt install if missing) → require `DEPLOY_ENV` (`dev`|`live`) and set `APP_DIR` → create `SERVICE_USER` only if it isn't `root` and doesn't exist → copy `server.py` to `APP_DIR` → create data dir with permissions → write systemd unit (`User=$SERVICE_USER`, `Restart=on-failure`, `NoNewPrivileges=yes`, `ProtectSystem=strict`, `ReadWritePaths=$DATA_DIR`; unit name `bkmrklparsesave.service`) → `daemon-reload`, `enable --now` → render NGINX site to `sites-available`, symlink (or `conf.d` fallback), idempotent → port-in-use check → `nginx -t` (revert and abort on failure) → reload → `curl /health` through NGINX → print URL, `@connect` line to put in the userscript, and `systemctl`/`journalctl` commands
- [x] Reruns: overwrite our own unit/site/app file only; never touch other NGINX files; never recreate users/dirs that exist; keep existing data
- [x] All settings overridable: `DEPLOY_ENV`, `APP_DIR`, `DATA_DIR`, `APP_PORT`, `NGINX_PORT`, `NGINX_BIND`, `SERVICE_USER`, `SERVER_NAME` (default `_`)
- [x] `tests/test_nginx.sh`: health via proxy, POST save through proxy, duplicate skip through proxy

### Phase 8 — `README.md`
- [x] What it is (5 lines), install, Tampermonkey setup (including Chrome/Edge "Allow User Scripts"), bookmarklet install for Firefox and Chromium, first run walkthrough with the sample rule
- [x] Adding a rule with an LLM (workflow + prompt example)
- [x] Duplicate rules and the first-field caveat
- [x] Limitations: dynamic content, iframes, CSP/Firefox, `@connect` needs editing when the server address changes, `javascript:` bookmarklets disabled in some locked-down browsers/policies
- [x] Security note: HTTP unencrypted, no auth, don't expose to the internet, use `NGINX_BIND`/firewall/VPN
- [x] Troubleshooting table: no ack alert, no matching rule, connect-permission prompt, server unreachable, nginx port clash

### Phase 9 — End-to-end verification (manual, recorded in `tests/MANUAL.md`)
Serve fixtures: `python3 -m http.server 8000 --directory tests/fixtures` and add a rule matching `localhost:8000/shop.html`.
- [ ] Bookmarklet in Firefox and in a Chromium browser: preview appears, ack received
- [ ] Bookmarklet with Tampermonkey disabled: alert appears after ~1 s
- [ ] No rule matches (`empty.html`): informative message, nothing sent
- [ ] Two rules match: picker works
- [ ] Preview JSON/Markdown toggle; item with missing field; failing field function shows an error but extraction continues
- [ ] Cancel and Esc: server access log shows no request
- [ ] Save All: file created; second click on the same page after reopening → all skipped
- [ ] Unicode/emoji and multiline text intact in the `.md` file
- [ ] Through NGINX on a different machine on the LAN, with the correct `@connect` host
- [ ] Menu-command fallback works

## 4. Requirement → test coverage

| Required test | Where |
|---|---|
| Multiple items from one page | Phase 9 (shop.html, ≥5 cards) |
| CSS selectors and custom JS transforms | Phase 9 (sample rule uses both) |
| Preview and cancellation | Phase 9 |
| Sending data through Tampermonkey | Phase 9 |
| Creating Markdown files / appending | Phase 2 |
| Skipping duplicates across repeated submissions | Phase 2, Phase 9 |
| First-field and hash ID fallbacks | Phase 2 |
| Unicode and multiline text | Phase 2, Phase 9 |
| Invalid rule names | Phase 2 |
| Malformed JSON | Phase 2 |
| Concurrent save requests | Phase 2 |
| NGINX reverse proxy operation | Phase 7 (`test_nginx.sh`), Phase 9 |

## 5. Example: asking an LLM for a new rule

```text
Attached: extractor-spec.md (the rule API) and a sample of the page HTML.

Page URL: https://example.com/shop/page/2
Task: Extract every product card. Fields: title, price as a number, whether it is
in stock, absolute product URL, and the tags. Skip items without a price.
Use the SKU from the data-sku attribute as the explicit id.
Rule name: products

Follow extractor-spec.md exactly. Output only one rules.push({...}) block, then a
short "Uncertainties" list. Do not invent selectors that are not in the HTML.
```

## 6. Definition of done

- [ ] All deliverables from `CLAUDE.md` exist and match the contracts above
- [ ] `python3 -m unittest discover -s tests` passes
- [ ] `install.sh` works on a clean Ubuntu/Debian host and again on rerun, with an unrelated NGINX site still working
- [ ] Phase 9 checklist fully ticked
- [ ] No forbidden dependencies; `server.py` imports only the standard library
- [ ] README states the HTTP/no-auth warning
