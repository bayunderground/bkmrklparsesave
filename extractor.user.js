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

(function () {
  'use strict';

  // One place to change when the server moves; keep @connect in sync.
  var CONFIG = {
    server: 'http://192.168.1.10:8088', // EDIT: scheme+host+port of the NGINX site
    saveTimeoutMs: 15000
  };

  // === RULES START ===
  var rules = [];

  // Sample rule for tests/fixtures/shop.html (served at localhost:8000).
  rules.push({
    name: 'products',
    match: /localhost:8000\/shop\.html/,
    items: '.product-card',
    id: function (el) { return el.dataset.sku; },
    filter: function (item) { return item.Price > 0; },
    fields: {
      Title: 'a.title',
      Price: function (el) {
        var t = el.querySelector('.price');
        return t ? parseFloat(t.textContent.replace(/[^\d.]/g, '')) : NaN;
      },
      Available: function (el) { return !el.classList.contains('sold-out'); },
      URL: { sel: 'a.title', attr: 'href', abs: true },
      Tags: { sel: '.tag', all: true },
      Description: function (el) {
        var p = el.querySelector('.description');
        return p ? p.textContent.trim() : null;
      }
    }
  });
  // === RULES END ===

  // --- rule helpers (the only three; see extractor-spec.md) ---

  function $text(el, sel) {
    var m = el.querySelector(sel);
    return m ? m.textContent.trim() : null;
  }

  function $attr(el, sel, name) {
    var m = el.querySelector(sel);
    if (!m) return null;
    var v = m.getAttribute(name);
    return v === null ? null : v;
  }

  function $all(el, sel) {
    return Array.prototype.map.call(el.querySelectorAll(sel), function (n) {
      return n.textContent.trim();
    });
  }

  // --- rule matching ---

  function errText(ruleName, index, field, err) {
    var where = ruleName || '?';
    if (index !== null && index !== undefined) where += '[' + index + ']';
    if (field) where += '.' + field;
    return where + ': ' + (err && err.message ? err.message : String(err));
  }

  function ruleMatches(rule, url, doc) {
    var m = rule.match;
    if (typeof m === 'string') return url.indexOf(m) !== -1;
    if (m instanceof RegExp) return m.test(url);
    if (typeof m === 'function') return !!m(url, doc);
    return false;
  }

  function matchingRules(doc, errors) {
    var url = location.href;
    return rules.filter(function (rule) {
      try {
        return ruleMatches(rule, url, doc);
      } catch (err) {
        errors.push(errText(rule.name, null, null, err));
        return false; // a broken matcher must not hide the other rules
      }
    });
  }

  // --- extraction engine ---

  function isMissing(v) {
    if (v === null || v === undefined || v === '') return true;
    if (typeof v === 'number' && isNaN(v)) return true;
    if (Array.isArray(v) && v.length === 0) return true;
    return false;
  }

  function absUrl(v) {
    try { return new URL(v, document.baseURI).href; } catch (err) { return v; }
  }

  function resolveField(spec, el, itemSoFar) {
    if (typeof spec === 'function') return spec(el, itemSoFar);
    if (typeof spec === 'string') {
      var m = el.querySelector(spec);
      return m ? m.textContent.trim() : undefined; // no match = missing, not an error
    }
    if (spec && typeof spec === 'object' && !Array.isArray(spec) &&
        typeof spec.sel === 'string') {
      if (spec.all) {
        var out = [];
        Array.prototype.forEach.call(el.querySelectorAll(spec.sel), function (n) {
          var v = spec.attr ? n.getAttribute(spec.attr) : n.textContent.trim();
          if (v !== null && v !== undefined) out.push(spec.abs ? absUrl(v) : v);
        });
        return out;
      }
      var node = el.querySelector(spec.sel);
      if (!node) return undefined;
      var raw = spec.attr ? node.getAttribute(spec.attr) : node.textContent.trim();
      if (raw === null || raw === undefined) return undefined;
      return spec.abs ? absUrl(raw) : raw;
    }
    throw new Error('unsupported field spec (want selector, {sel}, or function)');
  }

  function normalizeValue(v) {
    if (v !== null && typeof v === 'object' && !Array.isArray(v)) {
      throw new Error('unsupported value type (want string/number/boolean/array)');
    }
    if (Array.isArray(v)) {
      return v.filter(function (x) {
        return x !== null && x !== undefined &&
          !(typeof x === 'number' && isNaN(x)) && x !== '';
      });
    }
    return v;
  }

  function extractItems(rule, doc, errors) {
    if (!rule.fields || Object.keys(rule.fields).length === 0) {
      errors.push(errText(rule.name, null, null, new Error('rule has no fields')));
      return [];
    }
    var els;
    try {
      if (typeof rule.items === 'function') {
        els = rule.items(doc);
      } else if (typeof rule.items === 'string') {
        els = Array.prototype.slice.call(doc.querySelectorAll(rule.items));
      } else {
        throw new Error('items must be a selector or a function');
      }
    } catch (err) {
      errors.push(errText(rule.name, null, null, err));
      return [];
    }
    if (!Array.isArray(els)) {
      errors.push(errText(rule.name, null, null,
        new Error('items function must return an array of elements')));
      return [];
    }

    var items = [];
    els.forEach(function (el, index) {
      try {
        var fields = {};
        Object.keys(rule.fields).forEach(function (name) {
          var value;
          try {
            value = normalizeValue(resolveField(rule.fields[name], el, fields));
          } catch (err) {
            errors.push(errText(rule.name, index, name, err));
            value = undefined;
          }
          if (!isMissing(value)) fields[name] = value;
        });

        if (typeof rule.filter === 'function') {
          var keep;
          try {
            keep = rule.filter(fields, el) !== false;
          } catch (err) {
            errors.push(errText(rule.name, index, 'filter', err));
            keep = true; // unsure? keep the item; the error stays visible
          }
          if (!keep) return;
        }

        if (Object.keys(fields).length === 0) {
          errors.push(errText(rule.name, index, null,
            new Error('no fields extracted, item skipped')));
          return;
        }

        var out = { fields: fields };
        if (typeof rule.id === 'function') {
          try {
            var idValue = rule.id(el);
            if (typeof idValue === 'string' && idValue.trim()) {
              out.id = idValue.trim();
            }
          } catch (err) {
            errors.push(errText(rule.name, index, 'id', err)); // server falls back
          }
        }
        items.push(out);
      } catch (err) {
        errors.push(errText(rule.name, index, null, err));
      }
    });
    return items;
  }

  // --- entry point: the preview UI calls these (Phase 4) ---

  function describeRule(rule) {
    return rule.name + ' — ' + String(rule.match);
  }

  function buildResult(rule, errors) {
    return {
      rule: rule.name,
      url: location.href,
      items: extractItems(rule, document, errors),
      errors: errors
    };
  }

  // kind: 'none' (no rule matches), 'one' (ready), 'many' (picker needed)
  function startExtraction() {
    var errors = [];
    var found = matchingRules(document, errors);
    if (found.length === 1) {
      return { kind: 'one', result: buildResult(found[0], errors), errors: errors };
    }
    if (found.length === 0) {
      return {
        kind: 'none',
        errors: errors,
        url: location.href,
        available: rules.map(describeRule)
      };
    }
    return {
      kind: 'many',
      errors: errors,
      choices: found.map(function (rule) {
        return { rule: rule, label: describeRule(rule) };
      })
    };
  }

  function extractChoice(choice) {
    var errors = [];
    return { kind: 'one', result: buildResult(choice.rule, errors), errors: errors };
  }

  // --- preview UI (closed Shadow DOM: page CSS cannot reach it) ---

  var preview = null; // { host, onKey, ... } while open

  function closePreview() {
    if (!preview) return;
    document.removeEventListener('keydown', preview.onKey, true);
    if (preview.host.parentNode) {
      preview.host.parentNode.removeChild(preview.host);
    }
    preview = null;
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function makeOverlay() {
    var host = document.createElement('div');
    var shadow = host.attachShadow({ mode: 'closed' });
    document.documentElement.appendChild(host);

    var style = document.createElement('style');
    style.textContent = [
      ':host { all: initial; }',
      '* { box-sizing: border-box; }',
      '.panel { position: fixed; inset: 0; z-index: 2147483647;',
      '  background: #171717; color: #eaeaea; display: flex; flex-direction: column;',
      '  font: 13px/1.45 ui-monospace, Consolas, Menlo, monospace; }',
      '.header { flex: 0 0 auto; padding: 10px 14px; background: #222;',
      '  border-bottom: 1px solid #3a3a3a; font-weight: bold; }',
      '.header .count { font-weight: normal; color: #9c9c9c; margin-left: 10px; }',
      '.header .count.warn { color: #ffb454; }',
      '.body { flex: 1 1 auto; overflow: auto; padding: 10px 14px; }',
      '.footer { flex: 0 0 auto; padding: 10px 14px; background: #222;',
      '  border-top: 1px solid #3a3a3a; display: flex; gap: 8px; }',
      'button { font: inherit; padding: 6px 14px; cursor: pointer;',
      '  background: #333; color: #eaeaea; border: 1px solid #555; border-radius: 3px; }',
      'button:hover { background: #404040; }',
      'button.primary { background: #2b6cb0; border-color: #2b6cb0; }',
      'button.primary:hover { background: #3182ce; }',
      'button:disabled { opacity: .5; cursor: default; }',
      '.tabs { margin-bottom: 8px; display: flex; gap: 6px; align-items: center; }',
      '.tabs .note { color: #9c9c9c; font-size: 12px; }',
      'pre { margin: 0; white-space: pre-wrap; word-break: break-word; }',
      '.message { color: #d0d0d0; }',
      '.errors { background: #3a2a12; border: 1px solid #7a5a20;',
      '  padding: 8px 10px; margin-bottom: 10px; color: #ffb454; }',
      '.errors .title { font-weight: bold; margin-bottom: 4px; }',
      '.errors .line { white-space: pre-wrap; }'
    ].join('\n');
    shadow.appendChild(style);

    var panel = el('div', 'panel');
    shadow.appendChild(panel);
    panel.appendChild(el('div', 'header'));
    panel.appendChild(el('div', 'body'));
    panel.appendChild(el('div', 'footer'));

    var onKey = function (e) {
      if (e.key === 'Escape' && preview) closePreview();
    };
    document.addEventListener('keydown', onKey, true);

    return {
      host: host,
      panel: panel,
      header: panel.children[0],
      body: panel.children[1],
      footer: panel.children[2],
      onKey: onKey,
      result: null,
      mode: 'json'
    };
  }

  function setHeaderCounts(ui, ruleName) {
    ui.header.textContent = '';
    ui.header.appendChild(el('span', '', ruleName));
    ui.header.appendChild(el('span', 'count',
      ui.result.items.length + ' item(s)'));
    if (ui.result.errors.length) {
      ui.header.appendChild(el('span', 'count warn',
        ui.result.errors.length + ' error(s)'));
    }
  }

  // --- client-side markdown approximation of the server format (labelled
  // as such in the UI): same shape, no ID markers, server may sanitize more ---

  function mdSafe(text) {
    return String(text).replace(/<!--/g, '&lt;!--');
  }

  function mdScalar(v) {
    if (typeof v === 'boolean') return v ? 'Yes' : 'No';
    if (typeof v === 'number') return String(v);
    if (v === null || v === undefined) return '';
    return String(v);
  }

  function mdField(name, value) {
    var label = mdSafe(String(name).replace(/[\n:]/g, ' ').trim()) || 'Field';
    if (Array.isArray(value)) {
      var lines = ['- ' + label + ':'];
      value.forEach(function (v) { lines.push('  - ' + mdSafe(mdScalar(v))); });
      return lines.join('\n');
    }
    var parts = mdSafe(mdScalar(value)).split('\n');
    if (parts.length === 1) return '- ' + label + ': ' + parts[0].trim();
    var out = ['- ' + label + ':'];
    parts.forEach(function (p) { out.push(p.trim() ? '  ' + p : '  '); });
    return out.join('\n');
  }

  function mdItem(item) {
    var names = Object.keys(item.fields);
    var first = names.length ? item.fields[names[0]] : 'Item';
    if (Array.isArray(first)) first = first.join(', ');
    var title = mdScalar(first).split('\n')[0];
    title = title.replace(/\s+/g, ' ').replace(/^#+\s*/, '').trim()
      .slice(0, 100) || 'Item';
    var out = ['## ' + mdSafe(title), ''];
    out.push('<!-- item-id: computed by the server -->', '');
    names.forEach(function (n) { out.push(mdField(n, item.fields[n])); });
    out.push('', '---', '');
    return out.join('\n');
  }

  function resultText(result, mode) {
    if (mode === 'md') return result.items.map(mdItem).join('\n');
    return JSON.stringify(
      { rule: result.rule, url: result.url, items: result.items,
        errors: result.errors },
      null, 2
    );
  }

  function renderErrors(ui, errors) {
    if (!errors.length) return;
    // Warnings, not failures: extraction continues, save stays available.
    var box = el('div', 'errors');
    box.appendChild(el('div', 'title', errors.length + ' warning(s):'));
    errors.forEach(function (msg) {
      box.appendChild(el('div', 'line', '• ' + msg));
    });
    ui.body.appendChild(box);
  }

  function showResult(result) {
    closePreview();
    var ui = makeOverlay();
    preview = ui;
    ui.result = result;
    setHeaderCounts(ui, result.rule);
    renderErrors(ui, result.errors);

    var tabs = el('div', 'tabs');
    var jsonBtn = el('button', 'primary', 'JSON');
    var mdBtn = el('button', '', 'Markdown');
    tabs.appendChild(jsonBtn);
    tabs.appendChild(mdBtn);
    tabs.appendChild(el('span', 'note',
      'Markdown view is a client-side approximation of the server file'));
    var pre = el('pre', 'message');
    ui.body.appendChild(tabs);
    ui.body.appendChild(pre);
    ui.pre = pre;

    var setMode = function (mode) {
      ui.mode = mode;
      jsonBtn.className = mode === 'json' ? 'primary' : '';
      mdBtn.className = mode === 'md' ? 'primary' : '';
      pre.textContent = resultText(result, mode);
    };
    jsonBtn.addEventListener('click', function () { setMode('json'); });
    mdBtn.addEventListener('click', function () { setMode('md'); });
    setMode('json');
    addCancel(ui);
  }

  function addCancel(ui) {
    var cancel = el('button', '', 'Cancel');
    cancel.addEventListener('click', closePreview); // transmits nothing
    ui.footer.appendChild(cancel);
  }

  function showNone(start) {
    closePreview();
    var ui = makeOverlay();
    preview = ui;
    ui.header.appendChild(el('span', '', 'No matching rule'));
    renderErrors(ui, start.errors);
    ui.body.appendChild(el('p', 'message', 'No rule matches this URL:'));
    ui.body.appendChild(el('pre', 'message', start.url));
    ui.body.appendChild(el('p', 'message', 'Rules available:'));
    start.available.forEach(function (summary) {
      ui.body.appendChild(el('pre', 'message', '• ' + summary));
    });
    addCancel(ui);
  }

  function showMany(start) {
    closePreview();
    var ui = makeOverlay();
    preview = ui;
    ui.header.appendChild(el('span', '', 'Multiple rules match'));
    ui.header.appendChild(el('span', 'count',
      start.choices.length + ' candidates'));
    renderErrors(ui, start.errors);
    ui.body.appendChild(el('p', 'message', 'Choose a rule:'));
    start.choices.forEach(function (choice) {
      var btn = el('button', '', choice.label);
      btn.style.display = 'block';
      btn.style.margin = '4px 0';
      btn.addEventListener('click', function () {
        openPreview(extractChoice(choice));
      });
      ui.body.appendChild(btn);
    });
    addCancel(ui);
  }

  function openPreview(start) {
    if (start.kind === 'one') showResult(start.result);
    else if (start.kind === 'none') showNone(start);
    else if (start.kind === 'many') showMany(start);
  }

  // TEMP dev trigger while the bookmarklet listener is not wired yet
  // (Phase 5 replaces it): bkmrklDevRun() from the console opens the preview.
  window.bkmrklDevRun = function () { openPreview(startExtraction()); };

})();
