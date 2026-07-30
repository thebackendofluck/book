// Companion code for "The Backend of Luck" - Chapter 08, Team Structure and Operations.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// AcmetoCasino Backoffice - Player Info Query Handler
// CQRS query handler that aggregates player data from multiple sources:
// 1. Legacy platform API (player profile, balance, KYC, marketing prefs)
// 2. PostgreSQL database (additional info, flags with permission-based visibility)
//
// This demonstrates a common migration pattern in iGaming: the new backoffice
// queries both the legacy monolith API and the new database, gradually shifting
// data ownership as features are migrated.
//
// The flag permission system is noteworthy: flags can be restricted by role,
// so a junior agent might not see sensitive compliance flags that are visible
// to senior compliance officers.

import {
  IQueryHandler,
  IQuery,
  registerQueryHandler,
} from '@acme.bo/services/cqrs-server';
import { LegacyPlatformService } from '@acme.bo/services/legacy-platform-service';
import { injectable } from 'tsyringe';
import { PlatformDatabase } from '@acme.bo/services/platform-database';

export interface GetPlayerInfoQuery extends IQuery<Player> {
  id: number;
  brandId: number;
  adminId: number;
}

export interface Player {
  id: number;
  firstName: string;
  lastName: string;
  gender: string;
  birthDate: string;
  salutation: string;
  address1: string;
  address2: string;
  city: string;
  zipCode: string;
  state: string;
  country: string;
  email: string;
  phonePrefix: string;
  phone: number;
  language: string;
  currency: string;
  isActive: boolean;
  isLocked: boolean;
  isFunded: boolean;
  hasExcludedMarketing: boolean;
  balance: number;
  bonusBalance: number;
  xpLevel: number;
  xpPoint: number;
  brand: string;
  registrationDate: string;
  lastLogin: string;
  depositToBonusRatio: number;
  communicationPreferences: CommunicationPreferences;
  flags: Flag[];
  verificationInfo: VerificationInfo;
  limits: Limits;
  depositInfo: OperationInfo;
  withdrawalInfo: OperationInfo;
}

export interface Flag {
  id: number;
  name: string;
  description: string;
  isGlobal: boolean;
  isBlockBonuses: boolean;
  isExcludedFromMarketing: boolean;
  isBlockWithdrawals: boolean;
  isBlockAwards: boolean;
  isSystem: boolean;
  hasFrame: boolean;
  addedBy: string;
  reason: string;
  date: string;
}

export interface CommunicationPreferences {
  brandMarketing: MarketingPreferences;
  crossMarketing: MarketingPreferences;
}

export interface MarketingPreferences {
  isEmail: boolean;
  isPhone: boolean;
  isSms: boolean;
  isPost: boolean;
}

export interface VerificationInfo {
  kycStatus: string;
  isKycApproved: boolean;
  kycIdStatus: string;
  kycAddressStatus: string;
  pepStatus: string;
  isPepApproved: boolean;
  isIdCheck: boolean;
}

export interface Limit {
  period: string;
  value: number;
  default: boolean;
}

export interface Limits {
  sessionDuration: Limit;
  deposit: Limit;
  monthlyDeposit: Limit;
  weeklyDeposit: Limit;
  dailyDeposit: Limit;
  hardDeposit: Limit;
  singleWager: Limit;
  twilightDeposit: Limit;
  withdrawal: Limit;
}

export interface OperationInfo {
  count: number;
  total: number;
  first: string;
  last: string;
}

@registerQueryHandler()
export class GetPlayerInfoQueryHandler extends IQueryHandler<
  GetPlayerInfoQuery,
  Player
