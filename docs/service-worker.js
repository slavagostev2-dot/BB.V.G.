'use strict';

const CACHE='bb-vg-v5.1.0';
const SHELL=[
  './',
  './index.html',
  './styles.css?v=5.0.0',
  './theme.css?v=1.0.0',
  './theme.js?v=1.0.0',
  './app.js?v=4.0.1',
  './bbvg-controls.js?v=2.0.0',
  './runtime-config.js?v=5.0.0',
  './manifest.webmanifest',
  './icon.svg',
  './splash.svg'
];

self.addEventListener('install',event=>{
  event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(SHELL)).then(()=>self.skipWaiting()));
});
self.addEventListener('activate',event=>{
  event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(key=>key!==CACHE).map(key=>caches.delete(key)))).then(()=>self.clients.claim()));
});
self.addEventListener('fetch',event=>{
  if(event.request.method!=='GET')return;
  const url=new URL(event.request.url);
  if(url.origin!==location.origin)return;
  event.respondWith(fetch(event.request).then(response=>{
    const copy=response.clone();
    caches.open(CACHE).then(cache=>cache.put(event.request,copy));
    return response;
  }).catch(()=>caches.match(event.request).then(response=>response||caches.match('./index.html'))));
});
