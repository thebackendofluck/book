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
 * cdn-asset-resolver.js — CDN URL Resolver with Client Hints Support
 *
 * Resolves asset URLs based on:
 *   - Network tier (2G/3G/4G/WiFi)
 *   - Device pixel ratio (DPR)
 *   - Viewport width
 *   - Browser format support (AVIF > WebP > JPEG)
 *   - Jurisdiction (data residency constraints)
 *
 * Sends Client Hints headers (ECT, DPR, Viewport-Width) to origin so the
 * CDN edge can tailor image responses without JavaScript.
 *
 * Chapter 14 — Mobile-First Architecture for iGaming
 */

'use strict';

const CDN_ENDPOINTS = {
  EU: {
    primary: process.env.CDN_EU_PRIMARY || 'https://cdn.acmetocasino.com',
    secondary: process.env.CDN_EU_SECONDARY || 'https://cdn2.acmetocasino.com',
  },
  US: {
    primary: process.env.CDN_US_PRIMARY || 'https://us-cdn.acmetocasino.com',
    secondary: process.env.CDN_US_SECONDARY || 'https://us-cdn2.acmetocasino.com',
  },
  APAC: {
    primary: process.env.CDN_APAC_PRIMARY || 'https://apac-cdn.acmetocasino.com',
    secondary: process.env.CDN_APAC_SECONDARY || 'https://apac-cdn2.acmetocasino.com',
  },
};

const JURISDICTION_REGION_MAP = {
  GB: 'EU', MT: 'EU', SE: 'EU', DK: 'EU', DE: 'EU',
  'US-NJ': 'US', 'US-PA': 'US', 'US-MI': 'US',
  IN: 'APAC', JP: 'APAC', KR: 'APAC',
};

class CdnAssetResolver {
  constructor(jurisdiction = 'MT') {
    this._jurisdiction = jurisdiction;
    this._region = JURISDICTION_REGION_MAP[jurisdiction] || 'EU';
    this._format = this._detectBestFormat();
  }

  // ---------------------------------------------------------------------------
  // URL resolution
  // ---------------------------------------------------------------------------

  resolveImageUrl(path, options = {}) {
    const { width, quality, format } = {
      width: this._recommendedWidth(),
      quality: this._recommendedQuality(),
      format: this._format,
      ...options,
    };

    const cdn = this._getCdnBase();
    const params = new URLSearchParams({ w: width, q: quality, f: format });
    return `${cdn}/images${path}?${params}`;
  }

  resolveGameAssetUrl(gameId, assetPath) {
    return `${this._getCdnBase()}/games/${gameId}/${assetPath}`;
  }

  resolveStaticUrl(path) {
    return `${this._getCdnBase()}/static${path}`;
  }

  getManifestCacheControl() {
    // Live manifests need very short TTL; static assets are immutable
    return 'max-age=0, s-maxage=1, stale-while-revalidate=1';
  }

  getStaticCacheControl() {
    return 'public, max-age=31536000, immutable';
  }

  // ---------------------------------------------------------------------------
  // Client Hints
  // ---------------------------------------------------------------------------

  getClientHints() {
    const conn = navigator.connection;
    return {
      'ECT': conn?.effectiveType || '4g',
      'DPR': String(window.devicePixelRatio || 1),
      'Viewport-Width': String(window.innerWidth || 375),
    };
  }

  acceptHeader() {
    const formats = [];
    if (this._supportsAvif()) formats.push('image/avif');
    if (this._supportsWebp()) formats.push('image/webp');
    formats.push('image/jpeg', '*/*');
    return formats.join(', ');
  }

  // ---------------------------------------------------------------------------
  // Internal
  // ---------------------------------------------------------------------------

  _getCdnBase() {
    const endpoints = CDN_ENDPOINTS[this._region] || CDN_ENDPOINTS.EU;
    return endpoints.primary;
  }

  _recommendedWidth() {
    const dpr = window.devicePixelRatio || 1;
    const vw = window.innerWidth || 375;
    const conn = navigator.connection;
    const ect = conn?.effectiveType;

    // Reduce image size on slow networks
    if (ect === '2g' || ect === 'slow-2g') return Math.min(vw * dpr, 400);
    if (ect === '3g') return Math.min(vw * dpr, 800);
    return Math.min(vw * dpr, 1920);
  }

  _recommendedQuality() {
    const conn = navigator.connection;
    const ect = conn?.effectiveType;
    if (ect === '2g' || ect === 'slow-2g') return 60;
    if (ect === '3g') return 75;
    return 85;
  }

  _detectBestFormat() {
    if (this._supportsAvif()) return 'avif';
    if (this._supportsWebp()) return 'webp';
    return 'jpeg';
  }

  _supportsAvif() {
    const canvas = document.createElement('canvas');
    return canvas.toDataURL('image/avif').startsWith('data:image/avif');
  }

  _supportsWebp() {
    const canvas = document.createElement('canvas');
    return canvas.toDataURL('image/webp').startsWith('data:image/webp');
  }
}

module.exports = { CdnAssetResolver, CDN_ENDPOINTS };
