// Companion code for "The Backend of Luck" - Chapter 08, Team Structure and Operations.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// AcmetoCasino Backoffice - API Router
// Express route configuration with module-level access control

import * as express from 'express';
import { Database } from '../database';
import { mustHaveModuleAccess } from '../auth';

import { workFlowRouter } from './workflow';
import { customerRouter } from './customer';
import { adminRouter } from './admin';

export const apiRouter = express.Router();

// Workflow routes require complaints module access
apiRouter.use('/workflow', mustHaveModuleAccess('customers.complaints'), workFlowRouter);
apiRouter.use('/customer', customerRouter);
apiRouter.use('/admin', adminRouter);

export default apiRouter;
