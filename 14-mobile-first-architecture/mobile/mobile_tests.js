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
 * Mobile Testing Suite
 *
 * Comprehensive Detox/Jest test suite for the mobile casino application covering:
 * - Offline bet queuing and sync verification
 * - Performance tests under 3G network conditions (load time < 2s)
 * - Security tests for rooted device detection and data encryption
 *
 * Reference: Chapter 7 - Mobile Testing Strategy section
 */

describe('Mobile Casino App', () => {
  describe('Offline Functionality', () => {
    it('should queue bets when offline', async () => {
      // Simulate offline condition
      await device.setConnectivity(false);

      // Place bet
      await element(by.id('bet-button')).tap();
      await element(by.id('bet-amount')).typeText('10');
      await element(by.id('confirm-bet')).tap();

      // Verify bet is queued
      const queuedBets = await getQueuedBets();
      expect(queuedBets).toHaveLength(1);
      expect(queuedBets[0].amount).toBe(10);
    });

    it('should sync queued bets when online', async () => {
      // Place bet while offline
      await device.setConnectivity(false);
      await placeBet(10);

      // Go online
      await device.setConnectivity(true);

      // Wait for sync
      await waitFor(element(by.text('Bet placed successfully')))
        .toBeVisible()
        .withTimeout(10000);

      // Verify balance updated
      const balance = await getBalance();
      expect(balance).toBe(initialBalance - 10);
    });
  });

  describe('Performance Tests', () => {
    it('should load game in under 2 seconds on 3G', async () => {
      // Network condition simulation
      device.setNetworkConditions({
        latency: 150,
        throughput: 780, // 3G speed
      });

      const startTime = Date.now();

      // Navigate to game
      await element(by.id('roulette-game')).tap();

      // Wait for game to load
      await waitFor(element(by.id('game-loaded')))
        .toBeVisible()
        .withTimeout(5000);

      const loadTime = Date.now() - startTime;
      expect(loadTime).toBeLessThan(2000);
    });
  });

  describe('Security Tests', () => {
    it('should detect rooted device', async () => {
      // Mock rooted device
      mockDeviceRooted(true);

      // App should refuse to start
      await expect(element(by.id('security-error')))
        .toBeVisible();
    });

    it('should encrypt sensitive data', async () => {
      const sensitiveData = 'user-password-123';

      // Enter sensitive data
      await element(by.id('password-input')).typeText(sensitiveData);

      // Verify encryption in storage
      const storedData = await getStoredData('password');
      expect(storedData).not.toBe(sensitiveData);
      expect(isEncrypted(storedData)).toBe(true);
    });
  });
});