> {
  constructor(
    private legacyPlatformService: LegacyPlatformService,
    private client: PlatformDatabase
  ) {
    super();
  }

  async Handle(query: GetPlayerInfoQuery): Promise<Player> {
    // Aggregate data from legacy API and new database
    const pUserData = await this.legacyPlatformService.getPlayerInfo(
      query.id, query.brandId
    );
    const pBalanceData = await this.legacyPlatformService.getPlayerBalance(
      query.id, query.brandId
    );
    const pShuftiproData = await this.legacyPlatformService.getShuftiproStatus(
      query.id
    );
    const pMarketingData = await this.legacyPlatformService.getPlayerMarketing(
      query.id, query.brandId, query.adminId
    );

    const dbPlayerData = await this.getPlayerAdditionalInfo(query.id);
    const dbflagsData = await this.getPlayerFlagsInfo(
      query.id, query.adminId, pUserData.flags
    );

    return {
      id: pUserData.userid,
      firstName: pUserData.firstname,
      lastName: pUserData.lastname,
      gender: pUserData.gender,
      birthDate: pUserData.dob,
      salutation: dbPlayerData.salutation,
      address1: pUserData.address1,
      address2: pUserData.address2,
      city: pUserData.town,
      zipCode: pUserData.postalcode,
      state: pUserData.state,
      country: pUserData.country,
      email: pUserData.email,
      phonePrefix: pUserData.phonePrefix,
      phone: pUserData.phone,
      language: pUserData.language,
      currency: pUserData.currency,
      isActive: pUserData.activated,
      isLocked: pUserData.locked,
      isFunded: pUserData.funded,
      hasExcludedMarketing: pUserData.excludeFromMarketing,
      balance: pBalanceData.balances.cash,
      bonusBalance: pBalanceData.balances.bonus,
      xpLevel: pBalanceData.experience.level,
      xpPoint: pBalanceData.experience.points,
      brand: pUserData.brand.title,
      registrationDate: pUserData.created,
      lastLogin: pUserData.lastlogin,
      depositToBonusRatio: 1,
      communicationPreferences: {
        brandMarketing: pMarketingData?.marketingPrefs?.brandMarketing,
        crossMarketing: pMarketingData?.marketingPrefs?.crossMarketing,
      },
      flags: dbflagsData,
      verificationInfo: {
        kycStatus: pUserData.kycStatus,
        isKycApproved: pUserData.kycApproved,
        kycIdStatus: pShuftiproData.idStatus,
        kycAddressStatus: pShuftiproData.addressStatus,
        pepStatus: pUserData.pepStatus,
        isPepApproved: pUserData.pepApproved,
        isIdCheck: pUserData.idChecked,
      },
      limits: {
        sessionDuration: pUserData.sessiondurationlimit,
        deposit: pUserData.depositlimit,
        monthlyDeposit: pUserData.monthlydepositlimit,
        weeklyDeposit: pUserData.weeklydepositlimit,
        dailyDeposit: pUserData.dailydepositlimit,
        hardDeposit: pUserData.harddepositlimit,
        singleWager: pUserData.singlewagerlimit,
        twilightDeposit: pUserData.twilightdepositlimit,
        withdrawal: pUserData.withdrawallimit,
      },
      depositInfo: pBalanceData.depositInfo,
      withdrawalInfo: pBalanceData.withdrawalInfo,
    };
  }

  // Flags are filtered by the admin user's role permissions.
  // Permission levels:
  //   0-1: visible to all agents
  //   2+:  requires explicit role grant (can_view = 1)
  async getPlayerFlagsInfo(
    uid: number, adminId: number, flags: any[]
  ): Promise<Flag[]> {
    if (!flags || !flags.length) return [];

    const queryTxt = `
      SELECT
        f.id, f.name, f.description, f.global, f.block_bonuses,
        f.exclude_from_marketing, f.disallow_cashouts, f.block_awards,
        f.is_system, f.flag_frame, au.username, cc.entry, uf.created
      FROM platform.flags f
        INNER JOIN platform.user_flags uf
          ON uf.flag_id = f.id AND uf.user_id = $2
        INNER JOIN backoffice.admin_user au
          ON uf.admin_user_id = au.id
        INNER JOIN backoffice.customer_comments cc
          ON uf.comment_id = cc.id
      WHERE f.id IN ($1)
        AND (
          COALESCE((
            SELECT permission_config_level
            FROM backoffice.flag_permission_config
            WHERE flag_id = f.id
          ), 0) < 2
          OR COALESCE((
            SELECT MAX(COALESCE(rf.can_view, 0))
            FROM backoffice.flag_permission_config fp
            INNER JOIN backoffice.role_flag rf ON fp.flag_id = rf.flag_id
            WHERE fp.flag_id = f.id
              AND rf.role_id IN (
                SELECT granted_role
                FROM backoffice.admin_user_role
                WHERE granted_to = $3
              )
          ), 0) = 1
        )
    `;

    const flagIds = flags.map(v => v.id).join();
    const pgQuery = {
      text: queryTxt,
      values: [flagIds, uid, adminId],
    };

    const res = await this.client.query(pgQuery);
    return res.rows.map(v => ({
      id: v.id,
      name: v.name,
      description: v.description,
      isGlobal: v.global,
      isBlockBonuses: v.block_bonuses,
      isExcludedFromMarketing: v.exclude_from_marketing,
      isBlockWithdrawals: v.disallow_cashouts,
      isBlockAwards: v.block_awards,
      isSystem: v.is_system,
      hasFrame: v.flag_frame,
      addedBy: v.username,
      reason: v.entry,
      date: v.created,
    }));
  }

  async getPlayerAdditionalInfo(uid: number) {
    const queryTxt = `
      SELECT ui.salutation
      FROM platform.users u
        INNER JOIN platform.user_info ui ON ui.userid = u.id
      WHERE u.id = $1
    `;

    const pgQuery = {
      text: queryTxt,
      values: [uid],
    };

    const res = await this.client.query(pgQuery);
    return res.rows[0];
  }
}
