// Companion code for "The Backend of Luck" - Chapter 36, Financial Operations.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// AcmetoCasino Cashier UI Launcher Component
// Embeddable Web Component that hosts the cashier iframe and manages
// cross-origin communication with the cashier application.
//
// The launcher reads configuration from a JSON attribute on the custom element,
// handles session management, body scroll locking (for modal mode), and
// jurisdiction-specific styling (e.g., Swedish Gaming Authority requirements).

import { Component, HostListener, ElementRef, OnInit } from '@angular/core';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';
import { disableBodyScroll, enableBodyScroll } from 'body-scroll-lock';
import { environment } from '../environments/environment';

@Component({
  selector: 'acme-cashier-ui-launcher',
  templateUrl: './app.component.html',
  styleUrls: ['./app.component.scss']
})
export class AppComponent implements OnInit {

  sessionId: string = '';
  private iframe: any;

  public cashierHidden: boolean = true;
  public cashierUrl: SafeResourceUrl = '';
  public showInModal: boolean = true;
  public brandLogoURL: SafeResourceUrl;
  public lightHeader: boolean = false;
  public sgaStyling: boolean = false;      // Swedish Gaming Authority styling
  private handler: any;
  public config: CashierConfig;

  constructor(
    public sanitizer: DomSanitizer,
    private elementRef: ElementRef
  ) {
    this.cashierUrl = sanitizer.bypassSecurityTrustResourceUrl(
      environment.cashierUrl
    );
  }

  ngOnInit() {
    // Parse configuration from the custom element's 'config' attribute
    this.config = JSON.parse(
      this.elementRef.nativeElement.getAttribute('config')
    );

    if (!this.config.sessionId) return;

    this.brandLogoURL = this.sanitizer.bypassSecurityTrustResourceUrl(
      this.config.brandLogoUrl
    );
    this.lightHeader = this.config.lightHeader === 'true';
    this.showInModal = this.config.showInModal === 'true';
    this.sgaStyling = this.config.jurisdiction === 'sga';

    this.iframe = document.getElementById('cashier-ui-iframe');

    // Default display settings
    this.config.paymentMethodListFormat =
      this.config.paymentMethodListFormat || 'grid';
    this.config.hideAmountOptions =
      this.config.hideAmountOptions === ('true' as any);

    // Listen for config-ready signal from the cashier iframe
    window.addEventListener('message', (event) => {
      if (event.data === 'acme-cashier-ui-config-listener-ready') {
        this.iframe.contentWindow.postMessage({
          call: 'acme-cashier-ui-config',
          value: this.config
        }, '*');
      }

      if (event.data === 'session-expired') {
        window.location.reload();
      }
    }, false);
  }

  onIframeLoaded() {
    // Hook for post-load initialization
  }

  // Global click handler for cashier open/close triggers
  @HostListener('document:click', ['$event'])
  open(event) {
    if (event.target.classList.contains('acme-cashier-ui-open')) {
      this.cashierHidden = false;
      disableBodyScroll(this.iframe);

      // Handle jQuery Mobile ui-page elements if present
      let uiPage = document.getElementsByClassName('ui-page')[0];
      if (uiPage) {
        uiPage['style']['position'] = 'initial';
        uiPage['style']['top'] = 'initial';
        uiPage['style']['display'] = 'none';
        document.body.style.position = 'fixed';
        document.documentElement.style.position = 'fixed';
      }
    } else if (event.target.classList.contains('acme-cashier-trigger')) {
      // Reload cashier with fresh config (e.g., after balance change)
      this.iframe = document.getElementById('cashier-ui-iframe');
      this.iframe['src'] += '';
      this.config = JSON.parse(
        this.elementRef.nativeElement.getAttribute('config')
      );
      this.iframe.contentWindow.postMessage({
        call: 'acme-cashier-ui-config',
        value: this.config
      }, '*');
    }
  }

  hideCashier() {
    this.cashierHidden = true;
    document.getElementById('cashier-ui-iframe')['src'] += '';
    enableBodyScroll(this.iframe);

    let uiPage = document.getElementsByClassName('ui-page')[0];
    if (uiPage) {
      uiPage['style']['position'] = '';
      uiPage['style']['top'] = '0';
      uiPage['style']['display'] = 'block';
      document.body.style.position = 'initial';
      document.documentElement.style.position = 'initial';
    }
  }
}

interface CashierConfig {
  sessionId: string;
  originUrl: string;
  brandLogoUrl: string;
  showInModal: string;
  themeUrl: string;
  lightHeader: string;
  language: string;
  googleAnalyticsContainerId: string;
  currency: string;
  paymentMethodListFormat: 'grid' | 'row';
  hotjarId: string;
  hotjarSv: string;
  hideAmountOptions: boolean;
  jurisdiction: string;
}
