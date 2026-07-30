# Prize Admin - Campaign & Rewards Management

Angular 12 admin interface for managing prize campaigns, game qualifiers,
recurring schedules, and brand-specific reward distribution across the
AcmetoCasino platform.

## Architecture

Built with Angular 12, Keycloak authentication, and NgBootstrap UI components.
Uses a shared CRUD framework (`SimpleCreateEditViewFormComponent`) with lazy-loaded
feature modules for promotions, settings, and campaign management.

## Modules

| Module | Purpose |
|--------|---------|
| Promotions | Campaign creation, prize pools, reward rules |
| Campaign Reoccurrence | Recurring campaign schedules (daily/weekly/monthly) |
| Game Qualifier | Link specific games to campaign eligibility |
| Brand | Multi-brand campaign targeting |
| Country/Cities | Geographic targeting for regional campaigns |
| Marketing Category | Campaign categorization for reporting |
| Settings | System configuration and admin preferences |

## Key Patterns

- **Keycloak SSO**: APP_INITIALIZER bootstraps Keycloak before Angular router
- **Lazy-loaded modules**: Promotions and Settings loaded on demand
- **Generic CRUD forms**: `SimpleCreateEditViewFormComponent` base class
  provides create/edit/view states with shared validation
- **Conditional validators**: Form fields validate only when parent
  controls are in specific states (e.g., recurrence fields only
  validate when recurrence is enabled)

## Running

```bash
npm install
ng serve        # http://localhost:4200
npm run build:prod  # Production build with AOT
docker build -t prize-admin .  # Nginx-based container
```
