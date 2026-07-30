# Payments OpenAPI Specifications

Production OpenAPI/Swagger specifications for AcmetoCasino's payment service endpoints.
Sanitized from a real iGaming payment platform.

## What This Contains

- **`api/swagger/new.yml`** -- OpenAPI 3.0 spec for the payment methods retrieval endpoint (`/methods/new`)
- **`types/new.yml`** -- Type definitions for payment method listing (used for TypeScript generation)
- **`types/make.yml`** -- OpenAPI 3.0 spec for payment processing endpoint (`/methods/make`), including Adyen encrypted card data schemas, existing vs new payment method handling, and response types (SUCCESS, FAILED, PENDING, VERIFY, UKGC_CREDIT_CARD_NOT_ALLOWED)
- **`types/adyen_apple_merchant_validate.yaml`** -- Swagger 2.0 spec for Apple Pay merchant validation via Adyen (`/adyen/apple/merchant/v1/validate`)
- **`scripts/generateTsDefinitions.js`** -- Node.js build script that converts all YAML type specs into TypeScript interfaces using `@manifoldco/swagger-to-ts`
- **`package.json`** -- NPM package config, published to GitHub Package Registry as `@acmetocasino/payments-openapi`

## Architecture

The specs are consumed as an NPM package by frontend applications. The `prepublish` script auto-generates TypeScript definitions from the YAML files, giving frontend teams type-safe interfaces for payment integration:

```
types/*.yml  -->  generateTsDefinitions.js  -->  dist/*.ts  -->  npm publish
```

## Key Design Decisions

- **Dual authentication**: Supports both `userId + brand` (backoffice-initiated) and `sessionid` (player-initiated) via `oneOf` schema
- **Adyen client-side encryption**: Card data arrives pre-encrypted by Adyen's CSE library -- the API never sees raw card numbers (PCI DSS compliance)
- **UKGC credit card block**: The `UKGC_CREDIT_CARD_NOT_ALLOWED` response type handles the UK Gambling Commission's 2020 ban on credit card gambling deposits
- **Redirect flexibility**: Supports both iframe and full-page redirects with GET/POST methods for different PSP requirements
