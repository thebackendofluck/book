// Companion code for "The Backend of Luck" - Chapter 36, Financial Operations.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// AcmetoCasino Apple Pay Integration Channel
// Bridges the Apple Pay JS API between the cashier iframe and the top-level
// casino page. Apple Pay requires the payment sheet to be initiated from the
// top frame (not an iframe), so this channel handles cross-frame messaging.
//
// Flow:
// 1. Cashier iframe sends ApplePayCanActivateRequest to check device support
// 2. This channel responds with canMakePayments() result
// 3. On deposit, cashier sends ApplePayStart with payment parameters
// 4. This channel creates ApplePaySession, handles merchant validation
// 5. On user authorization (Touch ID/Face ID), sends token back to cashier
// 6. Cashier processes the token through the payment backend
//
// Reference: https://developer.apple.com/documentation/apple_pay_on_the_web

import {
  Messages,
  ApplePayCanActivateResponse,
  ApplePayStartEvent,
  ApplePayValidateRequest,
  ApplePayToken,
  MessageEvents,
  ErrorEvent,
} from './applepay-messages';

declare global {
  interface Window {
    ApplePaySession?: typeof ApplePaySession;
  }
}

export default class ApplePayChannel {
  private applePaySession: ApplePaySession | undefined;
  private open = false;
  private cashierIframe: WindowProxy;

  constructor() {
    window.addEventListener(
      'message',
      (event: MessageEvent) => {
        // Ensure source is a Window (not MessagePort or ServiceWorker)
        if (
          !(event.source instanceof MessagePort) &&
          !(event.source instanceof ServiceWorker)
        ) {
          this.cashierIframe = event.source;
        }

        if (this.isType(event, Messages.ApplePayStart)) {
          this.beginApplePay(event.data);
        } else if (this.isType(event, Messages.ApplePayCanActivateRequest)) {
          this.canActivate();
        }
      },
      false
    );
  }

  private beginApplePay(data: ApplePayStartEvent['data']) {
    window.onmessage = (event) => {
      if (!this.applePaySession || !this.open) return;

      if (this.isType(event, Messages.ApplePayValidateResponse)) {
        // Backend validated merchant with Apple -- complete validation
        this.applePaySession.completeMerchantValidation(
          event.data.merchantSession
        );
      } else if (this.isType(event, Messages.ApplePayCompletePayment)) {
        // Payment processed -- show result on Apple Pay sheet
        this.applePaySession.completePayment(event.data.status);
      } else if (this.isType(event, Messages.CancelRequest)) {
        this.cancelAppleSession();
      }
    };

    // Create Apple Pay session with merchant-provided parameters
    this.applePaySession = new ApplePaySession(...data.parameters);

    // Merchant validation: Apple calls this when payment sheet displays
    this.applePaySession.onvalidatemerchant = (validateEvent) => {
      const validationRequest: ApplePayValidateRequest['data'] = {
        type: Messages.ApplePayValidateRequest,
        validationURL: validateEvent.validationURL,
        hostname: location.hostname,
      };
      this.cashierIframe.postMessage(validationRequest, '*');
    };

    // User cancelled: notify cashier with error code
    this.applePaySession.oncancel = () => {
      this.open = false;
      const token: ErrorEvent['data'] = {
        type: Messages.PaymentError,
        error: { code: 'apple-pay.1' },
      };
      this.cashierIframe.postMessage(token, '*');
    };

    // User authorized with Touch ID / Face ID / Passcode
    this.applePaySession.onpaymentauthorized = (authorizedEvent) => {
      const token: ApplePayToken['data'] = {
        type: Messages.ApplePayToken,
        payment: authorizedEvent.payment,
      };
      this.cashierIframe.postMessage(token, '*');
    };

    // Begin the merchant validation process
    this.applePaySession.begin();
    this.open = true;
  }

  // Check whether Apple Pay is available on this device/browser
  private canActivate() {
    let canActivate: boolean;
    try {
      canActivate =
        !!window.ApplePaySession && ApplePaySession.canMakePayments();
    } catch (error) {
      canActivate = false;
    }
    const response: ApplePayCanActivateResponse['data'] = {
      type: Messages.ApplePayCanActivateResponse,
      canActivate,
    };
    this.cashierIframe.postMessage(response, '*');
  }

  private cancelAppleSession(): void {
    if (this.applePaySession) {
      this.applePaySession.abort();
      this.open = false;
    }
  }

  private isType<T extends keyof MessageEvents>(
    event: MessageEvent,
    type: T
  ): event is MessageEvents[T] {
    return event.data && event.data.type === type;
  }
}
