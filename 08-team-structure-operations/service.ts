// Companion code for "The Backend of Luck" - Chapter 08, Team Structure and Operations.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * service.ts — Player Management Service
 *
 * Angular injectable service providing player account management operations
 * for the backoffice. Implements the platform-to-backoffice service boundary
 * described in Chapter 8: what the backoffice can READ vs what it can MUTATE,
 * and which service enforces the permission boundary.
 *
 * All mutations create audit trail entries. All actions are scoped by the
 * operator's permission claims in their JWT.
 *
 * Chapter 8 — Team Structure and Operations
 */

import { Injectable } from '@angular/core';
import { HttpClient, HttpParams, HttpHeaders } from '@angular/common/http';
import { Observable, throwError } from 'rxjs';
import { map, catchError, tap } from 'rxjs/operators';
import { environment } from '../environments/environment';

// ---------------------------------------------------------------------------
// Shared type definitions (player.models.ts in production)
// ---------------------------------------------------------------------------

export type PlayerOperationalState =
  | 'authenticated'
  | 'launch-eligible'
  | 'wallet-eligible'
  | 'kyc-approved'
  | 'payment-ready'
  | 'rg-limited'
  | 'support-escalated';

export type KycStatus = 'approved' | 'pending' | 'rejected' | 'not_started';

export type AccountLockType =
  | 'SELF_EXCLUDE'
  | 'OPERATOR_SE'
  | 'TEMPORARY'
  | 'AWAITING_KYC'
  | 'MATCHED_AWAITING_KYC'
  | 'BANNED_FRAUD'
  | 'DUPLICATE'
  | 'DORMANT_USER'
  | 'RG3';

export type RgLimitType =
  | 'deposit_daily'
  | 'deposit_weekly'
  | 'deposit_monthly'
  | 'loss_daily'
  | 'loss_weekly'
  | 'loss_monthly'
  | 'session_time'
  | 'wager_daily';

export interface AccountLock {
  id: string;
  lockType: AccountLockType;
  appliedAt: string;
  appliedBy: string;
  expiryDate?: string;
  reason: string;
  isActive: boolean;
}

export interface RgLimit {
  id: string;
  limitType: RgLimitType;
  value: number;
  currency?: string;
  effectiveFrom: string;
  effectiveTo?: string;
  appliedBy: string;
  isActive: boolean;
}

export interface KycDocument {
  id: string;
  documentType: string;
  status: KycStatus;
  uploadedAt: string;
  reviewedAt?: string;
  reviewedBy?: string;
  notes?: string;
}

export interface PlayerAccount {
  id: string;
  username: string;
  email: string;
  jurisdiction: string;
  registeredAt: string;
  lastLoginAt?: string;
  operationalStates: PlayerOperationalState[];
  kycStatus: KycStatus;
  activeLocks: AccountLock[];
  rgLimits: RgLimit[];
  kycDocuments?: KycDocument[];
  balanceCents: number;
  bonusBalanceCents: number;
  currency: string;
  isTestAccount: boolean;
  uniformPatronIdentifier?: string; // DUPI for DGE reporting
}

export interface PlayerSearchParams {
  query: string;
  page?: number;
  pageSize?: number;
  status?: string;
  kycStatus?: KycStatus;
  jurisdiction?: string;
}

export interface PagedResult<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
}

export interface ApplyLockRequest {
  lockType: AccountLockType;
  reason: string;
  appliedBy: string;
  expiryDate?: Date;
}

export interface ApplyRgLimitRequest {
  limitType: RgLimitType;
  value: number;
  currency?: string;
  appliedBy: string;
}

export interface KycReviewRequest {
  reviewType: 'standard' | 'enhanced';
  notes: string;
}

interface ApiResponse<T> {
  success: boolean;
  data: T;
  error?: string;
}

// ---------------------------------------------------------------------------
// Service implementation
// ---------------------------------------------------------------------------

@Injectable({
  providedIn: 'root',
})
export class PlayerManagementService {
  private readonly baseUrl: string;
  private readonly apiVersion = 'v2';

  constructor(private readonly http: HttpClient) {
    this.baseUrl = environment.apiBaseUrl || '/api';
  }

