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
