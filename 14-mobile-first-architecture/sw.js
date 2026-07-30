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
 * sw.js — iGaming PWA Service Worker
 *
 * Implements a multi-strategy cache for the casino PWA:
 *   - Cache-first for versioned/hashed static assets (JS, CSS, images)
 *   - Network-first for API calls (game state, balance, account)
 *   - Stale-while-revalidate for game lobby and promotional content
 *   - Network-only for payment endpoints (never cached)
 *
 * Offline fallback: serves a pre-cached offline page when network is unavailable.
 * Background sync: queues failed bet placements for retry when connection restores.
 *
 * Chapter 14 — Mobile-First Architecture for iGaming
 */

'use strict';

const CACHE_VERSION = 'v1';
const STATIC_CACHE = `casino-static-${CACHE_VERSION}`;
const DYNAMIC_CACHE = `casino-dynamic-${CACHE_VERSION}`;
const OFFLINE_CACHE = `casino-offline-${CACHE_VERSION}`;

const OFFLINE_FALLBACK_URL = '/offline.html';
const OFFLINE_GAME_IMAGE = '/images/offline-placeholder.png';

// Assets to pre-cache on install
const PRECACHE_URLS = [
  '/',
  '/offline.html',
  '/manifest.json',
  '/images/logo.svg',
  '/images/offline-placeholder.png',
  '/fonts/casino-icons.woff2',
];

// Routes that must NEVER be cached (financial transactions)
const NEVER_CACHE_PATTERNS = [
  /\/api\/v\d+\/payments\//,
  /\/api\/v\d+\/deposits\//,
  /\/api\/v\d+\/withdrawals\//,
  /\/api\/v\d+\/wallet\/debit/,
];

// Routes that use network-first strategy
const NETWORK_FIRST_PATTERNS = [
  /\/api\/v\d+\/account\//,
  /\/api\/v\d+\/balance/,
  /\/api\/v\d+\/game-session\//,
  /\/api\/v\d+\/compliance\//,
  /\/api\/v\d+\/rg\//,           // Responsible gaming — always fresh
];

// Routes that use stale-while-revalidate
const SWR_PATTERNS = [
  /\/api\/v\d+\/lobby\//,
  /\/api\/v\d+\/promotions\//,
  /\/api\/v\d+\/games\/list/,
  /\/images\/game-thumbnails\//,
];

// ---------------------------------------------------------------------------
// Install: pre-cache core assets
// ---------------------------------------------------------------------------

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(OFFLINE_CACHE);
      await cache.addAll(PRECACHE_URLS);
      await self.skipWaiting(); // Activate immediately
    })()
  );
});

// ---------------------------------------------------------------------------
// Activate: clean up stale caches
// ---------------------------------------------------------------------------

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(
        keys
          .filter((key) => key !== STATIC_CACHE && key !== DYNAMIC_CACHE && key !== OFFLINE_CACHE)
          .map((key) => caches.delete(key))
      );
      await self.clients.claim();
    })()
  );
});

// ---------------------------------------------------------------------------
// Fetch: route-based caching strategy
// ---------------------------------------------------------------------------

self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Ignore non-GET requests except for background sync
  if (request.method !== 'GET') {
    // Queue failed mutations for background sync
    if (request.method === 'POST' && url.pathname.includes('/api/')) {
      event.respondWith(
        fetch(request.clone()).catch(() => queueForSync(request))
      );
    }
    return;
  }

  // Never cache financial endpoints
  if (NEVER_CACHE_PATTERNS.some((p) => p.test(url.pathname))) {
    event.respondWith(fetch(request));
    return;
  }

  // Network-first for real-time data
  if (NETWORK_FIRST_PATTERNS.some((p) => p.test(url.pathname))) {
    event.respondWith(networkFirst(request));
    return;
  }

  // Stale-while-revalidate for lobby content
  if (SWR_PATTERNS.some((p) => p.test(url.pathname))) {
    event.respondWith(staleWhileRevalidate(request));
    return;
  }

  // Cache-first for static/hashed assets
  if (isStaticAsset(url)) {
    event.respondWith(cacheFirst(request));
    return;
  }

  // Default: network-first with offline fallback
  event.respondWith(networkFirstWithFallback(request));
});

// ---------------------------------------------------------------------------
// Background Sync: retry queued API calls
// ---------------------------------------------------------------------------