  // ---------------------------------------------------------------------------
  // Read operations (no audit trail required)
  // ---------------------------------------------------------------------------

  /**
   * Search players by username, email, or player ID.
   * Requires backoffice READ_PLAYERS permission.
   */
  searchPlayers(params: PlayerSearchParams): Observable<PagedResult<PlayerAccount>> {
    let httpParams = new HttpParams()
      .set('q', params.query)
      .set('page', String(params.page ?? 1))
      .set('pageSize', String(params.pageSize ?? 25));

    if (params.status) httpParams = httpParams.set('status', params.status);
    if (params.kycStatus) httpParams = httpParams.set('kycStatus', params.kycStatus);
    if (params.jurisdiction) httpParams = httpParams.set('jurisdiction', params.jurisdiction);

    return this.http.get<ApiResponse<PagedResult<PlayerAccount>>>(
      `${this.baseUrl}/${this.apiVersion}/backoffice/players/search`,
      { params: httpParams }
    ).pipe(
      map((res) => this._unwrap(res)),
      catchError((err) => throwError(() => this._mapError(err)))
    );
  }

  /**
   * Get complete player detail including KYC documents, active locks, and RG limits.
   * Requires backoffice READ_PLAYER_DETAIL permission.
   */
  getPlayerDetail(playerId: string): Observable<PlayerAccount> {
    return this.http.get<ApiResponse<PlayerAccount>>(
      `${this.baseUrl}/${this.apiVersion}/backoffice/players/${encodeURIComponent(playerId)}`
    ).pipe(
      map((res) => this._unwrap(res)),
      catchError((err) => throwError(() => this._mapError(err)))
    );
  }

  /**
   * Get transaction history for a player.
   * Requires backoffice READ_TRANSACTIONS permission.
   */
  getPlayerTransactions(
    playerId: string,
    fromDate: string,
    toDate: string,
    page = 1,
    pageSize = 50
  ): Observable<PagedResult<Record<string, unknown>>> {
    const params = new HttpParams()
      .set('fromDate', fromDate)
      .set('toDate', toDate)
      .set('page', String(page))
      .set('pageSize', String(pageSize));

    return this.http.get<ApiResponse<PagedResult<Record<string, unknown>>>>(
      `${this.baseUrl}/${this.apiVersion}/backoffice/players/${encodeURIComponent(playerId)}/transactions`,
      { params }
    ).pipe(
      map((res) => this._unwrap(res)),
      catchError((err) => throwError(() => this._mapError(err)))
    );
  }

  // ---------------------------------------------------------------------------
  // Mutation operations (all create audit trail entries)
  // ---------------------------------------------------------------------------

  /**
   * Apply an account lock.
   * Requires APPLY_ACCOUNT_LOCK permission.
   * Creates mandatory audit trail entry with operator ID and reason.
   *
   * Note: SELF_EXCLUDE locks (GDPR/LCCP) cannot be removed by operators —
   * only by the player through the self-exclusion scheme.
   */
  applyAccountLock(playerId: string, request: ApplyLockRequest): Observable<PlayerAccount> {
    return this.http.post<ApiResponse<PlayerAccount>>(
      `${this.baseUrl}/${this.apiVersion}/backoffice/players/${encodeURIComponent(playerId)}/locks`,
      {
        lockType: request.lockType,
        reason: request.reason,
        expiryDate: request.expiryDate?.toISOString(),
      }
    ).pipe(
      tap(() => { /* audit log handled server-side */ }),
      map((res) => this._unwrap(res)),
      catchError((err) => throwError(() => this._mapError(err)))
    );
  }

  /**
   * Remove an account lock.
   * Requires REMOVE_ACCOUNT_LOCK permission.
   * Will reject SELF_EXCLUDE and OPERATOR_SE locks (requires compliance approval).
   */
  removeAccountLock(
    playerId: string,
    lockId: string,
    reason: string
  ): Observable<PlayerAccount> {
    return this.http.delete<ApiResponse<PlayerAccount>>(
      `${this.baseUrl}/${this.apiVersion}/backoffice/players/${encodeURIComponent(playerId)}/locks/${encodeURIComponent(lockId)}`,
      { body: { reason } }
    ).pipe(
      map((res) => this._unwrap(res)),
      catchError((err) => throwError(() => this._mapError(err)))
    );
  }

