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
 * networkOptimiser.js — Network-Adaptive Resource Loading
 *
 * Detects network conditions and applies appropriate optimisations:
 *   - Fast (4G/WiFi): full quality, prefetch enabled
 *   - Medium (3G): medium quality, lazy load images
 *   - Slow (2G): minimal quality, disable non-critical resources
 *   - Offline: serve from cache only
 *
 * Uses: Network Information API, Resource Hints, Intersection Observer
 *
 * Chapter 14 — Mobile-First Architecture for iGaming
 */

'use strict';

class NetworkOptimiser {
  constructor(options = {}) {
    this._options = {
      imageSrcAttr: 'data-src',
      lazyLoadMargin: '200px',
      onTierChange: null,
      ...options,
    };
    this._tier = 'medium';
    this._observer = null;
    this._init();
  }

  // ---------------------------------------------------------------------------
  // Network tier detection
  // ---------------------------------------------------------------------------

  detectTier() {
    if (!navigator.onLine) return 'offline';

    const conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
    if (!conn) return 'medium';

    const ect = conn.effectiveType;
    if (ect === '4g') return conn.downlink > 5 ? 'fast' : 'medium';
    if (ect === '3g') return 'medium';
    if (ect === '2g' || ect === 'slow-2g') return 'slow';
    return 'fast';
  }

  getTier() { return this._tier; }

  // ---------------------------------------------------------------------------
  // Optimisation application
  // ---------------------------------------------------------------------------

  applyOptimisations(tier) {
    const prev = this._tier;
    this._tier = tier;

    document.documentElement.setAttribute('data-network', tier);

    switch (tier) {
      case 'fast':
        this._enablePrefetch();
        this._loadAllImages();
        this._enableAutoplay();
        break;

      case 'medium':
        this._disablePrefetch();
        this._lazyLoadImages();
        break;

      case 'slow':
        this._disablePrefetch();
        this._hideNonCriticalMedia();
        this._simplifyAnimations();
        break;

      case 'offline':
        this._showOfflineBanner();
        break;
    }

    if (prev !== tier && this._options.onTierChange) {
      this._options.onTierChange(tier, prev);
    }
  }

  // ---------------------------------------------------------------------------
  // Resource hints
  // ---------------------------------------------------------------------------

  _isSafeUrl(url) {
    try {
      const parsed = new URL(url, location.href);
      return parsed.protocol === 'https:' || parsed.protocol === 'http:';
    } catch (_) {
      return false;
    }
  }

  prefetchUrl(url) {
    if (this._tier !== 'fast') return;
    if (!this._isSafeUrl(url)) return;
    const existing = Array.from(document.head.querySelectorAll('link[rel="prefetch"]'))
      .find(function(el) { return el.getAttribute('href') === url; });
    if (existing) return;

    const link = document.createElement('link');
    link.rel = 'prefetch';
    link.href = url;
    document.head.appendChild(link);
  }

  preloadUrl(url, as) {
    if (!this._isSafeUrl(url)) return;
    const link = document.createElement('link');
    link.rel = 'preload';
    link.href = url;
    link.as = as;
    if (as === 'font') link.crossOrigin = 'anonymous';
    document.head.appendChild(link);
  }

  // ---------------------------------------------------------------------------
  // Image optimisation
  // ---------------------------------------------------------------------------

  _loadAllImages() {
    if (this._observer) { this._observer.disconnect(); this._observer = null; }
    document.querySelectorAll(`img[${this._options.imageSrcAttr}]`).forEach((img) => {
      const src = img.getAttribute(this._options.imageSrcAttr);
      if (src && this._isSafeUrl(src)) { img.src = src; }
    });
  }

  _lazyLoadImages() {
    if (!('IntersectionObserver' in window)) {
      this._loadAllImages();
      return;
    }

    this._observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            const img = entry.target;
            const src = img.getAttribute(this._options.imageSrcAttr);
            if (src && this._isSafeUrl(src)) { img.src = src; }
            this._observer.unobserve(img);
          }
        });
      },
      { rootMargin: this._options.lazyLoadMargin }
    );

    document.querySelectorAll(`img[${this._options.imageSrcAttr}]`).forEach((img) => {
      this._observer.observe(img);
    });
  }

  _hideNonCriticalMedia() {
    document.querySelectorAll('[data-network-tier="fast"]').forEach((el) => {
      el.style.display = 'none';
    });
    document.querySelectorAll('video[autoplay]').forEach((v) => {
      v.pause();
      v.removeAttribute('autoplay');
    });
  }

  // ---------------------------------------------------------------------------
  // Animation / autoplay controls
  // ---------------------------------------------------------------------------

  _enableAutoplay() {
    document.querySelectorAll('video[data-autoplay="true"]').forEach((v) => {
      v.play().catch(() => {});
    });
  }

  _simplifyAnimations() {
    const style = document.createElement('style');
    style.id = 'network-slow-override';
    style.textContent = '*, *::before, *::after { animation: none !important; transition: none !important; }';
    if (!document.getElementById('network-slow-override')) {
      document.head.appendChild(style);
    }
  }

  _enablePrefetch() {
    document.querySelectorAll('[data-prefetch]').forEach((el) => {
      this.prefetchUrl(el.getAttribute('data-prefetch'));
    });
  }

  _disablePrefetch() {
    document.querySelectorAll('link[rel="prefetch"]').forEach((link) => link.remove());
  }

  // ---------------------------------------------------------------------------
  // Offline banner
  // ---------------------------------------------------------------------------

  _showOfflineBanner() {
    if (document.getElementById('offline-banner')) return;
    const banner = document.createElement('div');
    banner.id = 'offline-banner';
    banner.textContent = 'You are offline. Some features may be unavailable.';
    banner.style.cssText = [
      'position:fixed', 'top:0', 'left:0', 'width:100%',
      'background:#e74c3c', 'color:#fff', 'text-align:center',
      'padding:8px', 'z-index:99999', 'font-size:14px',
    ].join(';');
    document.body.prepend(banner);

    window.addEventListener('online', () => {
      document.getElementById('offline-banner')?.remove();
      this._init();
    }, { once: true });
  }

  // ---------------------------------------------------------------------------
  // Lifecycle
  // ---------------------------------------------------------------------------

  _init() {
    const tier = this.detectTier();
    this.applyOptimisations(tier);

    const conn = navigator.connection;
    if (conn) {
      conn.addEventListener('change', () => {
        const newTier = this.detectTier();
        if (newTier !== this._tier) this.applyOptimisations(newTier);
      });
    }

    window.addEventListener('offline', () => this.applyOptimisations('offline'));
    window.addEventListener('online', () => this.applyOptimisations(this.detectTier()));
  }
}

module.exports = { NetworkOptimiser };
