// Companion code for "The Backend of Luck" - Chapter 32, Testing and QA in Gambling.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// spec.js
// Protractor E2E test specification for casino platform
// Tests critical player journeys: login, registration, deposit, game search
// Part of the early-stage E2E test suite referenced in Chapter 32

'use strict';

const { browser, by, element, ExpectedConditions } = require('protractor');

// Test configuration
const BASE_URL = process.env.TEST_BASE_URL || 'https://staging.acmetocasino.com';
const DEFAULT_TIMEOUT = 10000;
const VALID_USERNAME = process.env.TEST_USERNAME || 'e2e_test_player';
const VALID_PASSWORD = process.env.TEST_PASSWORD || 'E2eTestPass123!';

describe('Casino Platform E2E Tests', function () {

  beforeAll(function () {
    browser.waitForAngularEnabled(true);
    browser.get(BASE_URL);
  });

  afterEach(function () {
    // Take screenshot on failure for CI debugging
    browser.takeScreenshot().then(function (png) {
      const stream = require('fs').createWriteStream(`screenshots/failure-${Date.now()}.png`);
      stream.write(Buffer.from(png, 'base64'));
      stream.end();
    });
  });

  // ============================================================
  // LOGIN TESTS
  // ============================================================
  describe('Login Flow', function () {

    beforeEach(function () {
      browser.get(`${BASE_URL}/login`);
    });

    it('should login successfully with valid credentials', function () {
      element(by.id('username')).sendKeys(VALID_USERNAME);
      element(by.id('password')).sendKeys(VALID_PASSWORD);
      element(by.id('login-button')).click();

      browser.wait(
        ExpectedConditions.urlContains('/lobby'),
        DEFAULT_TIMEOUT,
        'Should redirect to lobby after successful login'
      );

      expect(browser.getCurrentUrl()).toContain('/lobby');
      expect(element(by.css('[data-testid="player-balance"]')).isPresent()).toBe(true);
    });

    it('should show error message with invalid password', function () {
      element(by.id('username')).sendKeys(VALID_USERNAME);
      element(by.id('password')).sendKeys('WrongPassword123!');
      element(by.id('login-button')).click();

      browser.wait(
        ExpectedConditions.visibilityOf(element(by.css('.login-error'))),
        DEFAULT_TIMEOUT,
        'Error message should be visible'
      );

      expect(element(by.css('.login-error')).getText()).toContain('Invalid username or password');
      expect(browser.getCurrentUrl()).not.toContain('/lobby');
    });

    it('should show error for non-existent username', function () {
      element(by.id('username')).sendKeys('nonexistent_player_xyz');
      element(by.id('password')).sendKeys('AnyPassword123!');
      element(by.id('login-button')).click();

      const errorEl = element(by.css('[data-testid="login-error"]'));
      browser.wait(ExpectedConditions.visibilityOf(errorEl), DEFAULT_TIMEOUT);
      expect(errorEl.isDisplayed()).toBe(true);
    });

    it('should block after 5 failed login attempts', function () {
      const attempts = [1, 2, 3, 4, 5];
      attempts.forEach(function () {
        browser.get(`${BASE_URL}/login`);
        element(by.id('username')).sendKeys(VALID_USERNAME);
        element(by.id('password')).sendKeys('WrongPassword!');
        element(by.id('login-button')).click();
      });

      const lockoutMessage = element(by.css('[data-testid="account-locked-message"]'));
      browser.wait(ExpectedConditions.visibilityOf(lockoutMessage), DEFAULT_TIMEOUT);
      expect(lockoutMessage.isDisplayed()).toBe(true);
    });
  });

  // ============================================================
  // REGISTRATION TESTS
  // ============================================================
  describe('Registration Flow', function () {

    const testUser = {
      username: `e2e_reg_${Date.now()}`,
      email: `e2e_${Date.now()}@test.acmetocasino.com`,
      password: (process.env.LOADTEST_PASSWORD || 'loadtest-user'),
      firstName: 'E2E',
      lastName: 'Tester',
      dateOfBirth: '01/15/1990',
      gender: 'male',
      addressLine1: '123 Test Street',
      city: 'Atlantic City',
      state: 'NJ',
      zipCode: '08401',
      currency: 'USD',
      acceptTerms: true
    };

    beforeEach(function () {
      browser.get(`${BASE_URL}/register`);
    });

    it('should successfully register a new player', function () {
      // Fill all 12 registration fields
      element(by.id('reg-username')).sendKeys(testUser.username);
      element(by.id('reg-email')).sendKeys(testUser.email);
      element(by.id('reg-password')).sendKeys(testUser.password);
      element(by.id('reg-first-name')).sendKeys(testUser.firstName);
      element(by.id('reg-last-name')).sendKeys(testUser.lastName);
      element(by.id('reg-date-of-birth')).sendKeys(testUser.dateOfBirth);

      // Gender dropdown
      element(by.css('select#reg-gender')).sendKeys('Male');

      element(by.id('reg-address')).sendKeys(testUser.addressLine1);
      element(by.id('reg-city')).sendKeys(testUser.city);
      element(by.id('reg-state')).sendKeys(testUser.state);
      element(by.id('reg-zip')).sendKeys(testUser.zipCode);

      // Accept terms and conditions
      element(by.id('reg-terms')).click();

      element(by.id('reg-submit')).click();

      browser.wait(
        ExpectedConditions.urlContains('/registration-success'),
        DEFAULT_TIMEOUT * 2,
        'Should redirect to success page'
      );

      expect(browser.getCurrentUrl()).toContain('/registration-success');
    });

    it('should validate age requirement (must be 21+ in NJ)', function () {
      element(by.id('reg-date-of-birth')).sendKeys('01/15/2010');
      element(by.id('reg-submit')).click();

      const ageError = element(by.css('[data-testid="age-error"]'));
      browser.wait(ExpectedConditions.visibilityOf(ageError), DEFAULT_TIMEOUT);
      expect(ageError.getText()).toContain('must be 21 or older');
    });
  });

  // ============================================================
  // DEPOSIT FLOW TESTS
  // ============================================================
  describe('Deposit Flow', function () {

    beforeAll(function () {
      // Login before deposit tests
      browser.get(`${BASE_URL}/login`);
      element(by.id('username')).sendKeys(VALID_USERNAME);
      element(by.id('password')).sendKeys(VALID_PASSWORD);
      element(by.id('login-button')).click();
      browser.wait(ExpectedConditions.urlContains('/lobby'), DEFAULT_TIMEOUT);
    });

    it('should navigate to cashier from sidebar login button', function () {
      // Click the cashier/deposit button
      element(by.css('[data-testid="deposit-button"]')).click();

      browser.wait(
        ExpectedConditions.urlContains('/cashier'),
        DEFAULT_TIMEOUT
      );

      expect(browser.getCurrentUrl()).toContain('/cashier');
    });

    it('should show deposit methods on cashier page', function () {
      browser.get(`${BASE_URL}/cashier/deposit`);

      browser.wait(
        ExpectedConditions.visibilityOf(element(by.css('[data-testid="payment-methods"]'))),
        DEFAULT_TIMEOUT
      );

      // Should show Visa/Mastercard options (credit cards disabled in NJ for online gambling)
      expect(element(by.css('[data-testid="payment-method-visa"]')).isPresent()).toBe(true);
      expect(element(by.css('[data-testid="payment-method-paypal"]')).isPresent()).toBe(true);
    });
  });

  // ============================================================
  // GAME SEARCH AND LAUNCH TESTS
  // ============================================================
  describe('Game Search and Launch', function () {

    beforeAll(function () {
      browser.get(`${BASE_URL}/login`);
      element(by.id('username')).sendKeys(VALID_USERNAME);
      element(by.id('password')).sendKeys(VALID_PASSWORD);
      element(by.id('login-button')).click();
      browser.wait(ExpectedConditions.urlContains('/lobby'), DEFAULT_TIMEOUT);
    });

    it('should find Sweet Bonanza via search', function () {
      element(by.css('[data-testid="game-search-input"]')).sendKeys('Sweet Bonanza');

      browser.wait(
        ExpectedConditions.visibilityOf(element(by.css('[data-testid="search-results"]'))),
        DEFAULT_TIMEOUT
      );

      const firstResult = element(by.css('[data-testid="game-card"]:first-child'));
      expect(firstResult.isDisplayed()).toBe(true);
      expect(firstResult.getText()).toContain('Sweet Bonanza');
    });

    it('should launch game in play-for-fun mode', function () {
      element(by.css('[data-testid="game-search-input"]')).sendKeys('Sweet Bonanza');
      browser.wait(
        ExpectedConditions.visibilityOf(element(by.css('[data-testid="play-for-fun-button"]'))),
        DEFAULT_TIMEOUT
      );

      element(by.css('[data-testid="play-for-fun-button"]')).click();

      const newWindowHandle = browser.getAllWindowHandles().then(function (handles) {
        return handles[handles.length - 1];
      });

      browser.switchTo().window(newWindowHandle);

      // Verify game URL contains expected parameters
      expect(browser.getCurrentUrl()).toContain('mode=demo');
    });

    it('should launch game in play-for-real mode', function () {
      browser.get(`${BASE_URL}/lobby`);
      element(by.css('[data-testid="game-search-input"]')).sendKeys('Sweet Bonanza');
      browser.wait(
        ExpectedConditions.visibilityOf(element(by.css('[data-testid="play-for-real-button"]'))),
        DEFAULT_TIMEOUT
      );

      element(by.css('[data-testid="play-for-real-button"]')).click();

      const newWindowHandle = browser.getAllWindowHandles().then(function (handles) {
        return handles[handles.length - 1];
      });

      browser.switchTo().window(newWindowHandle);

      // Verify real-money game URL
      expect(browser.getCurrentUrl()).toContain('mode=real');
      expect(browser.getCurrentUrl()).toContain('session=');
    });
  });
});
