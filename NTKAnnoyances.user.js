// ==UserScript==
// @name         NTK Annoyance Remover
// @namespace    ntk
// @version      1.2
// @description  Disables warning banners, devtools blocker and adblock detection.
// @author       tyuop077
// @match        *://sbxh3.com/*
// @homepageURL  https://github.com/tyuop077/NTKDownloader
// @run-at       document-start
// @grant        none
// ==/UserScript==

(function() {
    'use strict';

    const injectScript = function(code) {
        const script = document.createElement('script');
        script.textContent = '(' + code.toString() + ')();';
        (document.head || document.documentElement).appendChild(script);
        script.remove();
    };

    const payload = function() {
        // 1. Initial states and overrides
        window.__ntkDevtoolsPreflight = 1;
        window.__ntkDevtoolsTripped = false;
        window.stop = function() {};

        // Prevent DevToolsBlocker from forcing a Google redirect if triggered
        const origReplace = window.location.replace;
        window.location.replace = function(url) {
            const target = typeof url === 'string' ? url : (url && url.toString ? url.toString() : '');
            if (target.includes('google.com')) return;
            return origReplace.apply(this, arguments);
        };

        // 2. Cosmetic filter
        const style = document.createElement('style');
        style.textContent = `
            div[id="ntk_blk_overlay"],
            div[id="ntk_ad_allow_overlay"],
            div[id="ntk_dev_warn_modal"],
            .ntk-fade-in {
                display: none !important;
                opacity: 0 !important;
                pointer-events: none !important;
                z-index: -1 !important;
            }
            body, html {
                overflow: auto !important;
            }
        `;
        (document.head || document.documentElement).appendChild(style);

        // 3. Data cleanup and interception
        const targetCookies = ['ntk_blk', 'ntk_blk_ok', 'ntk_unlock'];
        const targetKeys = ['ntk_blk', 'ntk_dev_warn'];

        try {
            for (let i = 0; i < localStorage.length; i++) {
                const key = localStorage.key(i);
                if (key && targetKeys.some(k => key.startsWith(k))) {
                    localStorage.removeItem(key);
                    i--;
                }
            }
        } catch(e) {}

        targetCookies.forEach(cookie => {
            document.cookie = `${cookie}=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;`;
        });

        try {
            const req = indexedDB.open('ntk', 1);
            req.onsuccess = (e) => {
                const db = e.target.result;
                if (db.objectStoreNames.contains('kv')) {
                    try { db.transaction('kv', 'readwrite').objectStore('kv').delete('ntk_blk'); } catch(e) {}
                }
            };
        } catch(e) {}

        const origSetItem = Storage.prototype.setItem;
        Storage.prototype.setItem = function(key, val) {
            if (targetKeys.some(k => key.startsWith(k))) return;
            origSetItem.call(this, key, val);
        };

        const origCookie = Object.getOwnPropertyDescriptor(Document.prototype, 'cookie');
        if (origCookie) {
            Object.defineProperty(document, 'cookie', {
                get: () => origCookie.get.call(document),
                set: (val) => {
                    if (targetKeys.some(k => val.includes(k))) return;
                    origCookie.set.call(document, val);
                }
            });
        }

        // 4. API request mocking
        const origFetch = window.fetch;
        window.fetch = async function(...args) {
            const url = typeof args[0] === 'string' ? args[0] : (args[0] && args[0].url ? args[0].url : '');

            if (url.includes('/api/me/block-check')) {
                return new Response(JSON.stringify({ blocked: false, ok: true }), {
                    status: 200, headers: { 'Content-Type': 'application/json' }
                });
            }
            if (url.includes('/api/dev-block') || url.includes('/api/m/ev')) {
                return new Response(JSON.stringify({ ok: true }), {
                    status: 200, headers: { 'Content-Type': 'application/json' }
                });
            }

            return origFetch.apply(this, args);
        };

        if (navigator.sendBeacon) {
            const origBeacon = navigator.sendBeacon;
            navigator.sendBeacon = function(url, data) {
                if (typeof url === 'string' && (url.includes('/api/dev-block') || url.includes('/api/m/ev'))) return true;
                return origBeacon.apply(this, arguments);
            };
        }

        // 5. Block external init script
        const MOCK_JS = 'data:application/javascript,console.log("[Bypass] block.js intercepted!");';
        const origSrcDesc = Object.getOwnPropertyDescriptor(HTMLScriptElement.prototype, 'src');
        if (origSrcDesc) {
            Object.defineProperty(HTMLScriptElement.prototype, 'src', {
                get: function() { return origSrcDesc.get.call(this); },
                set: function(val) {
                    if (typeof val === 'string' && val.includes('/init/block.js')) val = MOCK_JS;
                    origSrcDesc.set.call(this, val);
                }
            });
        }
        const origSetAttribute = Element.prototype.setAttribute;
        Element.prototype.setAttribute = function(name, value) {
            if (this.tagName === 'SCRIPT' && name === 'src' && typeof value === 'string' && value.includes('/init/block.js')) value = MOCK_JS;
            return origSetAttribute.call(this, name, value);
        };

        // 6. Webpack chunk interceptor
        const badExports = [
            'BlockCheck', 'BuildIdGuard', 'DevToolsBlocker', /* 'AdAckBeacon', */
            'InitBlockGuard', 'AdBlockGuard', 'AdminBrowserDisguise', 'DevToolsBlockerGate'
        ];

        let _webpackChunk = window.webpackChunk_N_E || [];
        function hookWebpackChunk(array) {
            if (array.__hooked) return;
            const origPush = array.push.bind(array);
            array.push = function(chunkData) {
                const modules = chunkData[1];
                if (modules) {
                    for (const modId in modules) {
                        const origMod = modules[modId];
                        if (typeof origMod === 'function') {
                            modules[modId] = function(e, t, n) {
                                badExports.forEach(key => {
                                    try {
                                        Object.defineProperty(t, key, {
                                            enumerable: true,
                                            get: () => {
                                                if (key === 'DevToolsBlockerGate') return (props) => props.children || null;
                                                return () => null;
                                            }
                                        });
                                    } catch (err) {}
                                });
                                return origMod(e, t, n);
                            };
                        }
                    }
                }
                return origPush(chunkData);
            };
            array.__hooked = true;
        }
        hookWebpackChunk(_webpackChunk);
        Object.defineProperty(window, 'webpackChunk_N_E', {
            get: () => _webpackChunk,
            set: (val) => { hookWebpackChunk(val); _webpackChunk = val; }
        });
    };

    injectScript(payload);
})();
