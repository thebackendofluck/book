// Companion code for "The Backend of Luck" - Chapter 36, Financial Operations.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// AcmetoCasino Payment Gateway UI Constants
// Defines open-loop payment method handling and UI animations.
// Open-loop methods (PayPal, Venmo) allow withdrawals to accounts
// that weren't used for deposit -- unlike closed-loop card methods
// where funds must return to the original deposit card.

import { animate, state, style, transition, trigger } from '@angular/animations';

export enum methodId {
  PAYPAL_OPENLOOP = 'paypal_openloop',
  VENMO = 'paypal_venmo',
  NEWBANK = 'newbank'
}

export enum apiResponseStatus {
  PENDING = 'PENDING',
  PROCESSING = 'PROCESSING',
  BATCH_APPROVED = 'BATCH_APPROVED',
  REVIEW = 'REVIEW'
}

export enum apiResponseType {
  ERROR = 'error'
}

// Cashier UI animations for expanding/collapsing payment method sections
export const animations = [
  trigger('expandCollapse', [
    state('true', style({ height: '0px', opacity: '0' })),
    state('false', style({ height: '*', opacity: '1' })),
    transition('1 => 0', animate('200ms ease-out')),
    transition('0 => 1', animate('200ms ease-out'))
  ]),
  trigger('rotateCaret', [
    state('true', style({
      transform: 'rotate(-180deg)',
      'transform-origin': 'center'
    })),
    state('false', style({
      transform: 'rotate(0)',
      'transform-origin': 'center'
    })),
    transition('1 => 0', animate('150ms linear')),
    transition('0 => 1', animate('150ms linear'))
  ]),
];

// Open-loop payment methods support withdrawals to non-deposit accounts
export const openLoopMethods: Array<string | methodId> = [
  methodId.PAYPAL_OPENLOOP,
  methodId.VENMO
];

// Which user identifier each open-loop method uses for verification
export const openLoopMethodUserIdProperty = {
  [methodId.PAYPAL_OPENLOOP]: 'email',
  [methodId.VENMO]: 'phone'
};

export const openLoopMethodLangKeys = {
  [methodId.PAYPAL_OPENLOOP]: {
    label: '@paypal-openloop-label',
    warning: '@paypal-openloop-warning'
  },
  [methodId.VENMO]: {
    label: '@venmo-openloop-label',
    warning: '@venmo-openloop-warning'
  }
};
