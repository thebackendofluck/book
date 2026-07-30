// Companion code for "The Backend of Luck" - Chapter 15, Casino Mathematics and Game Economy.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// AcmetoCasino Prize Admin - Root Module
// Bootstraps the prize administration application with Keycloak SSO.
// Uses APP_INITIALIZER to ensure authentication completes before
// the Angular router activates -- critical for role-based route guards.

import { APP_INITIALIZER, NgModule } from '@angular/core';
import { BrowserModule } from '@angular/platform-browser';
import { AppRoutingModule } from './app-routing.module';
import { AppComponent } from './app.component';
import { SharedModule } from './shared/shared.module';
import { BrowserAnimationsModule } from '@angular/platform-browser/animations';
import { FormControl, FormsModule, ReactiveFormsModule } from '@angular/forms';
import { HttpClientModule, HttpClient } from '@angular/common/http';
import { NgbModule, NgbTooltipModule } from '@ng-bootstrap/ng-bootstrap';
import { EnvServiceProvider } from './services/env.service.provider';
import { initializeKeycloak } from './init/keycloak-init.factory';
import { KeycloakAngularModule, KeycloakService } from 'keycloak-angular';
import { EnvService } from './services/env.service';
import { RouterModule } from '@angular/router';
import { MarketingCategoryModule } from './components/marketing-category/marketing-category.module';

@NgModule({
  declarations: [AppComponent],
  imports: [
    RouterModule,
    BrowserModule,
    FormsModule,
    AppRoutingModule,
    ReactiveFormsModule,
    SharedModule,
    BrowserAnimationsModule,
    HttpClientModule,
    NgbModule,
    KeycloakAngularModule,
    MarketingCategoryModule,
    NgbTooltipModule
  ],
  providers: [
    EnvServiceProvider,
    HttpClient,
    {
      // Keycloak initialization blocks app bootstrap until auth is complete.
      // This ensures route guards have access to user roles and permissions
      // before any navigation occurs.
      provide: APP_INITIALIZER,
      useFactory: initializeKeycloak,
      multi: true,
      deps: [KeycloakService, EnvService]
    }
  ],
  bootstrap: [AppComponent]
})
export class AppModule {}
