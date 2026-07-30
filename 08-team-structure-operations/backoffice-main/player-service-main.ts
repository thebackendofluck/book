// Companion code for "The Backend of Luck" - Chapter 08, Team Structure and Operations.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// AcmetoCasino Backoffice - Player Service Entry Point
// Microservice for player management, built on the CQRS framework.
// Registers with Kong API gateway on startup for automatic service discovery.
//
// The service exposes gRPC endpoints for:
// - GetPlayerInfo: Full player profile with flags, limits, KYC status
// - GetAccountHistory: Transaction and gameplay history
//
// Authentication is disabled for internal services (Kong handles auth at the gateway).

import { CqrsServer } from '@acme.bo/services/cqrs-server';

// Set working directory to script directory for proto file resolution
process.chdir(__dirname);

const server = new CqrsServer();

server.start({
  serviceName: 'player_service',
  registerWithKong: true,     // Auto-register with Kong API gateway
  disableAuth: true,          // Kong handles authentication at the edge
  overwriteExistingService: true, // Replace existing Kong service on restart
});
