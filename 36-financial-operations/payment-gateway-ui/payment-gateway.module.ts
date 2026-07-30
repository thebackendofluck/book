// Companion code for "The Backend of Luck" - Chapter 36, Financial Operations.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// AcmetoCasino Payment Gateway UI Module
// Root Angular module for the cashier library. Uses forRoot() pattern
// to inject brand-specific environment configuration at runtime.
// This allows a single cashier build to serve 30+ white-label brands.

import { ModuleWithProviders, NgModule } from '@angular/core';

import { SharedModule } from '@acmetocasino/shared';
import { CashierComponent } from './cashier.component';
import { EnvironmentConfig } from './modules/cashier-shared/services/environment.service';
import { allTranslations } from './translations/index';

@NgModule({
  declarations: [
    CashierComponent
  ],
  imports: [
    SharedModule.forRoot(null, null, allTranslations)
  ],
  exports: [
    CashierComponent
  ]
})
export class PaymentGatewayModule {

  // Inject brand-specific environment at module initialization.
  // Each white-label brand provides its own API base URLs, Adyen keys,
  // Braintree tokens, and EPG endpoint configurations.
  static forRoot(brandEnvironment: any): ModuleWithProviders<PaymentGatewayModule> {
    return {
      ngModule: PaymentGatewayModule,
      providers: [
        { provide: EnvironmentConfig, useValue: brandEnvironment },
      ]
    };
  }

  constructor(
    private environment: EnvironmentConfig
  ) { }
}
