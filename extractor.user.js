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
  // === RULES END ===

})();
