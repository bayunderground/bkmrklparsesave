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

})();
