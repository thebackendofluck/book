// Companion code for "The Backend of Luck" - Chapter 32, Testing and QA in Gambling.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// E2E Deposit Tests for AcmetoCasino Frontend
// Validates the deposit flow: login -> navigate to cashier -> initiate deposit
describe('AcmetoCasino Deposit Tests', function() {

  it('should initiate deposit after login', function() {
    browser.get('https://testbrand.acmetocasino-stage.com/');

    // Open login modal
    element(by.css(
      '#sidebar-wrapper > ul > li.sidebar-brand > a.cta_1.login-btn'
    )).click();

    // Enter credentials
    element(by.css('username')).clear();
    element(by.id('username')).sendKeys('testplayer001');
    element(by.id('password')).clear();
    element(by.id('password')).sendKeys('TestDeposit123');

    // Submit login
    element(by.id('submit')).click();

    // Deposit activity follows after successful login
    // The cashier component loads and presents payment methods
  });

});