  /**
   * Apply a responsible gaming limit.
   * Requires APPLY_RG_LIMITS permission.
   *
   * Note: Limit reductions take effect immediately (UKGC LCCP requirement).
   * Limit increases are subject to a 24-hour cooling-off period.
   */
  applyRgLimit(playerId: string, request: ApplyRgLimitRequest): Observable<PlayerAccount> {
    return this.http.post<ApiResponse<PlayerAccount>>(
      `${this.baseUrl}/${this.apiVersion}/backoffice/players/${encodeURIComponent(playerId)}/rg-limits`,
      {
        limitType: request.limitType,
        value: request.value,
        currency: request.currency,
      }
    ).pipe(
      map((res) => this._unwrap(res)),
      catchError((err) => throwError(() => this._mapError(err)))
    );
  }

  /**
   * Remove an active RG limit.
   * Requires REMOVE_RG_LIMITS permission.
   * Subject to cooling-off period enforcement at the platform level.
   */
  removeRgLimit(
    playerId: string,
    limitId: string,
    reason: string
  ): Observable<PlayerAccount> {
    return this.http.delete<ApiResponse<PlayerAccount>>(
      `${this.baseUrl}/${this.apiVersion}/backoffice/players/${encodeURIComponent(playerId)}/rg-limits/${encodeURIComponent(limitId)}`,
      { body: { reason } }
    ).pipe(
      map((res) => this._unwrap(res)),
      catchError((err) => throwError(() => this._mapError(err)))
    );
  }

  /**
   * Trigger a KYC review.
   * Requires TRIGGER_KYC_REVIEW permission.
   * Creates a compliance task and notifies the KYC team.
   */
  triggerKycReview(
    playerId: string,
    request: KycReviewRequest
  ): Observable<PlayerAccount> {
    return this.http.post<ApiResponse<PlayerAccount>>(
      `${this.baseUrl}/${this.apiVersion}/backoffice/players/${encodeURIComponent(playerId)}/kyc/review`,
      request
    ).pipe(
      map((res) => this._unwrap(res)),
      catchError((err) => throwError(() => this._mapError(err)))
    );
  }

  /**
   * Escalate player account to support.
   * Adds 'support-escalated' operational state and creates a support ticket.
   */
  escalateToSupport(
    playerId: string,
    reason: string,
    priority: 'low' | 'medium' | 'high' = 'medium'
  ): Observable<PlayerAccount> {
    return this.http.post<ApiResponse<PlayerAccount>>(
      `${this.baseUrl}/${this.apiVersion}/backoffice/players/${encodeURIComponent(playerId)}/escalate`,
      { reason, priority }
    ).pipe(
      map((res) => this._unwrap(res)),
      catchError((err) => throwError(() => this._mapError(err)))
    );
  }

  /**
   * Reset player's operational state to platform-calculated default.
   * Used to clear stale 'support-escalated' states after resolution.
   */
  clearSupportEscalation(
    playerId: string,
    resolutionNotes: string
  ): Observable<PlayerAccount> {
    return this.http.post<ApiResponse<PlayerAccount>>(
      `${this.baseUrl}/${this.apiVersion}/backoffice/players/${encodeURIComponent(playerId)}/escalate/resolve`,
      { resolutionNotes }
    ).pipe(
      map((res) => this._unwrap(res)),
      catchError((err) => throwError(() => this._mapError(err)))
    );
  }

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  private _unwrap<T>(response: ApiResponse<T>): T {
    if (!response.success) {
      throw new Error(response.error ?? 'API returned unsuccessful response');
    }
    return response.data;
  }

  private _mapError(err: unknown): Error {
    if (err instanceof Error) return err;
    const httpErr = err as { status?: number; error?: { error?: string; message?: string } };
    const message = httpErr?.error?.error
      ?? httpErr?.error?.message
      ?? `HTTP ${httpErr?.status ?? 'unknown'} error`;
    return new Error(message);
  }
}
