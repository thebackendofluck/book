// Companion code for "The Backend of Luck" - Chapter 36, Financial Operations.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// AcmetoCasino Deposit Payment Method Enumeration
// Maps all supported deposit payment methods to their API identifiers.
// Each enum value corresponds to a PSP + method combination configured
// in the platform's payment method settings.
//
// Note: A mature iGaming operator typically supports 50-70 payment methods
// across jurisdictions. This enum drives dynamic rendering of the cashier
// deposit form -- each method type has its own component template.

export enum DepositPaymentMethod {
  // PayPal integrations (multiple PSPs)
  Paypal = 'paypal_paypal',
  BraintreePaypal = 'braintree_paypal',
  AdyenPaypal = 'paypal',

  // US-regulated methods
  Vippreferred = 'vippreferred',
  PlayPlus = 'sightline_playplus',
  PayWithMyBank = 'paywithmybank_paywithmybank',

  // European banking
  Eps = 'eps',
  Ideal = 'ideal',
  DirectEbanking = 'directEbanking',
  EbankingFI = 'ebanking_FI',
  ApcoSofort = 'apco_sofort',
  ApcoDeSofort = 'apco_de_sofort',

  // Card payments (multiple PSPs)
  Card = 'card',
  Amex = 'amex',
  Visa = 'visa',
  Mastercard = 'mastercard',
  VisaDebit = 'visa_debit',
  MastercardDebit = 'mc_debit',
  Maestro = 'maestro',
  AdyenMastercard = 'mc',
  AdyenApplePay = 'adyen_applepay',

  // PXP Financial card methods
  PxpCard = 'pxp_card',
  PxpCardMyCash = 'pxp_card_mycash',
  PxpCardVisa = 'pxp_card_visa',
  PxpCardMastercard = 'pxp_card_mastercard',
  PxpCardDiscover = 'pxp_card_discover',
  PxpCardAmex = 'pxp_card_amex',
  PxpApplePay = 'pxp_apple_pay',
  PxpInstadebit = 'pxp_instadebit',

  // Cash and voucher methods
  PayNearMe = 'pxp_pay_near_me',
  CashAtCasino = 'pxp_cashatcasino',
  PaySafeCard = 'paysafecard_paysafecard',
  Check = 'check',
  WireTransfer = 'wire_transfer',

  // Open banking
  TrueLayerInstant = 'TrueLayer_Instant_Bank_Transfer',
  ZimplerBank = 'zimpler_bank',

  // Crypto and alternative
  Crypto = 'forumpay_crypto',
  MuchBetter = 'hexopay_much_better',
  Moneybookers = 'moneybookers',
  Venmo = 'venmo',

  // EPG (Electronic Payment Gateway) methods
  EpgAibVisaCard = 'epg_aib_visa_card',
  EpgAibMastercard = 'epg_aib_mc_card',
  EpgAibMaestroCard = 'epg_aib_maestro_card',
  EpgPaypal = 'c_paypal',
  EpgAsInNetbanking = 'epg_as_in_netbanking',
  EpgAsInUpi = 'epg_as_in_upi',
  EpgAsInGooglepay = 'epg_as_in_googlepay',

  // PaymentIQ aggregator methods
  PaymentIqVisa = 'paymentiq_visa',
  PaymentIqMastercard = 'paymentiq_mc',
  PaymentIqMaestro = 'paymentiq_maestro',
  PaymentIqEzeeWallet = 'paymentiq_ezeewallet',
  PaymentIqTrueLayer = 'paymentiq_truelayer',
  PaymentIqLuxonpay = 'paymentiq_luxonpay',

  // Brazilian methods
  OnlineIpsBrBoleto = 'online_ips_br_boleto',
  OnlineIpsBrVisa = 'online_ips_br_visa',
  OnlineIpsBrMastercard = 'online_ips_br_mastercard',
  OnlineIpsBrDiscover = 'online_ips_br_discover',
  OnlineIpsBrElo = 'online_ips_br_elo'
}
