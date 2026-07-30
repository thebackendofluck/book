// Companion code for "The Backend of Luck" - Chapter 14, Mobile-First Architecture for iGaming.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * Offline-First Service Worker for Casino PWA
 *
 * Cache strategies:
 *   - Cache First: Static assets (images, fonts, CSS, JS bundles)
 *   - Network First: API calls (balance, game state, lobby)
 *   - Stale While Revalidate: Game thumbnails, promotions
 *   - Network Only: Transactions, bets, deposits/withdrawals
 *
 * Regulatory note: Real-money transactions MUST always go through the network.
 *   Offline play should be limited to demo/free-play modes only.
 */

const CACHE_VERSION = 'casino-v1.2.0';
const STATIC_CACHE = `static-${CACHE_VERSION}`;
const DYNAMIC_CACHE = `dynamic-${CACHE_VERSION}`;
const API_CACHE = `api-${CACHE_VERSION}`;
const IMAGE_CACHE = `images-${CACHE_VERSION}`;

// Assets to precache on install
const PRECACHE_URLS = [
  '/',
  '/index.html',
  '/offline.html',
  '/css/main.min.css',
  '/js/app.bundle.js',
  '/js/vendor.bundle.js',
  '/fonts/casino-icons.woff2',
  '/images/logo.svg',
  '/images/placeholder-game.webp',
  '/manifest.json',
];

// Routes that must NEVER be cached (real-money operations)
const NETWORK_ONLY_PATTERNS = [
  /\/api\/v1\/transactions/,
  /\/api\/v1\/bets/,
  /\/api\/v1\/deposits/,
  /\/api\/v1\/withdrawals/,
  /\/api\/v1\/kyc/,
  /\/api\/v1\/auth\/login/,
  /\/api\/v1\/auth\/token/,
  /\/api\/v1\/responsible-gaming/,
];

// Routes using stale-while-revalidate
const STALE_REVALIDATE_PATTERNS = [
  /\/api\/v1\/games\/thumbnails/,
  /\/api\/v1\/promotions/,
  /\/api\/v1\/banners/,
  /\/images\/games\//,
  /\/images\/providers\//,
];

// Routes using network-first
const NETWORK_FIRST_PATTERNS = [
  /\/api\/v1\/balance/,
  /\/api\/v1\/games\/lobby/,
  /\/api\/v1\/games\/categories/,
  /\/api\/v1\/profile/,
  /\/api\/v1\/notifications/,
];

// Maximum cache sizes
const CACHE_LIMITS = {
  [DYNAMIC_CACHE]: 100,
  [API_CACHE]: 50,
  [IMAGE_CACHE]: 200,
};

// Maximum age for cached API responses (in ms)
const API_CACHE_MAX_AGE = 5 * 60 * 1000; // 5 minutes

// ─────────────────────────────────────────────
// Install: Precache static assets
// ─────────────────────────────────────────────
self.addEventListener('install', (event) => {
  console.log('[SW] Installing service worker:', CACHE_VERSION);
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then((cache) => {
        console.log('[SW] Precaching static assets');
        return cache.addAll(PRECACHE_URLS);
      })
      .then(() => self.skipWaiting())
  );
});

// ─────────────────────────────────────────────
// Activate: Clean up old caches
// ─────────────────────────────────────────────
self.addEventListener('activate', (event) => {
  console.log('[SW] Activating service worker:', CACHE_VERSION);
  event.waitUntil(
    caches.keys()
      .then((cacheNames) => {
        return Promise.all(
          cacheNames
            .filter((name) => {
              // Delete caches from previous versions
              return !name.endsWith(CACHE_VERSION);
            })
            .map((name) => {
              console.log('[SW] Deleting old cache:', name);
              return caches.delete(name);
            })
        );
      })
      .then(() => self.clients.claim())
  );
});

// ─────────────────────────────────────────────
// Fetch: Route-based cache strategies
// ─────────────────────────────────────────────
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Skip non-GET requests
  if (request.method !== 'GET') {
    return;
  }

  // Skip cross-origin requests (CDN exceptions below)
  if (url.origin !== self.location.origin && !isTrustedCDN(url)) {
    return;
  }

  // Strategy: Network Only (transactions, bets, auth)
  if (matchesPatterns(url.pathname, NETWORK_ONLY_PATTERNS)) {
    event.respondWith(networkOnly(request));
    return;
  }

  // Strategy: Stale While Revalidate (thumbnails, promos)
  if (matchesPatterns(url.pathname, STALE_REVALIDATE_PATTERNS)) {
    event.respondWith(staleWhileRevalidate(request, IMAGE_CACHE));
    return;
  }

  // Strategy: Network First (balance, lobby, profile)
  if (matchesPatterns(url.pathname, NETWORK_FIRST_PATTERNS)) {
    event.respondWith(networkFirst(request, API_CACHE));
    return;
  }

  // Strategy: Cache First (static assets)
  event.respondWith(cacheFirst(request));
});

