// Companion code for "The Backend of Luck" - Chapter 36, Financial Operations.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// AcmetoCasino Cashier Environment Configuration
// Dynamically resolves the static content URL and deployment environment
// based on the current browser URL. This allows a single cashier build
// to serve development, staging, and production environments.

export let environment: IEnvironment;

export const scontentUrl = determineScontentUrl();

function determineScontentUrl(): string {
  const href = document.location.href;

  if (href.includes('localhost')) {
    return 'https://stage.acmetocasino-stage.com/static';
  } else if (href.includes('dev-')) {
    return document.location.origin;
  } else if (href.includes('acmetocasino-stage')) {
    return `${document.location.origin}/static`;
  } else {
    return document.location.origin;
  }
}

export function determineDeploymentEnvironment(): string {
  const href = document.location.href;

  if (href.includes('localhost')) {
    return 'development';
  }
  if (href.includes('acmetocasino-stage')) {
    return 'stage';
  }
  return 'production';
}

export function setEnvironment(val: IEnvironment) {
  environment = val;
}

// Environment interface defining all PSP-specific configuration
// that varies between deployment environments. Each payment provider
// has its own set of keys, script URLs, and API endpoints.
export interface IEnvironment {
  production: boolean;
  environment?: string;
  apiBase: string;               // Cashier backend API
  platformApiBase: string;       // Platform API for user/session operations
  adyenOriginKey: string;        // Adyen client-side encryption key
  adyenScriptURL: string;        // Adyen drop-in component script
  epgBaseURL: string;            // EPG payment gateway base URL
  epgScriptURL: string;          // EPG client-side script
  paymentIqEncryptionUrl: string; // PaymentIQ card encryption endpoint
  braintree: {
    braintreeClientScriptUrl: string;   // Braintree client SDK
    paypalCheckoutScriptUrl: string;    // PayPal Checkout SDK
    braintreeDataCollector: string;     // Braintree device fingerprinting
  };
}
