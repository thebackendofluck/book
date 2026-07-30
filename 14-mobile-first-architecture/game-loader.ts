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
 * game-loader.ts — Adaptive Game Loader with Network-Aware Asset Loading
 *
 * Loads casino games with progressive enhancement based on network conditions.
 * Uses the Network Information API to select the appropriate asset quality tier.
 *
 * Quality tiers:
 *   - 4G/WiFi:  Full HD assets, WebGL enabled
 *   - 3G:       Medium quality assets, WebGL enabled
 *   - 2G/Slow:  Low quality assets, Canvas fallback
 *   - Offline:  Pre-cached demo mode assets only
 *
 * Chapter 14 — Mobile-First Architecture for iGaming
 */

export type NetworkTier = 'offline' | 'slow' | 'medium' | 'fast';
export type GameFormat = 'webgl' | 'canvas' | 'html5';

export interface GameManifest {
  gameId: string;
  gameName: string;
  provider: string;
  launchUrl: string;
  assets: {
    low: string;
    medium: string;
    high: string;
  };
  supportsWebGL: boolean;
  offlineDemo: boolean;
  minNetworkTier: NetworkTier;
}

export interface GameLoadOptions {
  containerId: string;
  playMode: 'real' | 'demo';
  jurisdiction: string;
  locale: string;
  onLoad?: () => void;
  onError?: (error: Error) => void;
}

export class GameLoader {
  private readonly cdnBaseUrl: string;

  constructor(cdnBaseUrl: string) {
    this.cdnBaseUrl = cdnBaseUrl;
  }

  async loadGame(manifest: GameManifest, options: GameLoadOptions): Promise<void> {
    const tier = this.detectNetworkTier();
    const format = this.selectGameFormat(manifest, tier);

    if (tier === 'offline' && !manifest.offlineDemo) {
      options.onError?.(new Error('This game requires an internet connection'));
      return;
    }

    const assetUrl = this.resolveAssetUrl(manifest, tier);
    const container = document.getElementById(options.containerId);
    if (!container) {
      options.onError?.(new Error(`Container #${options.containerId} not found`));
      return;
    }

    try {
      await this.injectGame(container, manifest, assetUrl, format, options);
      options.onLoad?.();
    } catch (err) {
      options.onError?.(err instanceof Error ? err : new Error(String(err)));
    }
  }

  detectNetworkTier(): NetworkTier {
    if (!navigator.onLine) return 'offline';

    // Network Information API (Chrome/Android)
    const connection = (navigator as { connection?: { effectiveType?: string; downlink?: number } }).connection;
    if (connection) {
      const ect = connection.effectiveType;
      if (ect === '4g') return 'fast';
      if (ect === '3g') return 'medium';
      if (ect === '2g' || ect === 'slow-2g') return 'slow';
      return 'fast'; // Unknown / wifi
    }

    return 'medium'; // Fallback
  }

  private selectGameFormat(manifest: GameManifest, tier: NetworkTier): GameFormat {
    if (!manifest.supportsWebGL) return 'html5';
    if (tier === 'slow' || tier === 'offline') return 'canvas';
    return 'webgl';
  }

  private resolveAssetUrl(manifest: GameManifest, tier: NetworkTier): string {
    const base = `${this.cdnBaseUrl}/games/${manifest.provider}/${manifest.gameId}`;
    switch (tier) {
      case 'fast': return `${base}/${manifest.assets.high}`;
      case 'medium': return `${base}/${manifest.assets.medium}`;
      default: return `${base}/${manifest.assets.low}`;
    }
  }

  private async injectGame(
    container: HTMLElement,
    manifest: GameManifest,
    assetUrl: string,
    format: GameFormat,
    options: GameLoadOptions
  ): Promise<void> {
    // Build launch URL with parameters
    const url = new URL(manifest.launchUrl);
    url.searchParams.set('mode', options.playMode);
    url.searchParams.set('jurisdiction', options.jurisdiction);
    url.searchParams.set('locale', options.locale);
    url.searchParams.set('format', format);
    url.searchParams.set('assets', assetUrl);

    const iframe = document.createElement('iframe');
    iframe.src = url.toString();
    iframe.style.cssText = 'width:100%;height:100%;border:none;';
    iframe.setAttribute('allow', 'autoplay; fullscreen; accelerometer; gyroscope');
    iframe.setAttribute('allowfullscreen', 'true');
    iframe.setAttribute('title', manifest.gameName);
    iframe.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-forms allow-popups');

    container.appendChild(iframe);

    await new Promise<void>((resolve, reject) => {
      iframe.onload = () => resolve();
      iframe.onerror = () => reject(new Error('Game iframe failed to load'));
      setTimeout(() => reject(new Error('Game load timeout')), 30_000);
    });
  }
}
