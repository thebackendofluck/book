// Companion code for "The Backend of Luck" - Chapter 08, Team Structure and Operations.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// AcmetoCasino Backoffice - Player Search Query Handler
// Advanced player search with dynamic filter composition.
// Builds parameterized SQL queries from search criteria including:
// keyword (name/email/phone/address), brands, countries, flags,
// funded status, test accounts, and pagination.
//
// This query runs against the platform's PostgreSQL database and
// joins multiple tables to produce a comprehensive search result.
// The approach of building filter clauses dynamically (rather than
// using an ORM) gives precise control over query performance --
// critical when searching across millions of player records.

import {
  IQueryHandler,
  IQuery,
  registerQueryHandler,
} from '@acme.bo/services/cqrs-server';
import { PlatformDatabase } from '@acme.bo/services/platform-database';

export interface PlayerSearchQuery extends IQuery<PlayerSearchResult> {
  keyword: string;
  brandsList: string[];
  countriesList: string[];
  paymentMethodsList: string[];
  paymentId: string;
  locksList: string[];
  flaggedStatusesList: string[];
  interactionsList: string[];
  registrationDateFrom: string;
  registrationDateTo: string;
  fundedsList: string[];
  testAccountsList: string[];
  Pagination: PaginationRequest;
}

export interface PlayerSearchResult {
  result: PlayerSearchData[];
  pagination: Pagination;
}

@registerQueryHandler()
export class PlayerSearchQueryHandler extends IQueryHandler<
  PlayerSearchQuery,
  PlayerSearchResult
