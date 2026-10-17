# bkmrklparsesave — browser-to-Markdown extractor

Click one bookmark on any web page; a Tampermonkey userscript extracts items
with site-specific rules, shows a preview, and — only after you click
**Save All** — POSTs JSON to a small Python server that appends new items as
Markdown sections. No database, no accounts, stdlib Python only.

- `extractor.user.js` — the one userscript (rules + engine + preview + save)
- `bookmarklet.txt` — one reusable `javascript:` URL for every site
- `extractor-spec.md` — rule-writing spec, written for an LLM
- `server.py` / `nginx.conf` / `install.sh` — the save endpoint behind NGINX

## Install (server)

Ubuntu/Debian, from a checkout:

```bash
sudo DEPLOY_ENV=dev  bash install.sh   # dev  -> /var/www/mini/bkmrklparsesave
sudo DEPLOY_ENV=live bash install.sh   # live -> /root/bkmrklparsesave
```

`DEPLOY_ENV` is required on purpose — the installer refuses to guess where to
install. Rerunning is safe: it overwrites only its own files (site config,
systemd unit, `server.py`), keeps your data, and never touches other NGINX
sites. All settings can be overridden per run:

```bash
sudo DEPLOY_ENV=dev NGINX_PORT=9090 NGINX_BIND=192.168.1.10 bash install.sh
# also: APP_DIR, DATA_DIR, APP_PORT, SERVICE_USER, SERVER_NAME
```

The installer prints the URL and the exact `@connect` line to put into the
userscript when it finishes. Check the service with
`systemctl status bkmrklparsesave` and `journalctl -u bkmrklparsesave -f`.

## Browser setup (Tampermonkey)

1. Install Tampermonkey (Firefox and Chromium add-on stores).
2. Import `extractor.user.js` (drag the file onto the browser, or create a new
   script in the Tampermonkey dashboard and paste the file's contents).
3. Edit two lines in the script so they match your server:
   - `CONFIG.server = 'http://<host>:<port>'`
   - `// @connect <host>` (same host, no scheme/port)
   Then save.
4. **Chrome/Edge 138+ and newer:** open the Tampermonkey extension details and
   turn ON **"Allow User Scripts"** (older versions: enable *Developer Mode*
   under `chrome://extensions`). Without it, *no* userscript runs at all.

## Install the bookmarklet

Copy the single `javascript:` line from `bookmarklet.txt`, then:

- **Firefox:** right-click the bookmarks toolbar → *Bookmark This Page…* →
  paste the line into the URL field. Or drag the line onto the toolbar.
- **Chrome/Edge/Brave/Vivaldi:** bookmark manager → *Add new bookmark* →
  paste the line into the URL field.

One bookmarklet works on every site — the userscript picks the rule by URL.

## First run with the sample rule

```bash
python3 -m http.server 8000 --directory tests/fixtures   # serves shop.html
```

Open `http://localhost:8000/shop.html`, click the bookmark. The sample
`products` rule matches the 6 product cards and drops the one without a price
via its `filter`, so the preview lists 5 items. Toggle **JSON ⇄ Markdown**,
click **Save All**; the file is `data/products.md`. Click **Save All** again:
everything is skipped — duplicates are detected by ID, never by timestamps.

**Cancel** or `Esc` closes the preview and transmits nothing.

## Adding a rule with an LLM

Rules live in one block in `extractor.user.js` (between `// === RULES START ===`
and `// === RULES END ===`). The engine is never touched — only rules are
pasted in.

Give an LLM three things: **`extractor-spec.md`** (the API), **a sample of the
page HTML** (save it from the browser: right-click → *Save Page As*, or copy
the relevant cards' outerHTML), and a description of the fields. Example
prompt:

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

Paste the returned block into the RULES block, reload the userscript in the
Tampermonkey dashboard, and try it on the page. Never accept a rule that uses
classes not present in your sample — that is the LLM's most common failure.

## Duplicates and the first-field caveat

Duplicate detection is scoped to one rule's file and uses, in order:

1. the explicit `id` you extract (recommended: SKU, ISBN, `data-*`, slug),
2. otherwise the **first field's value**,
3. otherwise a SHA-256 hash of the fields.

Consequence: two *different* items whose first-field values are identical are
treated as duplicates — the second one is silently skipped. Fix: extract a
stable `id`, or put a genuinely unique field first. Timestamps are never used,
so re-running a page is always safe.

## Limitations

- **Dynamic content:** extraction runs on the DOM as it is when you click.
  Infinite scroll, "load more", and SPAs must be expanded first, then click.
- **Iframes and shadow DOM** are not reachable; if the items live inside one,
  the rule cannot see them.
- **CSP vs bookmarklets:** Chrome ignores page CSP for `javascript:` URLs;
  some Firefox pages still refuse them. Fallback: Tampermonkey menu →
  **"Extract this page"** (same code path).
- **`@connect` must match the server host:** if the server address changes,
  edit `CONFIG.server` and the `@connect` header line together.
- **Locked-down browsers/policies** may disable `javascript:` bookmarklets or
  user scripts entirely; nothing can work then.
- **Chromium "Allow User Scripts":** required since Chrome/Edge 138 (see
  above); if the userscript never answers, this is the first thing to check.

## Security — read this

**HTTP is unencrypted and the endpoint has no authentication.** Anyone who can
reach `NGINX_BIND:NGINX_PORT` can write to your Markdown files. The Python
server binds to `127.0.0.1` only; NGINX is the public door.

- **Never expose this to the open internet.** No auth system exists, by
  design, and none will be added.
- Keep `NGINX_BIND=0.0.0.0` only on a trusted LAN, or bind it to a VPN/LAN IP
  (`NGINX_BIND=192.168.1.10`), and/or firewall the port.
- HTTPS is not part of this project: traffic between your browser and the
  server is plain HTTP.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Alert: "userscript did not respond" after ~1 s | Tampermonkey missing/disabled, user scripts not allowed, or script not installed | Check Tampermonkey is enabled; Chrome/Edge: turn ON "Allow User Scripts" in the extension details; confirm the script is installed and enabled |
| No alert at all, nothing happens | Bookmarklet URL mangled (line broken when copied) | Re-copy the single line from `bookmarklet.txt` |
| Preview says "No matching rule" | No rule's `match` fits this URL | Check the listed rules; fix/add a rule for this site |
| Menu fallback also does nothing | Script not running on this site | Verify `@match http://*/*` lines are intact in the userscript |
| Tampermonkey asks for permission to connect | First request to a new host | Allow it; ensure `@connect <host>` matches `CONFIG.server` |
| Save shows "Network error" | Server/NGINX down, wrong host/port, firewall | `curl http://<host>:<port>/health`; `systemctl status bkmrklparsesave`; check `CONFIG.server` and `@connect` |
| Save shows "Timeout … may or may not have happened" | Slow or unreachable server within 15 s | Re-saving is safe (duplicates skipped); check service logs |
| Installer aborts: port already in use | Another service/site on `NGINX_PORT` | Pick a free port: `NGINX_PORT=9099 sudo DEPLOY_ENV=dev bash install.sh` |
| Installer aborts: `nginx -t` failed | Rendered config rejected | Our site file was removed automatically and nginx was not reloaded; inspect `nginx -t` output |
| `404` through the proxy is JSON from the server | Unknown path (only `/save` and `/health` exist) | Expected |
