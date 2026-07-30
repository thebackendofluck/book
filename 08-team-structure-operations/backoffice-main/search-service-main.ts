// Companion code for "The Backend of Luck" - Chapter 08, Team Structure and Operations.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// AcmetoCasino Backoffice - Search Service Entry Point
// Dedicated microservice for player search functionality.
// Separated from the player service to allow independent scaling --
// search is read-heavy and can be horizontally scaled behind Kong
// without affecting the player management write path.

import {
  CqrsServer,
  defaultCqrsServerOptions,
} from '@acme.bo/services/cqrs-server';
import { PlayerSearchQueryHandler } from './queries/playerSearchQuery';

// Ensure handler is imported (triggers @registerQueryHandler decorator)
const _handler = PlayerSearchQueryHandler.length;

process.chdir(__dirname);

const server = new CqrsServer();

server.start({
  serviceName: 'search_service',
  registerWithKong: true,
  disableAuth: true,
  overwriteExistingService: true,
});
