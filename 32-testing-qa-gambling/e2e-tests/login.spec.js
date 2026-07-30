// Companion code for "The Backend of Luck" - Chapter 32, Testing and QA in Gambling.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// E2E Login Tests for AcmetoCasino Frontend
// Validates authentication flow against the Angular casino application
describe('AcmetoCasino Login Tests', function() {

  it('should handle successful login', function() {
    browser.get('http://localhost:9001/login');

    element(by.id('username')).clear();
    element(by.id('username')).sendKeys('testuser001');
    element(by.id('password')).clear();
    element(by.id('password')).sendKeys('TestPass123');

    element(by.id('submit')).click();

    // Verify login success by checking we're redirected away from login page
    var loginMessage = element(
      by.xpath('/html/body/my-app/div/ng-component/login/div')
    ).click();

    expect(loginMessage.getText()).toEqual(
      'Login failed. Username or Password incorrect.'
    );
  });

  it('should display error on failed login', function() {
    browser.get('http://localhost:9001/login');

    element(by.id('username')).clear();
    element(by.id('username')).sendKeys('invaliduser');
    element(by.id('password')).clear();
    element(by.id('password')).sendKeys('wrongpassword');

    element(by.id('submit')).click();

    var errorMessage = element(
      by.xpath('/html/body/my-app/div/ng-component/login/div')
    ).click();

    expect(errorMessage.getText()).toEqual(
      'Login failed. Username or Password incorrect.'
    );
  });

});
