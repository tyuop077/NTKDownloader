// ==UserScript==
// @name         NTK Annoyance Remover
// @namespace    ntk
// @version      1.1
// @description  Disables warning banner, devtools blocker, adblock detection, and mimics Ad-Acks.
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
        // 1. INLINE SCRIPT KILL-SWITCHES
        window.__ntkDevtoolsPreflight = 1;
        window.__ntkDevtoolsTripped = false;
        window.stop = function() {};

        const targetCookies = ['ntk_blk', 'ntk_blk_ok', 'ntk_unlock'];
        const targetKeys = ['ntk_blk', 'ntk_dev_warn'];

        // 2. DATA CLEANUP
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
            if (key.startsWith('ntk_blk') || key === 'ntk_dev_warn') return;
            origSetItem.call(this, key, val);
        };

        const origCookie = Object.getOwnPropertyDescriptor(Document.prototype, 'cookie');
        if (origCookie) {
            Object.defineProperty(document, 'cookie', {
                get: () => origCookie.get.call(document),
                set: (val) => {
                    if (val.includes('ntk_blk')) return;
                    origCookie.set.call(document, val);
                }
            });
        }

        // 3. DYNAMIC AD AUTO-ACKER
        const origFetch = window.fetch;
        async function autoAckAd() {
            const path = window.location.pathname;
            try {
                const chalRes = await origFetch("/api/ad/challenge", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ path: path }),
                    credentials: "include"
                });
                if (!chalRes.ok) return;
                const chalData = await chalRes.json();
                if (!chalData?.challenge?.token) return;

                // DYNAMIC CALCULATION: Mimic the exact logic of the original g() function
                let expectedTotal = 0;
                document.querySelectorAll('[data-br="1"][data-br-n]').forEach(el => {
                    let n = parseInt(el.getAttribute("data-br-n") || "0", 10);
                    if (Number.isFinite(n) && n > 0) expectedTotal += n;
                });

                const slotNonces = chalData.challenge.slotNonces || [];
                if (expectedTotal === 0) expectedTotal = slotNonces.length || 2;

                // Server expects slotNonces array length to match 'visible' count
                const visibleNonces = slotNonces.slice(0, expectedTotal);

                const ackRes = await origFetch("/api/ad/ack", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        challengeToken: chalData.challenge.token,
                        total: expectedTotal,
                        visible: expectedTotal,
                        path: path,
                        slotNonces: visibleNonces
                    }),
                    credentials: "include"
                });

                if (ackRes.ok) {
                    window.__ntk_ad_ack_scope = path;
                    window.dispatchEvent(new CustomEvent("ntk-ad-ack-ready", { detail: { scope: path } }));
                }
            } catch (e) {}
        }

        autoAckAd();
        const origPushState = history.pushState;
        history.pushState = function() { origPushState.apply(this, arguments); autoAckAd(); };
        window.addEventListener('popstate', autoAckAd);
        window.addEventListener('ntk-ad-allow-needed', autoAckAd);

        // 4. API REQUEST & BEACON MOCKING
        window.fetch = async function(...args) {
            const url = typeof args[0] === 'string' ? args[0] : (args[0] && args[0].url ? args[0].url : '');
            if (url.includes('/api/me/block-check') || url.includes('/api/dev-block')) {
                return new Response(JSON.stringify({ blocked: false, ok: true }), {
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

        // 5. BLOCK EXTERNAL SCRIPT
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

        // 6. WEBPACK CHUNK INTERCEPTOR
        const badExports = [
            'BlockCheck', 'BuildIdGuard', 'DevToolsBlocker', 'AdAckBeacon',
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
