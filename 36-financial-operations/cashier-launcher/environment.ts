// Companion code for "The Backend of Luck" - Chapter 36, Financial Operations.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// AcmetoCasino Cashier UI Launcher - Environment Configuration
// Dynamically resolves the cashier URL based on the current deployment environment.
// This pattern is common in white-label iGaming: a single launcher codebase
// serves multiple environments without rebuild.

export const environment = {
  cashierUrl: `${getScontentUrl()}/js/acme-cashier-ui/index.html`
};

function getScontentUrl(): string {
  const href = document.location.href;

  if (href.includes('dev-')) {
    // Development environment: dedicated dev static content server
    return 'https://dev-static.acmetocasino-stage.com';
  }
  else if (href.includes('acmetocasino-stage') || href.includes('.stage')) {
    // Staging environment: static content served from staging domain
    return 'https://stage.acmetocasino-stage.com/static';
  }
  else {
    // Production: dedicated static content CDN
    return 'https://static.acmetocasino.com';
  }
}
