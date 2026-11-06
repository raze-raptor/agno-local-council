// Council service worker: exists so the browser offers a real install. It caches nothing and never touches /api or /auth;
// if the network is down it shows a short offline page instead of the browser error.
const OFFLINE = new Response('<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width"><title>Council</title><body style="font:16px system-ui;background:#1d1b18;color:#f6f2e9;display:grid;place-items:center;min-height:100vh;margin:0"><p style="max-width:28ch">Council can\'t reach server1. Check you are on the home network, then reload.</p>', {headers: {'Content-Type': 'text/html'}});
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', e => {
  if (e.request.mode === 'navigate') e.respondWith(fetch(e.request).catch(() => OFFLINE.clone()));
});
