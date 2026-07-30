// Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * Cloudflare Workers for iGaming Platform Security
 * Implements geographic blocking, WAF rules, and bot detection
 * for Brazilian betting platforms and regulated gaming environments.
 */

// =============================================================================
// Worker 1: Basic Geo-Blocking
// =============================================================================

addEventListener('fetch', event => {
  event.respondWith(handleRequest(event.request))
})

async function handleRequest(request) {
  const country = request.cf.country

  // Blocked countries list
  const blockedCountries = ['NL', 'DE']

  if (blockedCountries.includes(country)) {
    return new Response('Access denied due to regional regulations', {
      status: 403,
      headers: {
        'Content-Type': 'text/plain'
      }
    })
  }

  // Allow access for other countries
  return fetch(request)
}


// =============================================================================
// Worker 2: CDN Optimization for iGaming Assets
// =============================================================================

async function handleCDNRequest(request) {
  const url = new URL(request.url)

  // Cache static game assets
  if (url.pathname.startsWith('/assets/games/')) {
    const cacheKey = new Request(url, request)
    const cache = caches.default

    let response = await cache.match(cacheKey)
    if (!response) {
      response = await fetch(request)
      // Cache for 1 hour
      response = new Response(response.body, response)
      response.headers.set('Cache-Control', 'public, max-age=3600')
      event.waitUntil(cache.put(cacheKey, response.clone()))
    }
    return response
  }

  // API requests - no caching
  if (url.pathname.startsWith('/api/')) {
    return fetch(request)
  }

  return fetch(request)
}


// =============================================================================
// Worker 3: Brazilian-Specific WAF Rules
// =============================================================================

async function handleBrazilianWAF(request) {
  const country = request.cf.country
  const userAgent = request.headers.get('User-Agent') || ''
  const url = new URL(request.url)

  // Block access from restricted Brazilian states if needed
  const restrictedStates = ['SP', 'RJ'] // Example - adjust based on regulations
  if (country === 'BR' && restrictedStates.includes(request.cf.region)) {
    return new Response('Access restricted in this region', { status: 403 })
  }

  // Detect gambling-specific attack patterns
  const suspiciousPatterns = [
    /bonus.*farm/i,
    /multiple.*account/i,
    /auto.*bet/i,
    /script.*injection/i
  ]

  const requestBody = await request.text()
  for (const pattern of suspiciousPatterns) {
    if (pattern.test(requestBody) || pattern.test(url.search)) {
      return new Response('Suspicious activity detected', { status: 403 })
    }
  }

  return fetch(request)
}


// =============================================================================
// Worker 4: State-Level Geo-Blocking
// =============================================================================

async function handleGeoBlocking(request) {
  const country = request.cf.country
  const region = request.cf.region // Brazilian state code

  // Block specific states where online gambling is restricted
  const blockedStates = ['SP', 'RJ', 'MG'] // São Paulo, Rio de Janeiro, Minas Gerais

  if (country === 'BR' && blockedStates.includes(region)) {
    return new Response(JSON.stringify({
      error: 'Access restricted',
      message: 'Online gambling is restricted in your state',
      code: 'GEO_BLOCKED'
    }), {
      status: 403,
      headers: { 'Content-Type': 'application/json' }
    })
  }

  return fetch(request)
}


// =============================================================================
// Worker 5: Advanced Bot Detection
// =============================================================================

async function handleBotDetection(request) {
  const botScore = request.cf.bot_management.score
  const ja3Fingerprint = request.cf.ja3_hash
  const url = new URL(request.url)

  // Known bot JA3 fingerprints
  const knownBotJA3 = [
    'hash1', 'hash2', 'hash3' // Replace with actual hashes
  ]

  if (knownBotJA3.includes(ja3Fingerprint)) {
    return new Response('Bot detected', { status: 403 })
  }

  // Behavioral analysis for gambling bots
  if (url.pathname.includes('/bet') && botScore < 40) {
    // Check for rapid successive requests
    const clientIP = request.headers.get('CF-Connecting-IP')
    const key = `requests:${clientIP}`

    // This would require Durable Objects or KV for state
    // Simplified version
    if (await checkRequestRate(clientIP)) {
      return new Response('Rate limit exceeded', { status: 429 })
    }
  }

  return fetch(request)
}

async function checkRequestRate(ip) {
  // Implementation would use Cloudflare KV or Durable Objects
  // to track request rates per IP
  return false // Placeholder
}


// =============================================================================
// Worker 6: Turnstile Human Verification
// =============================================================================

document.addEventListener('DOMContentLoaded', function() {
  turnstile.render('.cf-turnstile', {
    sitekey: 'YOUR_SITE_KEY',
    callback: function(token) {
      // Send token to server for verification
      fetch('/api/verify-turnstile', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: token })
      })
    }
  })
})
