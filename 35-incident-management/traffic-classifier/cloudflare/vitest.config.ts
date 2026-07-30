// Companion code for "The Backend of Luck" - Chapter 35, Incident Management.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

import { defineWorkersConfig } from "@cloudflare/vitest-pool-workers/config";

export default defineWorkersConfig({
  test: {
    poolOptions: {
      workers: {
        wrangler: { configPath: "./wrangler.toml" },
        miniflare: {
          // Provide stub KV namespaces for the test environment.
          // The test file uses in-memory mocks rather than real KV, so these
          // are just required to satisfy the Wrangler config binding parser.
          kvNamespaces: [
            "RATE_LIMITS",
            "CAMPAIGNS",
            "ATTACK_LOG",
            "JA3_BLOCKLIST",
          ],
          durableObjects: {
            ATTACK_COUNTER: "AttackCounter",
          },
        },
      },
    },
    // Run tests sequentially to avoid KV state leakage between tests
    pool: "vitest-pool-workers",
    globals: true,
    include: ["test/**/*.test.ts"],
  },
});
