// Companion code for "The Backend of Luck" - Chapter 01, The Online Casino Ecosystem.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// ============================================================================
// Modern Backoffice: Nx Monorepo with Vue/TypeScript
// ============================================================================
// This file contrasts the modern admin panel architecture against the legacy
// PHP version shown in legacy-php-structure.php.
//
// The modern backoffice is an Nx monorepo with:
//   - 1 Vue application shell (fe-main-app)
//   - 3 Node.js backend services (player, search, utils)
//   - 15+ shared libraries organized by domain
//   - TypeScript path aliases enforcing module boundaries
//   - CODEOWNERS mapping code ownership to team structure
// ============================================================================

// -- Architecture Comparison ------------------------------------------------

/**
 * LEGACY PHP BACKOFFICE (circa 2008-2020)
 * ├── Monolithic PHP application
 * ├── Smarty template engine for server-side rendering
 * ├── Direct SQL queries in module files
 * ├── No type safety, no build step
 * ├── Module = single .php file + .tpl template
 * ├── All admin functions in one deployable unit
 * ├── Multi-brand via runtime config constants
 * └── Team ownership: implicit (whoever last touched the file)
 *
 * MODERN NX MONOREPO (2020+)
 * ├── Vue 3 SPA with TypeScript
 * ├── Nx workspace with 20+ projects
 * ├── Feature libraries with barrel exports
 * ├── Full type safety across boundaries
 * ├── Dedicated backend services (Node.js)
 * ├── Independent deployment per service
 * ├── API gateway (Kong) for routing
 * └── Team ownership: explicit via CODEOWNERS + Nx boundaries
 */

// -- How module boundaries work in the modern architecture ------------------

// In the legacy PHP panel, any file could require() any other file.
// In the Nx monorepo, imports are constrained by TypeScript path aliases:

// ALLOWED: Feature imports shared UI through barrel export
// import { DataTable } from '@acme.bo/ui/components';
// import { PlayerIcon } from '@acme.bo/ui/icons';
// import { usePlayerSearch } from '@acme.bo/composables';

// BLOCKED: Feature cannot import another feature's internals
// import { something } from '../../../feature-dashboard/src/lib/internal';
// ^^^^^ TypeScript compiler error: path not in tsconfig aliases

// BLOCKED: Feature cannot import from another feature at all
// import { DashboardWidget } from '@acme.bo/feature-dashboard';
// ^^^^^ Nx boundary rule violation (feature -> feature dependency)

// -- Migration path: Legacy bridge pattern ----------------------------------

/**
 * The legacy-platform-service library wraps old PHP endpoints:
 *
 *   Modern Vue component
 *     -> @acme.bo/service-clients/player-service (typed client)
 *       -> player-service (Node.js, apps/)
 *         -> @acme.bo/services/legacy-platform-service (bridge)
 *           -> Legacy PHP API (HTTP calls to old backoffice)
 *
 * This allows incremental migration:
 * 1. New features are built entirely in the modern stack
 * 2. Existing features are wrapped via the legacy bridge
 * 3. Over time, legacy endpoints are replaced with native Node.js
 * 4. The bridge library shrinks until it can be removed
 */

// -- Project count comparison -----------------------------------------------

const architectureComparison = {
  legacy: {
    deployableUnits: 1,        // Single PHP application
    moduleFiles: 50,           // ~50 PHP module files
    teamOwnership: "implicit", // Whoever last touched the file
    buildTime: "0s",           // No build step (interpreted PHP)
    typeChecking: "none",      // Runtime errors only
    testCoverage: "minimal",   // Manual testing was primary QA
  },
  modern: {
    deployableUnits: 4,        // 1 SPA + 3 backend services
    libraryProjects: 20,       // 20+ Nx library projects
    teamOwnership: "explicit", // CODEOWNERS + Nx project boundaries
    buildTime: "~3min",        // Full build; <30s with Nx affected
    typeChecking: "strict",    // TypeScript strict mode
    testCoverage: "per-lib",   // Jest per library, Cypress E2E
  },
};

// -- Key insight for the book -----------------------------------------------
//
// The migration from legacy PHP to Nx monorepo was NOT primarily a
// technology decision. It was an organizational decision:
//
// - The PHP monolith had no clear ownership boundaries. Any developer
//   could modify any file, leading to implicit coupling and fear of
//   change in critical modules (payments, compliance).
//
// - The Nx monorepo makes team boundaries explicit in code. The
//   CODEOWNERS file ensures the right team reviews changes to their
//   domain. TypeScript path aliases prevent accidental cross-boundary
//   imports. Nx affected commands mean teams only wait for their own
//   tests in CI.
//
// - Conway's Law in action: the new architecture mirrors the team
//   structure. Feature teams own feature libraries. The platform team
//   owns the shell and shared infrastructure. The services team owns
//   backend services and their client libraries.
// ============================================================================

export { architectureComparison };
