// Companion code for "The Backend of Luck" - Chapter 08, Team Structure and Operations.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// ============================================================================
// Module Boundary Enforcement -- Nx Monorepo for Backoffice Admin Panel
// ============================================================================
// This file shows how TypeScript path aliases enforce team boundaries.
// Each library is scoped under @acme.bo/*, preventing direct file imports
// across library boundaries. Teams can only consume each other's code
// through the published barrel exports (index.ts).
//
// Source: tsconfig.base.json path mappings from production backoffice monorepo
// ============================================================================

// -- tsconfig.base.json path aliases (enforced at compile time) -------------
const pathAliases = {
  // Feature libraries -- owned by specific teams
  "@acme.bo/feature-dashboard":        "libs/feature-dashboard/src/index.ts",
  "@acme.bo/feature-players":          "libs/feature-players/src/index.ts",
  "@acme.bo/feature-knowledge-base":   "libs/feature-knowledge-base/src/index.ts",
  "@acme.bo/feature-search-and-tables":"libs/feature-search-and-tables/src/index.ts",
  "@acme.bo/feature-modals":           "libs/feature-modals/src/index.ts",

  // UI design system -- shared, owned by design system team
  "@acme.bo/ui/components":            "libs/ui/components/src/index.ts",
  "@acme.bo/ui/icons":                 "libs/ui/icons/src/index.ts",
  "@acme.bo/ui/layouts":               "libs/ui/layouts/src/index.ts",

  // Core infrastructure -- owned by platform team
  "@acme.bo/composables":              "libs/composables/src/index.ts",
  "@acme.bo/store-main":               "libs/store-main/src/index.ts",
  "@acme.bo/router-main":              "libs/router-main/src/index.ts",
  "@acme.bo/plugins":                  "libs/plugins/src/index.ts",
  "@acme.bo/interfaces/*":             "libs/interfaces/src/lib/*",
  "@acme.bo/util-js":                  "libs/util-js/src/index.ts",

  // Service clients -- bridge between frontend and backend services
  "@acme.bo/service-clients/base":            "libs/service-clients/base/src/index.ts",
  "@acme.bo/service-clients/player-service":  "libs/service-clients/player-service/src/index.ts",
  "@acme.bo/service-clients/search-service":  "libs/service-clients/search-service/src/index.ts",
  "@acme.bo/service-clients/utils-service":   "libs/service-clients/utils-service/src/index.ts",

  // Backend shared libraries
  "@acme.bo/services/cqrs-server":             "libs/services/cqrs-server/src/index.ts",
  "@acme.bo/services/helpers":                 "libs/services/helpers/src/index.ts",
  "@acme.bo/services/kong-client":             "libs/services/kong-client/src/index.ts",
  "@acme.bo/services/legacy-platform-service": "libs/services/legacy-platform-service/src/index.ts",
  "@acme.bo/services/platform-database":       "libs/services/platform-database/src/index.ts",
};

// -- nx.json configuration (enforced at build/CI time) ----------------------
const nxConfig = {
  npmScope: "acme.bo",
  affected: { defaultBase: "origin/master" },
  tasksRunnerOptions: {
    default: {
      runner: "@nrwl/workspace/tasks-runners/default",
      options: {
        cacheableOperations: ["build", "lint", "test", "e2e"],
        parallel: 3,  // Run up to 3 tasks in parallel
      },
    },
  },
  defaultProject: "fe-main-app",
};

// ============================================================================
// KEY ARCHITECTURAL PATTERNS:
//
// 1. BARREL EXPORTS: Every library exposes a single index.ts entry point.
//    Teams cannot import internal files from another team's library.
//    This creates a stable API contract between teams.
//
// 2. DEPENDENCY DIRECTION: Feature libs -> UI libs -> Interfaces/Util
//    Feature libraries depend on shared UI and infrastructure,
//    but never on each other. This prevents circular dependencies
//    and allows teams to work independently.
//
// 3. LEGACY BRIDGE: The "legacy-platform-service" library wraps the
//    old PHP admin panel's API, providing a typed TypeScript interface
//    over legacy endpoints. This allows incremental migration without
//    rewriting everything at once.
//
// 4. SERVICE CLIENTS: Each backend service (player, search, utils) has
//    a corresponding client library. The service team owns the server
//    AND the client, ensuring API contracts stay in sync.
//
// 5. AFFECTED COMMANDS: Nx's "affected" feature uses git diff to run
//    only the tests/lints for libraries that changed. This means a
//    change to feature-players only triggers tests for feature-players
//    and its dependents -- not the entire monorepo.
//
// TEAM TOPOLOGY MAPPING:
//   Code boundary          -> Team boundary
//   libs/feature-*         -> Feature teams (2-4 devs each)
//   libs/ui/*              -> Design system team (2-3 devs)
//   libs/services/*        -> Backend/services team (3-5 devs)
//   apps/fe-main-app       -> Platform team (shell, routing, auth)
//   libs/service-clients/* -> Shared ownership (service + consumer)
// ============================================================================

export { pathAliases, nxConfig };
