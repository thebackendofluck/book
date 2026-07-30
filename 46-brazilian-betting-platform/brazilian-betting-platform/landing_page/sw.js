// Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * BetBR Service Worker
 * Provides offline caching for the sports betting platform shell.
 * Content-Type: application/javascript
 */

const CACHE_NAME = 'betbr-v1';
const SHELL_ASSETS = [
  '/',
  '/index.html',
  '/manifest.json',
];

// Install: cache shell assets
self.addEventListener('install', function(event) {
  event.waitUntil(
    caches.open(CACHE_NAME).then(function(cache) {
      return cache.addAll(SHELL_ASSETS);
    })
  );
  self.skipWaiting();
});

// Activate: clean old caches
self.addEventListener('activate', function(event) {
  event.waitUntil(
    caches.keys().then(function(names) {
      return Promise.all(
        names
          .filter(function(name) { return name !== CACHE_NAME; })
          .map(function(name) { return caches.delete(name); })
      );
    })
  );
  self.clients.claim();
});

// Fetch: network-first for API calls, cache-first for shell assets
self.addEventListener('fetch', function(event) {
  var url = new URL(event.request.url);

  // Never cache API or SSE requests
  if (url.pathname.startsWith('/odds/') ||
      url.pathname.startsWith('/bets/') ||
      url.pathname.startsWith('/catalogue/') ||
      url.pathname.startsWith('/trader/')) {
    return;
  }

  event.respondWith(
    caches.match(event.request).then(function(cached) {
      if (cached) {
        // Return cached, but also update cache in background
        fetch(event.request).then(function(response) {
          if (response && response.status === 200) {
            caches.open(CACHE_NAME).then(function(cache) {
              cache.put(event.request, response);
            });
          }
        }).catch(function() {});
        return cached;
      }
      return fetch(event.request).then(function(response) {
        if (response && response.status === 200 && response.type === 'basic') {
          var clone = response.clone();
          caches.open(CACHE_NAME).then(function(cache) {
            cache.put(event.request, clone);
          });
        }
        return response;
      });
    })
  );
});
