// Companion code for "The Backend of Luck" - Chapter 15, Casino Mathematics and Game Economy.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// AcmetoCasino Prize Admin - Application Routing
// Demonstrates lazy-loaded module architecture for a casino admin tool.
// Default route redirects to /promotions -- the primary workflow.
// Promotions and Settings modules are loaded on-demand to reduce
// initial bundle size for operators who may only use a subset of features.

import { NgModule, NgModuleFactory, Type } from '@angular/core';
import { Routes, RouterModule } from '@angular/router';
import { DefaultComponent } from './layouts/default/default.component';
import { DashboardComponent } from './pages/dashboard/dashboard.component';
import { HomeComponent } from './pages/home/home.component';
import { Observable } from 'rxjs';
import { PromotionsModule } from './pages/promotions/promotions.module';
import { SettingsModule } from './pages/settings/settings.module';

const DEFAULT_PAGE: string = '/promotions';

const routes: Routes = [
  {
    path: '',
    component: DefaultComponent,
    children: [
      {
        path: '',
        redirectTo: DEFAULT_PAGE,
        pathMatch: 'full'
      },
      { path: 'home', component: HomeComponent },
      { path: 'dashboard', component: DashboardComponent },
      {
        path: 'promotions',
        loadChildren: (): Promise<PromotionsModule> =>
          import('./pages/promotions/promotions.module').then(
            m => m.PromotionsModule
          )
      },
      {
        path: 'settings',
        loadChildren: (): Promise<SettingsModule> =>
          import('./pages/settings/settings.module').then(
            m => m.SettingsModule
          )
      }
    ]
  },
  { path: '**', redirectTo: DEFAULT_PAGE, pathMatch: 'full' }
];

@NgModule({
  imports: [RouterModule.forRoot(routes, {
    relativeLinkResolution: 'legacy',
    enableTracing: false
  })],
  exports: [RouterModule]
})
export class AppRoutingModule {}
