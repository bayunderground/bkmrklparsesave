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

})();
