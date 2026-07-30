// Companion code for "The Backend of Luck" - Chapter 36, Financial Operations.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// AcmetoCasino Payment Gateway UI Shared Constants
// TUV certification requirements for German-regulated markets.
// German regulation requires that certain payment methods display
// TUV (Technischer Uberwachungsverein) certification marks.

import { CountryCode } from 'libphonenumber-js';

export enum method {
  C_BANKTRANSFER_BE = 'c_bankTransfer_BE',
  DIRECTEBANKING = 'directEbanking',
  C_REMBOURS = 'c_rembours'
}

// Countries requiring TUV-certified payment method display
export const tuvCountries: CountryCode[] = [
  'DE'
];

// Payment methods with TUV certification
export const tuvCertifiedMethods: string[] = [
  method.C_BANKTRANSFER_BE,
  method.DIRECTEBANKING,
  method.C_REMBOURS
];
