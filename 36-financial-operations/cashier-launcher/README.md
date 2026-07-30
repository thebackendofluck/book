# Cashier UI Launcher

Embedded Web Component that configures and launches the AcmetoCasino Cashier UI.
Deployed as a standalone `<acme-cashier-ui-launcher>` custom element that operators
embed in their white-label casino sites.

## Architecture

The launcher is an Angular 6 application compiled as a Web Component (Custom Element).
It renders an iframe pointing to the centrally-hosted Cashier UI, passing configuration
via `postMessage` cross-origin communication.

## Integration

Insert the code snippet below into your site:

```html
<acme-cashier-ui-launcher
  config='{
    "originUrl": "https://www.mybrand.com",
    "sessionId": "player-session-id",
    "brandLogoUrl": "https://www.mybrand.com/logo.png",
    "language": "en",
    "currency": "GBP",
    "showInModal": "true"
  }'
></acme-cashier-ui-launcher>

<script src="https://static.acmetocasino.com/js/acme-cashier-ui-launcher/acme-cashier-ui-launcher.min.js"></script>
```

## Configuration Options

| Parameter                  | Type   | Required | Default | Description |
|----------------------------|--------|----------|---------|-------------|
| originUrl                  | String | Yes      | None    | Casino site URL (e.g., https://www.mybrand.com) |
| sessionId                  | String | Yes      | None    | Platform session ID for the authenticated player |
| brandLogoUrl               | String | Yes      | None    | Brand logo URL displayed in the cashier header |
| lightHeader                | String | No       | false   | Use light-colored header instead of dark |
| language                   | String | No       | en      | ISO 639-2 language code (en, fr, de, no, sv) |
| showInModal                | String | No       | false   | Launch cashier in a fixed-size modal on desktop |
| currency                   | String | No       | GBP     | ISO 4217 currency code |
| paymentMethodListFormat    | String | No       | grid    | Payment method display: "grid" or "row" |
| hideAmountOptions          | String | No       | false   | Hide preset deposit amount buttons |
| jurisdiction               | String | No       | None    | Regulatory jurisdiction (e.g., "sga" for Swedish Gaming Authority) |

## Theming

The cashier loads a theme file from `{originUrl}/css/cashier/theme.css`.
Override Bootstrap 4.2.1 variables for brand customization:

```scss
$theme-colors: (
  "primary": #ff7a00,
  "secondary": #0072CF,
  "success":  #05f74f
);
$body-bg: #03063e;
$body-color: #fff;
@import "node_modules/bootstrap/scss/bootstrap.scss";
```

## Click Handlers

Add class `acme-cashier-ui-open` to any element to toggle the cashier on click.
