# payment-gateway-ui - Angular Payment Library

Production Angular 12 library implementing the full cashier experience for AcmetoCasino
white-label casino brands. Handles deposits, withdrawals, card validation, Apple Pay,
Braintree, device fingerprinting, and 50+ payment method integrations.

## Architecture

The payment gateway UI is an Angular library (`ng-packagr`) consumed by the cashier-ui host application.
It uses the `@acmetocasino/shared` library for common functionality (translations, HTTP services).

Key architectural decisions:
- **Library-first design**: Published as an npm package, consumed by multiple host apps
- **Environment injection**: Brand-specific configuration provided via `PaymentGatewayModule.forRoot()`
- **Cross-origin communication**: Runs inside an iframe, communicates with the launcher via `postMessage`
- **Multi-PSP abstraction**: Deposit/withdraw components dynamically render based on payment method type

## Payment Method Coverage

The payment gateway UI supports 70+ payment methods across deposit and withdrawal, including:
- **Cards**: Visa, Mastercard, Amex, Discover (via PXP Financial, Adyen, EPG)
- **Digital wallets**: PayPal, Venmo, Apple Pay, Google Pay, MuchBetter
- **Bank transfers**: TrueLayer, Sofort, iDEAL, Trustly, EPS
- **Alternative**: PaySafeCard, PayNearMe, Cash at Casino, Crypto
- **Regional**: Boleto (Brazil), Interac (Canada), Zimpler (Nordics)
- **Regulated US**: VIP Preferred, Play+, Sightline

## Key Components

| Component | Purpose |
|-----------|---------|
| `payment-gateway.module.ts` | Root module with `forRoot()` environment injection |
| `pxp-add-card.component.ts` | Card entry form with credit-card-type detection, CVV validation, expiry checking |
| `applepay.ts` | Apple Pay JS API integration via cross-frame messaging |
| `deposit-payment-method.enum.ts` | 70+ deposit payment method identifiers |
| `payment-gateway-shared.constants.ts` | TUV-certified payment methods (German regulation) |
| `payment-gateway.constants.ts` | Open-loop payment method definitions (PayPal, Venmo) |
| `environment.ts` | Dynamic environment resolution (dev/stage/production) |
