// Companion code for "The Backend of Luck" - Chapter 32, Testing and QA in Gambling.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// E2E Registration Tests for AcmetoCasino Frontend
// Validates the full player registration form flow
describe('AcmetoCasino Registration Tests', function() {

  it('should complete player registration', function() {
    browser.get('http://localhost:9001/register');

    // Gender selection
    var select = element(by.name('gender'));
    select.$('[value="female"]').click();

    // Personal information
    element(by.name('first-name')).clear();
    element(by.name('first-name')).sendKeys('Jane');

    element(by.name('last-name')).clear();
    element(by.name('last-name')).sendKeys('TestPlayer');

    element(by.name('email')).clear();
    element(by.name('email')).sendKeys('jane.test@example.com');

    // Address fields
    element(by.name('address-line-1')).clear();
    element(by.name('address-line-1')).sendKeys('123 Test Street');

    element(by.name('address-line-2')).clear();
    element(by.name('address-line-2')).sendKeys('Suite 456');

    element(by.name('city-town')).clear();
    element(by.name('city-town')).sendKeys('London');

    element(by.name('postcode')).clear();
    element(by.name('postcode')).sendKeys('SW1A 1AA');

    element(by.name('phone')).clear();
    element(by.name('phone')).sendKeys('07700900000');

    // Account credentials
    element(by.name('username')).clear();
    element(by.name('username')).sendKeys('janetestplayer');

    element(by.name('password')).clear();
    element(by.name('password')).sendKeys('SecurePass123!');

    element(by.name('confirm-password')).clear();
    element(by.name('confirm-password')).sendKeys('SecurePass123!');

    // Accept terms and conditions
    element(by.name('accept-terms')).click();

    // Submit registration form
    element(by.css('body > my-app > div > register > form > button')).click();
  });

});
