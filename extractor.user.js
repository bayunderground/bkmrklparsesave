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

  // TEMP dev trigger while there is no preview UI yet (Phase 5 replaces it):
  // run bkmrklDevRun() from the console to inspect the extraction result.
  window.bkmrklDevRun = function () { return startExtraction(); };

})();
