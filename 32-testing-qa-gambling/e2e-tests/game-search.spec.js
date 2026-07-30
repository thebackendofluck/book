// Companion code for "The Backend of Luck" - Chapter 32, Testing and QA in Gambling.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// E2E Game Search Tests for AcmetoCasino Frontend
// Validates game discovery, play-for-fun, and play-for-real launch flows
describe('AcmetoCasino Game Search Tests', function() {

  it('should search and launch play-for-fun', function() {
    browser.get('http://localhost:9001');

    // Search for a specific game
    element(by.css(
      'body > my-app > div > ng-component > games > div:nth-child(1) > div.col-md-4 > input'
    )).clear();
    element(by.css(
      'body > my-app > div > ng-component > games > div:nth-child(1) > div.col-md-4 > input'
    )).sendKeys('spectra');

    // Click play-for-fun button
    element(by.css(
      'body > my-app > div > ng-component > games > div:nth-child(2) > ul > li > div > button:nth-child(2)'
    )).click();

    // Verify game launch URL contains expected parameters
    expect(browser.driver.getCurrentUrl()).toContain('mode=fun');
  });

  it('should search and launch play-for-real', function() {
    browser.get('http://localhost:9001');

    // Search for a specific game
    element(by.css(
      'body > my-app > div > ng-component > games > div:nth-child(1) > div.col-md-4 > input'
    )).clear();
    element(by.css(
      'body > my-app > div > ng-component > games > div:nth-child(1) > div.col-md-4 > input'
    )).sendKeys('spectra');

    // Click play-for-real button
    element(by.css(
      'body > my-app > div > ng-component > games > div:nth-child(2) > ul > li:nth-child(1) > div > button.btn.btn-primary'
    )).click();

    // Verify game launch URL includes game and session parameters
    expect(browser.driver.getCurrentUrl()).toContain('game=');
  });

});
