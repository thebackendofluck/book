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
 * brand-config.ts — Multi-Brand Casino Configuration
 *
 * Defines the per-brand configuration for white-label casino deployments.
 * Each brand can override default behaviour for jurisdiction, theming,
 * feature flags, CDN, and compliance requirements.
 *
 * Used by: game-loader.ts, cdn-asset-resolver.js, serviceWorkerRegistration.ts
 *
 * Chapter 14 — Mobile-First Architecture for iGaming
 */

export interface JurisdictionConfig {
  code: string;          // ISO 3166-1 alpha-2 or state code (e.g. 'GB', 'US-NJ')
  licenseNumber: string;
  selfExclusionScheme: string;
  depositLimitMandatory: boolean;
  realityCheckIntervalMinutes: number;
  maxBonusWageringMultiplier: number | null;
  rtpDisplayRequired: boolean;
  pixOnly?: boolean;             // Brazil: only PIX payments
  cpfRequired?: boolean;         // Brazil: CPF number required
}

export interface CdnConfig {
  primary: string;
  secondary: string;
  imageBaseUrl: string;
  gameAssetsBaseUrl: string;
  cacheControlStatic: string;
  cacheControlDynamic: string;
}

export interface PushNotificationConfig {
  enabled: boolean;
  provider: 'onesignal' | 'firebase' | 'none';
  appId?: string;
  dailyCap: number;           // Max push notifications per day
  cooldownHours: number;      // Minimum hours between push notifications
  rgTriggerEnabled: boolean;  // Enable responsible gambling push triggers
}

export interface FeatureFlags {
  liveCasino: boolean;
  sportsBook: boolean;
  poker: boolean;
  virtualSports: boolean;
  bingo: boolean;
  cryptoPayments: boolean;
  pwaInstallPrompt: boolean;
  biometricAuth: boolean;
  realityCheck: boolean;
  affordabilityChecks: boolean;
}

export interface BrandConfig {
  brandId: string;
  brandName: string;
  host: string;
  homeUrl: string;
  userAgent: string;   // Custom UA to identify native shell to web frontend
  jurisdiction: JurisdictionConfig;
  cdn: CdnConfig;
  push: PushNotificationConfig;
  features: FeatureFlags;
  theme: {
    primaryColor: string;
    accentColor: string;
    logoUrl: string;
    faviconUrl: string;
    splashImageUrl: string;
  };
  webView: {
    clearCacheOnStartup: boolean;
    splashTimeoutMs: number;
    openExternalUrlsInBrowser: boolean;
    enableJavascript: boolean;
    enableDomStorage: boolean;
    enableGeolocation: boolean;
  };
}

// ---------------------------------------------------------------------------
// Brand registry
// ---------------------------------------------------------------------------

const BRAND_CONFIGS: Record<string, BrandConfig> = {
  acmetocasino: {
    brandId: 'acmetocasino',
    brandName: 'AcmeToCasino',
    host: 'www.acmetocasino.com',
    homeUrl: 'https://www.acmetocasino.com',
    userAgent: '[NativeApp - AcmeToCasino]',
    jurisdiction: {
      code: 'MT',
      licenseNumber: 'MGA/B2C/000/2024',
      selfExclusionScheme: 'MGA Self-Exclusion',
      depositLimitMandatory: false,
      realityCheckIntervalMinutes: 60,
      maxBonusWageringMultiplier: null,
      rtpDisplayRequired: true,
    },
    cdn: {
      primary: 'https://cdn.acmetocasino.com',
      secondary: 'https://cdn2.acmetocasino.com',
      imageBaseUrl: 'https://cdn.acmetocasino.com/images',
      gameAssetsBaseUrl: 'https://cdn.acmetocasino.com/games',
      cacheControlStatic: 'public, max-age=31536000, immutable',
      cacheControlDynamic: 'public, max-age=300, stale-while-revalidate=60',
    },
    push: {
      enabled: true,
      provider: 'onesignal',
      appId: process.env.ONESIGNAL_APP_ID || '',
      dailyCap: 3,
      cooldownHours: 8,
      rgTriggerEnabled: true,
    },
    features: {
      liveCasino: true,
      sportsBook: true,
      poker: false,
      virtualSports: true,
      bingo: false,
      cryptoPayments: false,
      pwaInstallPrompt: true,
      biometricAuth: true,
      realityCheck: true,
      affordabilityChecks: false,
    },
    theme: {
      primaryColor: '#1a237e',
      accentColor: '#ffb300',
      logoUrl: '/images/logo.svg',
      faviconUrl: '/images/favicon.ico',
      splashImageUrl: '/images/splash.png',
    },
    webView: {
      clearCacheOnStartup: true,
      splashTimeoutMs: 1800,
      openExternalUrlsInBrowser: true,
      enableJavascript: true,
      enableDomStorage: true,
      enableGeolocation: true,
    },
  },

  'acmetocasino-uk': {
    brandId: 'acmetocasino-uk',
    brandName: 'AcmeToCasino UK',
    host: 'www.acmetocasino.co.uk',
    homeUrl: 'https://www.acmetocasino.co.uk',
    userAgent: '[NativeApp - AcmeToCasinoUK]',
    jurisdiction: {
      code: 'GB',
      licenseNumber: '000-123456-R-123456-001',
      selfExclusionScheme: 'GamStop',
      depositLimitMandatory: true,  // UK: deposit limits required before first deposit (Oct 2025)
      realityCheckIntervalMinutes: 60,
      maxBonusWageringMultiplier: 10,
      rtpDisplayRequired: true,
    },
    cdn: {
      primary: 'https://cdn.acmetocasino.co.uk',
      secondary: 'https://cdn2.acmetocasino.co.uk',
      imageBaseUrl: 'https://cdn.acmetocasino.co.uk/images',
      gameAssetsBaseUrl: 'https://cdn.acmetocasino.co.uk/games',
      cacheControlStatic: 'public, max-age=31536000, immutable',
      cacheControlDynamic: 'public, max-age=60, stale-while-revalidate=30',
    },
    push: {
      enabled: true,
      provider: 'firebase',
      dailyCap: 2,  // UK: tighter cap
      cooldownHours: 12,
      rgTriggerEnabled: true,
    },
    features: {
      liveCasino: true,
      sportsBook: true,
      poker: false,
      virtualSports: true,
      bingo: true,
      cryptoPayments: false,
      pwaInstallPrompt: true,
      biometricAuth: true,
      realityCheck: true,
      affordabilityChecks: true,  // UK: mandatory
    },
    theme: {
      primaryColor: '#1a237e',
      accentColor: '#ffb300',
      logoUrl: '/images/logo-uk.svg',
      faviconUrl: '/images/favicon.ico',
      splashImageUrl: '/images/splash-uk.png',
    },
    webView: {
      clearCacheOnStartup: true,
      splashTimeoutMs: 1800,
      openExternalUrlsInBrowser: true,
      enableJavascript: true,
      enableDomStorage: true,
      enableGeolocation: true,
    },
  },
};

// ---------------------------------------------------------------------------
// Config accessor
// ---------------------------------------------------------------------------

export function getBrandConfig(brandId: string): BrandConfig {
  const config = BRAND_CONFIGS[brandId];
  if (!config) {
    throw new Error(`Unknown brand: ${brandId}`);
  }
  return config;
}

export function getBrandConfigByHost(host: string): BrandConfig | null {
  return Object.values(BRAND_CONFIGS).find((c) => c.host === host) ?? null;
}

export function listBrands(): string[] {
  return Object.keys(BRAND_CONFIGS);
}

export { BRAND_CONFIGS };
