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
 * push-notification-manager.ts — Responsible Gaming-Aware Push Notifications
 *
 * Manages push notification subscription, permission handling, and
 * responsible gambling safeguards including daily caps, cooldowns, and
 * player-configurable quiet hours.
 *
 * Chapter 14 — Mobile-First Architecture for iGaming
 */

export interface PushPreferences {
  enabled: boolean;
  dailyCap: number;           // Max push per day (default: 3)
  cooldownHours: number;      // Hours between pushes (default: 8)
  quietHoursEnabled: boolean;
  quietHoursStart: number;    // Hour 0-23
  quietHoursEnd: number;
  categories: {
    promotions: boolean;
    rgReminders: boolean;     // Responsible gaming reminders (cannot be disabled)
    accountAlerts: boolean;
    gameUpdates: boolean;
  };
}

const DEFAULT_PREFERENCES: PushPreferences = {
  enabled: true,
  dailyCap: 3,
  cooldownHours: 8,
  quietHoursEnabled: false,
  quietHoursStart: 22,
  quietHoursEnd: 8,
  categories: {
    promotions: true,
    rgReminders: true,      // Always on — regulatory requirement
    accountAlerts: true,
    gameUpdates: false,
  },
};

const STORAGE_KEY = 'push_preferences';
const DAILY_COUNT_KEY = 'push_daily_count';
const LAST_PUSH_KEY = 'push_last_sent';

export class PushNotificationManager {
  private readonly vapidPublicKey: string;
  private readonly apiBaseUrl: string;
  private swRegistration: ServiceWorkerRegistration | null = null;
  private preferences: PushPreferences;

  constructor(options: { vapidPublicKey: string; apiBaseUrl: string }) {
    this.vapidPublicKey = options.vapidPublicKey;
    this.apiBaseUrl = options.apiBaseUrl;
    this.preferences = this.loadPreferences();
  }

  // ---------------------------------------------------------------------------
  // Subscription lifecycle
  // ---------------------------------------------------------------------------

  async subscribe(): Promise<PushSubscription | null> {
    if (!this.isSupported()) {
      console.info('Push notifications not supported');
      return null;
    }

    const permission = await Notification.requestPermission();
    if (permission !== 'granted') {
      console.info('Push notification permission denied');
      return null;
    }

    this.swRegistration = await navigator.serviceWorker.ready;
    const existing = await this.swRegistration.pushManager.getSubscription();
    if (existing) return existing;

    const subscription = await this.swRegistration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: this.urlBase64ToUint8Array(this.vapidPublicKey),
    });

    await this.registerSubscription(subscription);
    return subscription;
  }

  async unsubscribe(): Promise<boolean> {
    const reg = await navigator.serviceWorker.ready;
    const subscription = await reg.pushManager.getSubscription();
    if (!subscription) return true;

    const result = await subscription.unsubscribe();
    if (result) {
      await this.deregisterSubscription(subscription);
    }
    return result;
  }

  // ---------------------------------------------------------------------------
  // Preferences
  // ---------------------------------------------------------------------------

  getPreferences(): PushPreferences {
    return { ...this.preferences };
  }

  async updatePreferences(updates: Partial<PushPreferences>): Promise<void> {
    // rgReminders cannot be disabled
    if (updates.categories) {
      updates.categories.rgReminders = true;
    }

    this.preferences = { ...this.preferences, ...updates };
    this.savePreferences();

    // Sync to server
    await fetch(`${this.apiBaseUrl}/api/v1/account/push-preferences`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(this.preferences),
    }).catch(() => {});
  }

  // ---------------------------------------------------------------------------
  // Rate limiting checks (called by server-side push sender)
  // ---------------------------------------------------------------------------

  canSendPush(category: keyof PushPreferences['categories']): boolean {
    if (!this.preferences.enabled) return false;
    if (category !== 'rgReminders' && !this.preferences.categories[category]) return false;

    if (category !== 'rgReminders') {
      if (this.isDailyCapReached()) return false;
      if (this.isCooldownActive()) return false;
      if (this.isQuietHours()) return false;
    }

    return true;
  }

  recordPushSent(): void {
    const today = new Date().toDateString();
    const stored = JSON.parse(localStorage.getItem(DAILY_COUNT_KEY) || '{}') as Record<string, number>;
    stored[today] = (stored[today] || 0) + 1;
    localStorage.setItem(DAILY_COUNT_KEY, JSON.stringify(stored));
    localStorage.setItem(LAST_PUSH_KEY, String(Date.now()));
  }

  // ---------------------------------------------------------------------------
  // Internal
  // ---------------------------------------------------------------------------

  private isDailyCapReached(): boolean {
    const today = new Date().toDateString();
    const counts = JSON.parse(localStorage.getItem(DAILY_COUNT_KEY) || '{}') as Record<string, number>;
    return (counts[today] || 0) >= this.preferences.dailyCap;
  }

  private isCooldownActive(): boolean {
    const lastPush = parseInt(localStorage.getItem(LAST_PUSH_KEY) || '0', 10);
    const cooldownMs = this.preferences.cooldownHours * 3600_000;
    return Date.now() - lastPush < cooldownMs;
  }

  private isQuietHours(): boolean {
    if (!this.preferences.quietHoursEnabled) return false;
    const hour = new Date().getHours();
    const { quietHoursStart: start, quietHoursEnd: end } = this.preferences;
    return start > end
      ? hour >= start || hour < end  // Wraps midnight
      : hour >= start && hour < end;
  }

  private async registerSubscription(subscription: PushSubscription): Promise<void> {
    await fetch(`${this.apiBaseUrl}/api/v1/push/subscribe`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(subscription.toJSON()),
    });
  }

  private async deregisterSubscription(subscription: PushSubscription): Promise<void> {
    await fetch(`${this.apiBaseUrl}/api/v1/push/unsubscribe`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ endpoint: subscription.endpoint }),
    });
  }

  private isSupported(): boolean {
    return (
      'serviceWorker' in navigator &&
      'PushManager' in window &&
      'Notification' in window
    );
  }

  private loadPreferences(): PushPreferences {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      return stored ? { ...DEFAULT_PREFERENCES, ...JSON.parse(stored) as PushPreferences } : { ...DEFAULT_PREFERENCES };
    } catch {
      return { ...DEFAULT_PREFERENCES };
    }
  }

  private savePreferences(): void {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(this.preferences));
  }

  private urlBase64ToUint8Array(base64String: string): Uint8Array {
    const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const rawData = atob(base64);
    return Uint8Array.from([...rawData].map((c) => c.charCodeAt(0)));
  }
}
