/* Drivers Manager service worker: fast static assets, fresh pages. */
const CACHE_NAME = "dm-static-v1";
const STATIC_PREFIXES = ["/static/", "https://unpkg.com/"];

self.addEventListener("install", (event) => {
    self.skipWaiting();
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) =>
            cache.addAll([
                "/static/manifest.webmanifest",
                "/static/app-icon-192.png",
                "/static/app-icon-512.png",
            ])
        )
    );
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys().then((keys) =>
            Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
        ).then(() => self.clients.claim())
    );
});

function isStaticAsset(url) {
    return STATIC_PREFIXES.some((prefix) => url.href.startsWith(prefix) || url.pathname.startsWith(prefix));
}

self.addEventListener("fetch", (event) => {
    const request = event.request;
    if (request.method !== "GET") {
        return;
    }
    const url = new URL(request.url);

    if (isStaticAsset(url)) {
        // Stale-while-revalidate: serve cached instantly, refresh in the
        // background so unversioned URLs (guest pages) never go stale forever.
        event.respondWith(
            caches.match(request).then((cached) => {
                const refresh = fetch(request)
                    .then((response) => {
                        if (response.ok) {
                            const copy = response.clone();
                            caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
                        }
                        return response;
                    })
                    .catch(() => cached);
                return cached || refresh;
            })
        );
        return;
    }

    if (request.mode === "navigate") {
        // Network-first: pages must stay fresh; fall back to last cached copy offline.
        event.respondWith(
            fetch(request)
                .then((response) => {
                    if (response.ok) {
                        const copy = response.clone();
                        caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
                    }
                    return response;
                })
                .catch(() => caches.match(request))
        );
    }
});
