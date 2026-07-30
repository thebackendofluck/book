// Companion code for "The Backend of Luck" - Chapter 32, Testing and QA in Gambling.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// Protractor configuration for AcmetoCasino E2E tests
// Runs against a local Selenium WebDriver instance
exports.config = {
  seleniumAddress: 'http://localhost:4444/wd/hub',
  specs: ['game-search.spec.js'],
  // Uncomment to run other specs:
  // specs: ['registration.spec.js'],
  // specs: ['login.spec.js'],
  // specs: ['deposit.spec.js'],
  useAllAngular2AppRoots: true
};