> {
  params: Array<any> = [];

  constructor(private client: PlatformDatabase) {
    super();
  }

  async Handle(query: PlayerSearchQuery): Promise<PlayerSearchResult> {
    const queryTxt = await this.prepareQuery(query);
    const params = this.getParams();
    const pgQuery = { text: queryTxt, values: params };
    const res = await this.client.query(pgQuery);

    const queryCountTxt = await this.countQuery(query);
    const pgCountQuery = { text: queryCountTxt, values: params };
    const resCount = await this.client.query(pgCountQuery);

    const per_page = Math.min(Math.max(query.Pagination.perPage, 1), 25);

    return {
      result: res.rows,
      pagination: {
        perPage: per_page,
        page: query.Pagination.page,
        total: resCount.rows[0] ? resCount.rows[0].total : 0,
        pages: Math.ceil(
          (resCount.rows[0] ? resCount.rows[0].total : 0) / per_page
        ),
      },
    };
  }

  getParams() {
    return this.params;
  }

  // Keyword search across 9 player fields using LIKE with parameterized values
  keywordQuery(keyword: string) {
    if (!keyword || keyword.length === 0) return '';

    this.params.push(`%${keyword}%`);
    return ` AND (
      LOWER(u.name) LIKE LOWER($${this.params.length}) OR
      LOWER(ui.firstname) LIKE LOWER($${this.params.length}) OR
      LOWER(ui.email) LIKE LOWER($${this.params.length}) OR
      LOWER(ui.lastname) LIKE LOWER($${this.params.length}) OR
      ui.phone LIKE $${this.params.length} OR
      LOWER(ui.town) LIKE LOWER($${this.params.length}) OR
      LOWER(ui.address1) LIKE LOWER($${this.params.length}) OR
      LOWER(ui.address2) LIKE LOWER($${this.params.length}) OR
      LOWER(ui.postalcode) LIKE LOWER($${this.params.length})
    )`;
  }

  // Brand filter: resolves brand names to IDs via a sub-query
  async brandsQuery(brands: Array<string>) {
    if (!brands || brands.length === 0) return '';

    const pgQuery = {
      text: `SELECT a.id FROM platform.brands a
             WHERE a.name = ANY($1) AND a.is_enabled = true`,
      values: [brands],
      rowMode: 'array',
    };
    const brandsDB = await this.client.query(pgQuery);
    if (brandsDB.rows.length > 0) {
      return ` AND u.affiliateid IN (${brandsDB.rows.toString()}) `;
    }
    return '';
  }

  countriesQuery(countries: Array<string>) {
    if (!countries || countries.length === 0) return '';
    this.params.push(countries);
    return ` AND ui.country = ANY ($${this.params.length}) `;
  }

  flaggedStatusesQuery(flags: Array<string>) {
    if (!flags || flags.length === 0) return '';
    this.params.push(flags);
    return ` AND u.id IN (
      SELECT user_id FROM platform.user_flags
      WHERE flag_id = ANY($${this.params.length}::int[]) AND value = true
    )`;
  }

  fundedsQuery(fundeds: Array<string>) {
    if (!fundeds || fundeds.length === 0) return '';
    return ` AND u.funded = ${stringToBoolean(fundeds[0])} `;
  }

  testAccountsQuery(testAccounts: Array<string>) {
    if (!testAccounts || testAccounts.length === 0) return '';
    return ` AND ui.testaccount = ${stringToBoolean(testAccounts[0])} `;
  }

  setPagination(query: PlayerSearchQuery) {
    const per_page = Math.min(Math.max(query.Pagination.perPage, 1), 25);
    const page = query.Pagination.page > 0 ? query.Pagination.page : 1;
    return ` OFFSET ${(page - 1) * per_page} LIMIT ${per_page}`;
  }

  async generateFilters(query: PlayerSearchQuery) {
    this.params = [];
    let filterQuery = '';
    filterQuery += this.keywordQuery(query.keyword);
    filterQuery += await this.brandsQuery(query.brandsList);
    filterQuery += this.countriesQuery(query.countriesList);
    filterQuery += this.flaggedStatusesQuery(query.flaggedStatusesList);
    filterQuery += this.fundedsQuery(query.fundedsList);
    filterQuery += this.testAccountsQuery(query.testAccountsList);
    return filterQuery;
  }

  async prepareQuery(query: PlayerSearchQuery) {
    const filterQuery = await this.generateFilters(query);
    const paginationQuery = this.setPagination(query);

    return `SELECT
        u.id,
        a.name AS brand,
        u.name AS username,
        ac.balance,
        ac.bonusbalance,
        ac.currency,
        u.funded,
        u.locked,
        CASE WHEN EXISTS(
          SELECT * FROM platform.user_lock
          WHERE USER_ID = u.id
            AND status NOT IN ('COMPLETED','CANCELLED')
            AND LOCK_TYPE_ID IN (
              SELECT id FROM platform.user_lock_type
              WHERE RESPONSIBLE_GAMBLING = true
            )
        ) THEN 1 ELSE 0 END rg_locked,
        u.activated,
        p.id AS game_user_id,
        ui.phone, ui.firstname, ui.lastname, ui.email, ui.country,
        ui.registration_jurisdiction_id AS state,
        ui.current_jurisdiction,
        ui.created AS create_date,
        ui.lastlogin AS last_login,
        ur.referrer AS affiliate,
        ur.clickid
      FROM platform.user_info ui
      JOIN platform.users u ON u.id = ui.userid
      LEFT JOIN platform.players p
        ON p.username = u.name AND p.brand_id = u.affiliateid
      LEFT JOIN platform.user_referral ur ON ur.userid = u.id
      LEFT JOIN platform.countries c ON ui.country = c.country
      JOIN platform.brands a ON a.id = u.affiliateid
      JOIN (
        SELECT
          ac.userid,
          SUM(CASE WHEN ac.typeid = 1 THEN ac.balance/100 ELSE 0 END) AS balance,
          SUM(CASE WHEN ac.typeid = 2 THEN ac.balance/100 ELSE 0 END) AS bonusbalance,
          ac.currency
        FROM platform.user_accounts ac
        JOIN platform.users u ON ac.userid = u.id
        JOIN platform.user_info ui ON ui.userid = u.id
        WHERE ac.typeid < 100
          ${filterQuery}
        GROUP BY ac.userid, ac.currency
      ) ac ON u.id = ac.userid
      ORDER BY ui.created DESC ${paginationQuery}`;
  }

  async countQuery(query: PlayerSearchQuery) {
    const filterQuery = await this.generateFilters(query);

    return `SELECT COUNT(u.id) AS total
      FROM platform.user_info ui
      JOIN platform.users u ON u.id = ui.userid
      LEFT JOIN platform.players p
        ON p.username = u.name AND p.brand_id = u.affiliateid
      LEFT JOIN platform.user_referral ur ON ur.userid = u.id
      LEFT JOIN platform.countries c ON ui.country = c.country
      JOIN platform.brands a ON a.id = u.affiliateid
      JOIN (
        SELECT
          ac.userid,
          SUM(CASE WHEN ac.typeid = 1 THEN ac.balance/100 ELSE 0 END) AS balance,
          SUM(CASE WHEN ac.typeid = 2 THEN ac.balance/100 ELSE 0 END) AS bonusbalance,
          ac.currency
        FROM platform.user_accounts ac
        JOIN platform.users u ON ac.userid = u.id
        JOIN platform.user_info ui ON ui.userid = u.id
        WHERE ac.typeid < 100
          ${filterQuery}
        GROUP BY ac.userid, ac.currency
      ) ac ON u.id = ac.userid`;
  }
}

function stringToBoolean(value: string): boolean {
  switch (value.toLowerCase().trim()) {
    case 'true': case 'yes': case '1': return true;
    case 'false': case 'no': case '0': case null: return false;
    default: return Boolean(value);
  }
}

export interface PlayerSearchData {
  id: number;
  brand: string;
  username: string;
  balance: string;
  bonusbalance: string;
  currency: string;
  funded: string;
  locked: string;
  activated: string;
  country: string;
  email: string;
  firstname: string;
  lastname: string;
  phone: string;
  create_date: string;
  last_login: string;
  affiliate: string;
}

export interface Pagination {
  perPage: number;
  page: number;
  total: number;
  pages: number;
}

export interface PaginationRequest {
  perPage: number;
  page: number;
}