// ─────────────────────────────────────────────
// Cache Strategies
// ─────────────────────────────────────────────

/**
 * Cache First: Serve from cache, fall back to network.
 * Best for: versioned static assets (CSS, JS, fonts, images)
 */
async function cacheFirst(request) {
  const cachedResponse = await caches.match(request);
  if (cachedResponse) {
    return cachedResponse;
  }

  try {
    const networkResponse = await fetch(request);
    if (networkResponse.ok) {
      const cache = await caches.open(STATIC_CACHE);
      cache.put(request, networkResponse.clone());
    }
    return networkResponse;
  } catch (error) {
    console.warn('[SW] Cache-first fallback failed:', request.url);
    return offlineFallback(request);
  }
}

/**
 * Network First: Try network, fall back to cache.
 * Best for: frequently updated data (balance, lobby)
 */
async function networkFirst(request, cacheName) {
  try {
    const networkResponse = await fetchWithTimeout(request, 5000);
    if (networkResponse.ok) {
      const cache = await caches.open(cacheName);
      // Store with timestamp for cache invalidation
      const responseToCache = networkResponse.clone();
      const headers = new Headers(responseToCache.headers);
      headers.set('x-sw-cached-at', Date.now().toString());

      const timedResponse = new Response(await responseToCache.blob(), {
        status: responseToCache.status,
        statusText: responseToCache.statusText,
        headers,
      });
      cache.put(request, timedResponse);
      await trimCache(cacheName, CACHE_LIMITS[cacheName] || 50);
    }
    return networkResponse;
  } catch (error) {
    console.warn('[SW] Network-first falling back to cache:', request.url);
    const cachedResponse = await caches.match(request);
    if (cachedResponse) {
      // Check if cached response is too stale
      const cachedAt = parseInt(cachedResponse.headers.get('x-sw-cached-at') || '0');
      if (Date.now() - cachedAt > API_CACHE_MAX_AGE) {
        console.warn('[SW] Cached API response is stale:', request.url);
        // Still serve it but mark as stale for the client
      }
      return cachedResponse;
    }
    return offlineFallback(request);
  }
}

/**
 * Stale While Revalidate: Serve from cache immediately, update in background.
 * Best for: game thumbnails, promotional banners
 */
async function staleWhileRevalidate(request, cacheName) {
  const cache = await caches.open(cacheName);
  const cachedResponse = await cache.match(request);

  const fetchPromise = fetch(request)
    .then((networkResponse) => {
      if (networkResponse.ok) {
        cache.put(request, networkResponse.clone());
        trimCache(cacheName, CACHE_LIMITS[cacheName] || 100);
      }
      return networkResponse;
    })
    .catch((error) => {
      console.warn('[SW] Background revalidation failed:', request.url);
      return cachedResponse;
    });

  return cachedResponse || fetchPromise;
}

/**
 * Network Only: Always fetch from network. No caching.
 * Best for: transactions, bets, auth tokens
 * REGULATORY REQUIREMENT: Real-money operations must never be cached.
 */
async function networkOnly(request) {
  try {
    return await fetch(request);
  } catch (error) {
    // For transaction endpoints, return a proper error rather than offline page
    return new Response(
      JSON.stringify({
        error: 'OFFLINE',
        message: 'You are currently offline. Real-money transactions require an active internet connection.',
        code: 'NETWORK_UNAVAILABLE',
      }),
      {
        status: 503,
        headers: { 'Content-Type': 'application/json' },
      }
    );
  }
}

// ─────────────────────────────────────────────
// Background Sync: Queue bets placed offline (demo mode only)
// ─────────────────────────────────────────────
self.addEventListener('sync', (event) => {
  if (event.tag === 'sync-demo-bets') {
    event.waitUntil(syncDemoBets());
  }
  if (event.tag === 'sync-analytics') {
    event.waitUntil(syncAnalyticsQueue());
  }
});

async function syncDemoBets() {
  const db = await openIndexedDB();
  const tx = db.transaction('demo-bet-queue', 'readwrite');
  const store = tx.objectStore('demo-bet-queue');
  const bets = await getAllFromStore(store);

  for (const bet of bets) {
    try {
      const response = await fetch('/api/v1/demo/bets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(bet),
      });
      if (response.ok) {
        const delTx = db.transaction('demo-bet-queue', 'readwrite');
        delTx.objectStore('demo-bet-queue').delete(bet.id);
      }
    } catch (error) {
      console.warn('[SW] Failed to sync demo bet:', bet.id);
    }
  }
}

