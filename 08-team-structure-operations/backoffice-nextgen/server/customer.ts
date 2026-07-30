// Companion code for "The Backend of Luck" - Chapter 08, Team Structure and Operations.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// AcmetoCasino Backoffice - Customer Search API
// Multi-brand customer lookup with flag types

import * as express from 'express';
import { Database } from '../database';
import { CustomerSearchResult } from '../../../shared/customer/models/customerSearchResult';
import { CustomerFlagType } from '../../../shared/customer/models/customerFlagType';
import { AuthenticatedResponse } from '../models/AuthenticatedResponse';
import * as xml2js from 'xml2js';

export const customerRouter = express.Router();

// Get customer flag types (VIP, self-excluded, etc.)
customerRouter.get('/flagTypes', async function (req, res: AuthenticatedResponse) {
  let dbItems = await res.locals.database.runQuery('customer', 'getFlags', {});

  let parsedItems: CustomerFlagType[] = [];
  for (let dbItem of dbItems) {
    parsedItems.push(
      new CustomerFlagType({
        id: dbItem.ID,
        name: dbItem.NAME,
        colour: dbItem.COLOUR,
      })
    );
  }

  res.json(parsedItems);
});

// Search customers by ID or text (scoped to user's brands)
customerRouter.get('/search', async function (req, res: AuthenticatedResponse) {
  let searchText = req.query.searchText;
  let id = parseInt(searchText);

  if (isNaN(id)) {
    id = null;
  } else {
    searchText = null;
  }

  let db = res.locals.database;

  // Brand-scoped search: user can only see customers on their assigned brands
  let dbItems = await db.runQuery('customer', 'find', {
    id: id,
    search: searchText,
    brands: res.locals.user.brandCsv,
  });

  let parsedItems: CustomerSearchResult[] = [];

  for (let dbItem of dbItems) {
    parsedItems.push(
      new CustomerSearchResult({
        brand: dbItem.BRAND,
        brandId: dbItem.BRAND_ID,
        id: dbItem.ID,
        username: dbItem.USERNAME,
        firstname: dbItem.FIRSTNAME,
        lastname: dbItem.LASTNAME,
        email: dbItem.EMAIL,
        country: dbItem.COUNTRY,
        created: dbItem.CREATE_DATE,
        lastLogin: dbItem.LAST_LOGIN,
      })
    );
  }

  res.json(parsedItems);
});
