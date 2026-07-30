# E2E Testing Scripts (Protractor)

Automated End-to-End proof-of-concept testing for Angular JS casino applications.
Uses Protractor for browser automation against a locally running casino frontend.

## Setup

```bash
npm install -g protractor
webdriver-manager start
protractor conf.js
```

## Test Coverage

| Spec File | Flow | Description |
|-----------|------|-------------|
| `login.spec.js` | Authentication | Login validation with correct/incorrect credentials |
| `registration.spec.js` | Player Registration | Full registration form with gender, address, phone, T&C acceptance |
| `deposit.spec.js` | Deposit | Login then initiate deposit flow |
| `game-search.spec.js` | Game Discovery | Search games, launch play-for-fun and play-for-real modes |

These tests represent early-stage E2E automation for a casino platform frontend,
demonstrating how Protractor interacts with Angular components for critical player journeys.
