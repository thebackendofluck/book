// Companion code for "The Backend of Luck" - Chapter 08, Team Structure and Operations.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// AcmetoCasino Backoffice - Authentication & Authorization Middleware
// Three-layer auth: session validation, module access, permission checks

import { Database } from './database';
import { UserSession } from './models/userSession';
import { AuthenticatedResponse } from './models/AuthenticatedResponse';
import * as express from 'express';

export async function authenticate(
  req: express.Request,
  res: AuthenticatedResponse,
  next: express.NextFunction
) {
  let sid: string = req.get('authorization') || req.body.sid || req.query.sid;

  if (!sid) {
    res.status(403);
    res.send('Missing user credentials');
    return;
  }

  // Strip Basic auth prefix if present
  if (sid.substr(0, 5) === 'Basic') {
    sid = sid.substr(6);
  }

  let db = res.locals.database;

  try {
    // Validate session against database
    let dbItems = await db.runQuery('auth', 'getCurrentSession', { id: sid });

    if (!dbItems || dbItems.length === 0) {
      send403(res);
    } else {
      let dbData = dbItems[0];

      res.locals.user = new UserSession({
        id: dbData.ID,
        loggedIn: dbData.LOGIN,
        deviceId: dbData.DEVICE_ID,
        userId: dbData.USER_ID,
        lastSeen: dbData.LAST_SEEN,
        lastPage: dbData.LAST_PAGE,
        username: dbData.USERNAME,
        email: dbData.EMAIL,
        firstName: dbData.FIRST_NAME,
        lastName: dbData.LAST_NAME,
        canExport: dbData.IS_EXPORT,
      });

      // Load user's brands, permissions, and module access
      let dbBrands: Array<{ ID: number }> = await db.runQuery('auth', 'getUserBrands', {
        id: res.locals.user.userId,
      });
      let perms: Array<{ GRANTED_PERMISSION: string }> = await db.runQuery(
        'auth',
        'getUserPerms',
        { id: res.locals.user.userId }
      );
      let modules: Array<{ SECTION_MODULE: string }> = await db.runQuery(
        'auth',
        'getUserModules',
        { id: res.locals.user.userId }
      );

      res.locals.user.brands = dbBrands ? dbBrands.map((i) => +i.ID) : [];
      res.locals.user.modules = modules ? modules.map((i) => i.SECTION_MODULE) : [];
      res.locals.user.permissions = perms ? perms.map((i) => i.GRANTED_PERMISSION) : [];

      next();
    }
  } catch (e) {
    next(e);
  }
}

// Middleware: require module-level access
export function mustHaveModuleAccess(...moduleNames: string[]) {
  return function (
    req: express.Request,
    res: AuthenticatedResponse,
    next: express.NextFunction
  ) {
    for (let mn of moduleNames) {
      if (res.locals.user.modules.some((m) => m === mn)) {
        next();
        return;
      }
    }
    send403(res, 'Module Access Denied');
  };
}

// Middleware: require specific permission
export function mustHavePermission(...permissionNames: string[]) {
  return function (
    req: express.Request,
    res: AuthenticatedResponse,
    next: express.NextFunction
  ) {
    for (let pn of permissionNames) {
      if (res.locals.user.permissions.some((p) => p === pn)) {
        next();
        return;
      }
    }
    send403(res, 'Permission Access Denied');
  };
}

function send403(res: AuthenticatedResponse, message = 'Invalid user credentials') {
  res.status(403);
  res.send(message);
}