async function syncAnalyticsQueue() {
  const db = await openIndexedDB();
  const tx = db.transaction('analytics-queue', 'readwrite');
  const store = tx.objectStore('analytics-queue');
  const events = await getAllFromStore(store);

  if (events.length === 0) return;

  try {
    const response = await fetch('/api/v1/analytics/batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ events }),
    });
    if (response.ok) {
      const clearTx = db.transaction('analytics-queue', 'readwrite');
      clearTx.objectStore('analytics-queue').clear();
    }
  } catch (error) {
    console.warn('[SW] Failed to sync analytics batch');
  }
}

// ─────────────────────────────────────────────
// Push Notifications
// ─────────────────────────────────────────────
self.addEventListener('push', (event) => {
  if (!event.data) return;

  const payload = event.data.json();

  // Validate notification payload
  const allowedTypes = [
    'bonus_available', 'tournament_starting', 'withdrawal_complete',
    'deposit_confirmed', 'kyc_verified', 'promo_expiring',
    'responsible_gaming_reminder',
  ];

  if (!allowedTypes.includes(payload.type)) {
    console.warn('[SW] Unknown notification type:', payload.type);
    return;
  }

  const options = {
    body: payload.body,
    icon: payload.icon || '/images/icons/icon-192x192.png',
    badge: '/images/icons/badge-72x72.png',
    image: payload.image,
    data: {
      url: payload.url || '/',
      type: payload.type,
      id: payload.id,
    },
    actions: payload.actions || [],
    tag: payload.tag || payload.type,
    renotify: payload.renotify || false,
    requireInteraction: payload.type === 'responsible_gaming_reminder',
    vibrate: [100, 50, 100],
  };

  event.waitUntil(
    self.registration.showNotification(payload.title, options)
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();

  const { url, type, id } = event.notification.data;

  // Track notification click
  event.waitUntil(
    fetch('/api/v1/analytics/notification-click', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type, id, action: event.action }),
    }).catch(() => {})
  );

  // Open or focus the appropriate page
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true })
      .then((clientList) => {
        // Try to focus an existing window
        for (const client of clientList) {
          if (client.url.includes(self.location.origin)) {
            client.navigate(url);
            return client.focus();
          }
        }
        // Open a new window
        return clients.openWindow(url);
      })
  );
});

// ─────────────────────────────────────────────
// Utility Functions
// ─────────────────────────────────────────────

function matchesPatterns(pathname, patterns) {
  return patterns.some((pattern) => pattern.test(pathname));
}

function isTrustedCDN(url) {
  const trustedOrigins = [
    'https://cdn.casinoplatform.com',
    'https://fonts.googleapis.com',
    'https://fonts.gstatic.com',
  ];
  return trustedOrigins.includes(url.origin);
}

function fetchWithTimeout(request, timeout) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Fetch timeout')), timeout);
    fetch(request)
      .then((response) => {
        clearTimeout(timer);
        resolve(response);
      })
      .catch((error) => {
        clearTimeout(timer);
        reject(error);
      });
  });
}

async function trimCache(cacheName, maxItems) {
  const cache = await caches.open(cacheName);
  const keys = await cache.keys();
  if (keys.length > maxItems) {
    // Remove oldest entries (FIFO)
    const toDelete = keys.slice(0, keys.length - maxItems);
    await Promise.all(toDelete.map((key) => cache.delete(key)));
  }
}

async function offlineFallback(request) {
  if (request.headers.get('Accept')?.includes('text/html')) {
    const cache = await caches.open(STATIC_CACHE);
    return cache.match('/offline.html') || new Response(
      '<html><body><h1>You are offline</h1><p>Please check your connection.</p></body></html>',
      { headers: { 'Content-Type': 'text/html' } }
    );
  }
  return new Response('Offline', { status: 503 });
}

function openIndexedDB() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open('casino-sw-db', 1);
    request.onupgradeneeded = (event) => {
      const db = event.target.result;
      if (!db.objectStoreNames.contains('demo-bet-queue')) {
        db.createObjectStore('demo-bet-queue', { keyPath: 'id' });
      }
      if (!db.objectStoreNames.contains('analytics-queue')) {
        db.createObjectStore('analytics-queue', { keyPath: 'id', autoIncrement: true });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function getAllFromStore(store) {
  return new Promise((resolve, reject) => {
    const request = store.getAll();
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}
