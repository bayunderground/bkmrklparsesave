# CLAUDE.md — bkmrklparsesave (Universal Browser-to-Markdown Extractor)

Personal pet project. A bookmarklet click on any web page triggers a Tampermonkey userscript, which extracts items using site-specific rules, shows a preview, and (only after I click **Save All**) POSTs JSON to a small Python server that appends new items to Markdown files.

Read `DEVELOPMENT_PLAN.md` for the phased plan, contracts, and test matrix. This file holds the standing rules.

## Golden rule: simplicity

This is not a corporate application. When in doubt, choose the shorter, more boring option. Do not add abstractions, config systems, plugin mechanisms, base classes, or "future-proofing". If a feature isn't in the plan, ask before adding it.

**Forbidden:** Docker/Compose, any database (SQLite included), Flask/FastAPI/any Python third-party package, Node.js/npm, bundlers/build steps, multiple userscripts, per-site bookmarklets, authentication/accounts, logging frameworks, scheduled/automatic extraction, browser extensions other than Tampermonkey, HTTPS/certificate management.

**Allowed:** plain JavaScript (no modules, no transpiling), Python 3 standard library only, Bash, NGINX, systemd.

## Deliverables (repo root)

```text
server.py            Python stdlib HTTP server (127.0.0.1 only)
extractor.user.js    The ONLY userscript: config + rules + engine + preview UI + transport
bookmarklet.txt      Single reusable javascript: URL, plus install notes
extractor-spec.md    Rule-writing spec given to an LLM (see "LLM workflow")
nginx.conf           Template for the NGINX site (placeholders filled by install.sh)
install.sh           Idempotent installer for Ubuntu/Debian
README.md            Install, usage, limitations, security note
tests/               Plain-script tests (no test framework)
data/                Markdown output (created at install time, not committed)
```

## Fixed architectural decisions (do not reopen unless technically impossible)

1. **Bookmarklet → userscript bridge:** `window.postMessage` with plain string payloads (`'bkmrklparsesave:run'` out, `'bkmrklparsesave:ack'` back). Strings only — never pass objects across the page/sandbox boundary (Firefox wraps cross-context objects and they can become inaccessible). The bookmarklet waits ~1 s for the ack and alerts if none arrives (Tampermonkey missing/disabled, user scripts not allowed, `@match` mismatch).
2. **Userscript uses `@grant GM_xmlhttpRequest` (and `GM_registerMenuCommand`)**, so it runs in Tampermonkey's sandbox, not the page context. Rule code still has full DOM access. `unsafeWindow` is available as an escape hatch but should be rare.
3. **No work on page load.** The script only registers a `message` listener and a Tampermonkey menu command (fallback trigger when a site's CSP blocks bookmarklets). Extraction starts only on an explicit trigger. Nothing is transmitted without clicking **Save All**.
4. **All rules live in one block** in `extractor.user.js` between `// === RULES START ===` and `// === RULES END ===`. New rules are pasted there; the engine is never touched when adding a rule.
5. **Server listens on 127.0.0.1 only**; NGINX is the reverse proxy. No auth, plain HTTP.
6. **Duplicate ID order:** explicit `id` (nonempty) → first extracted field (nonempty) → SHA-256 of canonical JSON of the item. Scope: one rule's Markdown file. Never timestamps.
7. **One Markdown file per rule name** (`data/<rule>.md`); append-only.

## Code conventions

- Python: single file, `http.server.ThreadingHTTPServer`, one global `threading.Lock` around read-dedupe-append, UTF-8 everywhere, functions short enough to read in one screen. Importable (`if __name__ == "__main__"` guard) so tests can call helpers.
- Rule name validation: `^[a-z0-9][a-z0-9_-]{0,63}$`. Build the path as `DATA_DIR / (name + ".md")` and verify the resolved parent is `DATA_DIR`. Reject everything else with HTTP 400.
- Request body limit 5 MB; item count limit 1000. Malformed input returns JSON `{"ok": false, "error": "..."}` with 4xx, never a stack trace.
- User-controlled text must never be able to forge a `<!-- item-id: ... -->` line or start a new section (see Markdown rules in the plan).
- JavaScript: ES2019-ish, `const`/`let`, no dependencies, no `eval`/`new Function`. Preview UI is built inside a **Shadow DOM** so page CSS can't break it. Every rule and every field runs inside try/catch; errors are collected and shown, never thrown to the console only.
- No automatic retries on save. Re-saving is safe anyway because duplicates are skipped.
- Bash: `set -euo pipefail`, all settings overridable by environment variables with defaults at the top, safe to rerun.
- Comments explain *why*, not *what*. No dead code, no TODO litter.

## NGINX / install rules

- Never overwrite or edit existing NGINX configs. Add only our own file (`/etc/nginx/sites-available/bkmrklparsesave` + symlink in `sites-enabled`, or `conf.d/bkmrklparsesave.conf` if `sites-enabled` doesn't exist).
- Always `nginx -t` before `systemctl reload nginx`; on failure remove our file/symlink and abort without reloading.
- Refuse to proceed if the chosen `NGINX_PORT` is already in use by something that isn't our own site.
- `install.sh` requires `DEPLOY_ENV=dev|live` (no default). Install dirs: dev `/var/www/mini/bkmrklparsesave`, live `/root/bkmrklparsesave`. The service runs as root by default because `/root` is not readable by other users; keep `ProtectSystem=strict`, `NoNewPrivileges=yes`, and 127.0.0.1-only binding.
- Our NGINX site only reverse-proxies; it must never serve files from `APP_DIR` (it may live under another site's web root on dev).
- Rerunning `install.sh` must leave exactly one site config, one systemd unit, no duplicates.

## LLM workflow (what `extractor-spec.md` is for)

I give an LLM: `extractor-spec.md` + sample HTML + a description of the fields/conditions. The LLM returns **one rule object only**, which I paste into the RULES block. The spec must tell the LLM to: output only the rule, use only selectors visible in the HTML, never invent selectors, list uncertainties explicitly (e.g. "no sample of an out-of-stock card"), and prefer stable attributes/classes over positional selectors. Keep the rule format flat and boring so LLM output is reliable.

## Commands

```bash
python3 server.py                      # dev run (defaults: 127.0.0.1:8765, ./data; env `BKMRKLPARSESAVE_HOST|PORT|DATA`)
python3 -m unittest discover -s tests  # server tests (stdlib only)
bash tests/test_nginx.sh               # proxy check, needs installed service
python3 -m http.server 8000 --directory tests/fixtures   # manual browser test pages
sudo DEPLOY_ENV=dev  bash install.sh   # dev server  -> /var/www/mini/bkmrklparsesave
sudo DEPLOY_ENV=live bash install.sh   # live server -> /root/bkmrklparsesave (env vars override other defaults)
```

## Working agreements

- Work through `DEVELOPMENT_PLAN.md` phase by phase; tick checkboxes as items are done; keep each phase runnable before moving on.
- Before each phase, state in 2–3 lines what you will build. After it, say how it was verified.
- If a requirement seems technically impossible or unreliable, stop and explain the problem and the smallest alternative — don't silently work around it.
- Don't create files outside the deliverables list without a reason stated in the commit/summary.
- The README must state plainly that HTTP is unencrypted and the unauthenticated endpoint must never be exposed to the open internet; no auth system is to be added.
