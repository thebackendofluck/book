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
 * Chapter 7: Mobile-First Architecture for iGaming
 * Network Optimizer
 *
 * Network-aware resource loading strategy for gambling PWAs operating on
 * low-bandwidth connections (2G/3G). Detects effective connection type and
 * loads the minimal required resources to ensure playability on slow networks.
 * Uses WebP image format with automatic fallback and lazy loading.
 *
 * Reference: Chapter 7 - Performance Optimization for 2G/3G Networks section
 */

class NetworkOptimizer {
  constructor() {
    this.connection = navigator.connection || {};
    this.effectiveType = this.connection.effectiveType || '4g';
  }

  async optimizeResources() {
    switch (this.effectiveType) {
      case 'slow-2g':
        await this.loadMinimalResources();
        break;
      case '2g':
        await this.loadBasicResources();
        break;
      case '3g':
        await this.loadStandardResources();
        break;
      default:
        await this.loadFullResources();
    }
  }

  async loadMinimalResources() {
    // Load only critical CSS
    await this.loadCriticalCSS();

    // Defer non-critical images
    this.deferImages();

    // Use WebP with fallbacks
    this.optimizeImages();

    // Minimize JavaScript bundles
    this.loadEssentialJS();
  }

  _isSafeUrl(url) {
    try {
      const parsed = new URL(url, location.href);
      return parsed.protocol === 'https:' || parsed.protocol === 'http:';
    } catch (_) {
      return false;
    }
  }

  optimizeImages() {
    const images = document.querySelectorAll('img[data-src]');
    images.forEach(img => {
      const src = img.getAttribute('data-src');
      // Validate URL using URL constructor to prevent javascript: and other unsafe schemes.
      if (!src || !this._isSafeUrl(src)) return;
      const webpSrc = src.replace(/\.(jpg|png)/, '.webp');

      // Check WebP support
      if (this.supportsWebP()) {
        img.setAttribute('src', webpSrc);
      } else {
        img.setAttribute('src', src);
      }

      // Lazy loading
      img.setAttribute('loading', 'lazy');
    });
  }

  supportsWebP() {
    const canvas = document.createElement('canvas');
    canvas.width = 1;
    canvas.height = 1;
    return canvas.toDataURL('image/webp').indexOf('webp') > -1;
  }
}
