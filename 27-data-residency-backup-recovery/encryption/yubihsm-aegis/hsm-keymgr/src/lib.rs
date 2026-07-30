// Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/// Library interface — exposes internal modules for integration tests.
/// Not intended as a public API; use the binary directly in production.
pub mod aegis_ops;
pub mod dev;
pub mod keymgr;
#[cfg(feature = "hsm")]
pub mod hsm;
