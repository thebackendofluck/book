# Backoffice Main (BO2) - Nx Monorepo

Production backoffice application for AcmetoCasino built as an Nx monorepo with:
- **Frontend**: Vue.js 3 with Composition API, PrimeVue, Keycloak SSO
- **Backend**: Node.js microservices with gRPC, CQRS pattern
- **Database**: PostgreSQL with parameterized queries
- **API Gateway**: Kong for service discovery and authentication
- **Infrastructure**: Docker Compose with isolated service networks

## Architecture

```
acme.bo/
├── apps/
│   ├── fe-main-app/        # Vue.js 3 frontend (PrimeVue, Keycloak)
│   ├── player-service/     # Player management microservice (gRPC)
│   ├── search-service/     # Player search with advanced filtering
│   ├── utils-service/      # Brands, countries, payment methods, alerts
│   └── fe-main-app-e2e/    # Cypress E2E tests
├── libs/
│   ├── composables/        # Vue 3 composables (useAxios, useToast)
│   ├── feature-players/    # Player management UI components
│   ├── feature-dashboard/  # Dashboard feature module
│   ├── services/
│   │   ├── cqrs-server/    # gRPC CQRS framework with Kong registration
│   │   ├── platform-database/  # PostgreSQL connection management
│   │   ├── kong-client/    # Kong API gateway client
│   │   └── legacy-platform-service/  # Legacy API integration
│   ├── store-main/         # Vuex state management
│   ├── router-main/        # Vue Router with role-based access
│   └── ui/                 # Shared UI component library
└── tools/
    ├── executors/          # Custom Nx executors (start, stop, service-build)
    └── generators/         # Nx generators (docker-compose, service-client)
```

## Key Patterns

- **CQRS over gRPC**: Each microservice extends `CqrsServer` and registers
  query/command handlers that are auto-discovered and registered with Kong
- **Kong service discovery**: Services register themselves with Kong at
  startup, enabling API gateway routing without manual configuration
- **Nx affected commands**: CI runs only tests for changed libraries,
  preventing feature teams from blocking each other
- **Keycloak JWT flow**: Frontend authenticates via Keycloak, Kong validates
  JWT tokens, services receive validated user context
