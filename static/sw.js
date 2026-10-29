'use strict';
const CACHE='memory-director-public-v1';
const PUBLIC=['/static/offline.html','/static/pwa.css','/static/icons/app-180.png'];
self.addEventListener('install',event=>{
  event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(PUBLIC)).then(()=>self.skipWaiting()));
});
self.addEventListener('activate',event=>{
  event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(key=>key.startsWith('memory-director-public-')&&key!==CACHE).map(key=>caches.delete(key)))).then(()=>self.clients.claim()));
});
self.addEventListener('fetch',event=>{
  const request=event.request,url=new URL(request.url);
  if(request.method!=='GET'||url.origin!==self.location.origin)return;
  // Never intercept API, media, account HTML, uploads, notes or face thumbnails.
  if(request.mode==='navigate'&&url.pathname==='/'){
    event.respondWith(fetch(request).catch(()=>caches.match('/static/offline.html')));
  }else if(PUBLIC.includes(url.pathname)&&!url.search){
    event.respondWith(fetch(request).catch(()=>caches.match(url.pathname)));
  }
});
