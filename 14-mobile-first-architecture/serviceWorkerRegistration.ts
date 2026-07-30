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
 * serviceWorkerRegistration.ts — PWA Service Worker Registration
 *
 * Registers the casino service worker with update detection and
 * user-facing notification when a new version is available.
 *
 * Chapter 14 — Mobile-First Architecture for iGaming
 */

export interface SwRegistrationOptions {
  swUrl?: string;
  onSuccess?: (registration: ServiceWorkerRegistration) => void;
  onUpdate?: (registration: ServiceWorkerRegistration) => void;
  onError?: (error: Error) => void;
}

export async function registerServiceWorker(
  options: SwRegistrationOptions = {}
): Promise<ServiceWorkerRegistration | null> {
  if (!('serviceWorker' in navigator)) {
    console.info('Service Worker not supported on this browser');
    return null;
  }

  const swUrl = options.swUrl ?? '/sw.js';

  try {
    const registration = await navigator.serviceWorker.register(swUrl, {
      scope: '/',
      updateViaCache: 'none', // Always re-validate SW on each visit
    });

    // Handle initial installation
    if (registration.installing) {
      console.info('Service Worker: installing');
      trackInstalling(registration.installing, registration, options);
    } else if (registration.waiting) {
      console.info('Service Worker: waiting (update available)');
      options.onUpdate?.(registration);
    } else if (registration.active) {
      console.info('Service Worker: active');
      options.onSuccess?.(registration);
    }

    // Listen for future updates
    registration.addEventListener('updatefound', () => {
      const newWorker = registration.installing;
      if (newWorker) {
        trackInstalling(newWorker, registration, options);
      }
    });

    // Check for updates every 60 seconds (catches background tabs)
    setInterval(() => {
      registration.update().catch(() => {});
    }, 60_000);

    // Re-check on visibility change (player returns from another tab)
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') {
        registration.update().catch(() => {});
      }
    });

    return registration;
  } catch (error) {
    const err = error instanceof Error ? error : new Error(String(error));
    console.error('Service Worker registration failed:', err);
    options.onError?.(err);
    return null;
  }
}

function trackInstalling(
  worker: ServiceWorker,
  registration: ServiceWorkerRegistration,
  options: SwRegistrationOptions
): void {
  worker.addEventListener('statechange', () => {
    if (worker.state === 'installed') {
      if (navigator.serviceWorker.controller) {
        // Update available — notify the UI
        console.info('Service Worker: new version available');
        options.onUpdate?.(registration);
        dispatchUpdateEvent(registration);
      } else {
        // First install
        console.info('Service Worker: content cached for offline use');
        options.onSuccess?.(registration);
      }
    }
  });
}

function dispatchUpdateEvent(registration: ServiceWorkerRegistration): void {
  window.dispatchEvent(new CustomEvent('sw:update-available', {
    detail: { registration },
    bubbles: true,
  }));
}

/**
 * Activate waiting service worker immediately.
 * Call this when the user accepts the "update available" prompt.
 */
export function activateWaitingWorker(registration: ServiceWorkerRegistration): void {
  const waiting = registration.waiting;
  if (waiting) {
    waiting.postMessage({ type: 'SKIP_WAITING' });
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      window.location.reload();
    });
  }
}

export async function unregisterServiceWorker(): Promise<boolean> {
  if (!('serviceWorker' in navigator)) return false;

  const registrations = await navigator.serviceWorker.getRegistrations();
  await Promise.all(registrations.map((r) => r.unregister()));
  return true;
}