self.addEventListener('sync', (event) => {
  if (event.tag === 'retry-api-calls') {
    event.waitUntil(retryQueuedCalls());
  }
});

// ---------------------------------------------------------------------------
// Push notifications: responsible gaming aware
// ---------------------------------------------------------------------------

self.addEventListener('push', (event) => {
  if (!event.data) return;

  let payload;
  try {
    payload = event.data.json();
  } catch {
    payload = { title: 'Casino Alert', body: event.data.text(), type: 'general' };
  }

  // Suppress push if player has set quiet hours
  event.waitUntil(
    (async () => {
      const isQuietHours = await checkQuietHours();
      if (isQuietHours && payload.type !== 'rg_mandatory') return;

      const options = {
        body: payload.body,
        icon: '/images/logo-192.png',
        badge: '/images/badge-72.png',
        tag: payload.tag || 'casino-notification',
        requireInteraction: payload.requireInteraction || false,
        data: { url: payload.url || '/', type: payload.type },
        actions: payload.actions || [],
      };

      await self.registration.showNotification(payload.title, options);
    })()
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const targetUrl = event.notification.data?.url || '/';

  event.waitUntil(
    self.clients.matchAll({ type: 'window' }).then((clients) => {
      for (const client of clients) {
        if (client.url === targetUrl && 'focus' in client) {
          return client.focus();
        }
      }
      return self.clients.openWindow(targetUrl);
    })
  );
});

// ---------------------------------------------------------------------------
// Caching strategies
// ---------------------------------------------------------------------------

async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;

  const response = await fetch(request);
  if (response.ok) {
    const cache = await caches.open(STATIC_CACHE);
    cache.put(request, response.clone());
  }
  return response;
}

async function networkFirst(request) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(DYNAMIC_CACHE);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    const cached = await caches.match(request);
    return cached || new Response(JSON.stringify({ error: 'offline', cached: false }), {
      status: 503,
      headers: { 'Content-Type': 'application/json' },
    });
  }
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(DYNAMIC_CACHE);
  const cached = await cache.match(request);

  const fetchPromise = fetch(request).then((response) => {
    if (response.ok) cache.put(request, response.clone());
    return response;
  }).catch(() => null);

  return cached || await fetchPromise || new Response(null, { status: 503 });
}

async function networkFirstWithFallback(request) {
  try {
    return await fetch(request);
  } catch {
    const cached = await caches.match(request);
    if (cached) return cached;

    // Return offline page for navigation requests
    if (request.mode === 'navigate') {
      return caches.match(OFFLINE_FALLBACK_URL);
    }

    return new Response(null, { status: 503 });
  }
}

// ---------------------------------------------------------------------------
// Background sync helpers
// ---------------------------------------------------------------------------

const syncQueue = [];

async function queueForSync(request) {
  const body = await request.clone().text();
  syncQueue.push({ url: request.url, method: request.method, body, timestamp: Date.now() });

  if ('sync' in self.registration) {
    await self.registration.sync.register('retry-api-calls');
  }

  return new Response(JSON.stringify({ queued: true }), {
    status: 202,
    headers: { 'Content-Type': 'application/json' },
  });
}

async function retryQueuedCalls() {
  while (syncQueue.length > 0) {
    const item = syncQueue[0];
    try {
      await fetch(item.url, { method: item.method, body: item.body });
      syncQueue.shift();
    } catch {
      break; // Still offline — leave in queue
    }
  }
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function isStaticAsset(url) {
  return (
    url.pathname.match(/\.(js|css|woff2?|ttf|svg|png|jpg|webp|avif|ico)$/) !== null ||
    url.pathname.includes('/static/') ||
    url.pathname.includes('/_next/static/')
  );
}

async function checkQuietHours() {
  try {
    const cache = await caches.open(DYNAMIC_CACHE);
    const resp = await cache.match('/api/v1/account/preferences');
    if (!resp) return false;
    const prefs = await resp.json();
    if (!prefs.quietHoursEnabled || !prefs.quietHoursStart || !prefs.quietHoursEnd) return false;

    const now = new Date();
    const hour = now.getHours();
    return hour >= prefs.quietHoursStart || hour < prefs.quietHoursEnd;
  } catch {
    return false;
  }
}
