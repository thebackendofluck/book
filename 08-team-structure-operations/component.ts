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
 * component.ts — Player Account Management Backoffice Component
 *
 * Angular component providing the backoffice player management interface.
 * Handles player search, account status, KYC state, responsible gaming limits,
 * and account lock/unlock operations.
 *
 * Chapter 8 — Team Structure and Operations
 * Demonstrates the shared state vocabulary and service boundary patterns
 * described in the chapter's backoffice modernisation section.
 */

import { Component, OnInit, OnDestroy } from '@angular/core';
import { Subject, Observable, combineLatest } from 'rxjs';
import {
  debounceTime,
  distinctUntilChanged,
  switchMap,
  takeUntil,
  catchError,
  finalize,
} from 'rxjs/operators';
import { of } from 'rxjs';

import { PlayerManagementService } from './service';
import {
  PlayerAccount,
  PlayerOperationalState,
  AccountLockType,
  KycStatus,
  RgLimitType,
  PlayerSearchParams,
  PagedResult,
} from './player.models';

/**
 * Shared state vocabulary (Chapter 8 — Shared State Vocabulary section).
 * Every surface in the platform uses these values — never ad-hoc strings.
 */
export const OPERATIONAL_STATES: Record<PlayerOperationalState, string> = {
  authenticated: 'Authenticated',
  'launch-eligible': 'Launch Eligible',
  'wallet-eligible': 'Wallet Eligible',
  'kyc-approved': 'KYC Approved',
  'payment-ready': 'Payment Ready',
  'rg-limited': 'RG Limited',
  'support-escalated': 'Support Escalated',
};

@Component({
  selector: 'app-player-management',
  templateUrl: './component.html',
  styleUrls: ['./component.css'],
})
export class PlayerManagementComponent implements OnInit, OnDestroy {
  // Search state
  searchQuery = '';
  searchResults: PlayerAccount[] = [];
  isSearching = false;
  searchError: string | null = null;
  currentPage = 1;
  pageSize = 25;
  totalResults = 0;

  // Selected player state
  selectedPlayer: PlayerAccount | null = null;
  isLoadingPlayer = false;
  playerLoadError: string | null = null;

  // Action state
  isApplyingAction = false;
  actionSuccess: string | null = null;
  actionError: string | null = null;

  // Filter state
  filterStatus: string = '';
  filterKycStatus: string = '';
  filterJurisdiction: string = '';

  // Available filter options
  readonly kycStatuses: KycStatus[] = ['approved', 'pending', 'rejected', 'not_started'];
  readonly lockTypes: AccountLockType[] = [
    'SELF_EXCLUDE',
    'OPERATOR_SE',
    'TEMPORARY',
    'AWAITING_KYC',
    'BANNED_FRAUD',
    'DORMANT_USER',
  ];
  readonly rgLimitTypes: RgLimitType[] = [
    'deposit_daily',
    'deposit_weekly',
    'deposit_monthly',
    'loss_daily',
    'loss_weekly',
    'loss_monthly',
    'session_time',
    'wager_daily',
  ];

  readonly operationalStates = OPERATIONAL_STATES;

  private searchTerms$ = new Subject<string>();
  private destroy$ = new Subject<void>();

  constructor(private readonly playerService: PlayerManagementService) {}

  ngOnInit(): void {
    // Reactive search: debounce 300ms, skip duplicate terms, cancel in-flight requests
    this.searchTerms$.pipe(
      debounceTime(300),
      distinctUntilChanged(),
      switchMap((term) => {
        if (!term || term.length < 3) {
          this.searchResults = [];
          this.totalResults = 0;
          return of(null);
        }
        this.isSearching = true;
        this.searchError = null;
        const params: PlayerSearchParams = {
          query: term,
          page: this.currentPage,
          pageSize: this.pageSize,
          status: this.filterStatus || undefined,
          kycStatus: this.filterKycStatus as KycStatus || undefined,
          jurisdiction: this.filterJurisdiction || undefined,
        };
        return this.playerService.searchPlayers(params).pipe(
          catchError((err) => {
            this.searchError = `Search failed: ${err?.message ?? 'Unknown error'}`;
            return of(null);
          }),
          finalize(() => { this.isSearching = false; })
        );
      }),
      takeUntil(this.destroy$)
    ).subscribe((result: PagedResult<PlayerAccount> | null) => {
      if (result) {
        this.searchResults = result.items;
        this.totalResults = result.total;
      }
    });
  }

  ngOnDestroy(): void {
    this.destroy$.next();
    this.destroy$.complete();
  }

  // ---------------------------------------------------------------------------
  // Search
  // ---------------------------------------------------------------------------

  onSearchInput(term: string): void {
    this.currentPage = 1;
    this.searchTerms$.next(term);
  }

  onPageChange(page: number): void {
    this.currentPage = page;
    this.searchTerms$.next(this.searchQuery);
  }

  onFilterChange(): void {
    this.currentPage = 1;
    this.searchTerms$.next(this.searchQuery);
  }

  // ---------------------------------------------------------------------------
  // Player detail
  // ---------------------------------------------------------------------------

  selectPlayer(player: PlayerAccount): void {
    this.selectedPlayer = null;
    this.playerLoadError = null;
    this.actionSuccess = null;
    this.actionError = null;
    this.isLoadingPlayer = true;

    this.playerService.getPlayerDetail(player.id).pipe(
      catchError((err) => {
        this.playerLoadError = `Failed to load player: ${err?.message ?? 'Unknown error'}`;
        return of(null);
      }),
      finalize(() => { this.isLoadingPlayer = false; }),
      takeUntil(this.destroy$)
    ).subscribe((detail: PlayerAccount | null) => {
      if (detail) {
        this.selectedPlayer = detail;
      }
    });
  }

  clearSelectedPlayer(): void {
    this.selectedPlayer = null;
    this.playerLoadError = null;
    this.actionSuccess = null;
    this.actionError = null;
  }

  // ---------------------------------------------------------------------------
  // Account actions
  // ---------------------------------------------------------------------------

  /**
   * Apply an account lock. Requires a reason string for audit trail.
   * Compliance requirement: every manual lock must be journalled.
   */
  applyAccountLock(lockType: AccountLockType, reason: string, expiryDate?: Date): void {
    if (!this.selectedPlayer) return;
    this.isApplyingAction = true;
    this.actionSuccess = null;
    this.actionError = null;

    this.playerService.applyAccountLock(this.selectedPlayer.id, {
      lockType,
      reason,
      appliedBy: 'backoffice_operator', // replaced at service level with actual user
      expiryDate,
    }).pipe(
      catchError((err) => {
        this.actionError = `Lock failed: ${err?.message ?? 'Unknown error'}`;
        return of(null);
      }),
      finalize(() => { this.isApplyingAction = false; }),
      takeUntil(this.destroy$)
    ).subscribe((updated: PlayerAccount | null) => {
      if (updated) {
        this.selectedPlayer = updated;
        this.actionSuccess = `Account lock applied: ${lockType}`;
      }
    });
  }

  removeAccountLock(lockId: string, reason: string): void {
    if (!this.selectedPlayer) return;
    this.isApplyingAction = true;
    this.actionSuccess = null;
    this.actionError = null;

    this.playerService.removeAccountLock(this.selectedPlayer.id, lockId, reason).pipe(
      catchError((err) => {
        this.actionError = `Unlock failed: ${err?.message ?? 'Unknown error'}`;
        return of(null);
      }),
      finalize(() => { this.isApplyingAction = false; }),
      takeUntil(this.destroy$)
    ).subscribe((updated: PlayerAccount | null) => {
      if (updated) {
        this.selectedPlayer = updated;
        this.actionSuccess = `Account lock ${lockId} removed`;
      }
    });
  }

  /**
   * Apply a responsible gaming limit.
   * Note: UK LCCP requires limits to take effect immediately; increases have a cooling-off period.
   */
  applyRgLimit(limitType: RgLimitType, value: number, currency?: string): void {
    if (!this.selectedPlayer) return;
    this.isApplyingAction = true;
    this.actionSuccess = null;
    this.actionError = null;

    this.playerService.applyRgLimit(this.selectedPlayer.id, {
      limitType,
      value,
      currency,
      appliedBy: 'backoffice_operator',
    }).pipe(
      catchError((err) => {
        this.actionError = `RG limit failed: ${err?.message ?? 'Unknown error'}`;
        return of(null);
      }),
      finalize(() => { this.isApplyingAction = false; }),
      takeUntil(this.destroy$)
    ).subscribe((updated: PlayerAccount | null) => {
      if (updated) {
        this.selectedPlayer = updated;
        this.actionSuccess = `RG limit applied: ${limitType} = ${value}`;
      }
    });
  }

  /**
   * Trigger a manual KYC review.
   * Creates an audit trail entry and notifies the compliance team.
   */
  triggerKycReview(reviewType: 'standard' | 'enhanced', notes: string): void {
    if (!this.selectedPlayer) return;
    this.isApplyingAction = true;
    this.actionSuccess = null;
    this.actionError = null;

    this.playerService.triggerKycReview(this.selectedPlayer.id, { reviewType, notes }).pipe(
      catchError((err) => {
        this.actionError = `KYC review trigger failed: ${err?.message ?? 'Unknown error'}`;
        return of(null);
      }),
      finalize(() => { this.isApplyingAction = false; }),
      takeUntil(this.destroy$)
    ).subscribe((updated: PlayerAccount | null) => {
      if (updated) {
        this.selectedPlayer = updated;
        this.actionSuccess = `KYC review triggered: ${reviewType}`;
      }
    });
  }

  // ---------------------------------------------------------------------------
  // Display helpers
  // ---------------------------------------------------------------------------

  getOperationalStateLabel(state: PlayerOperationalState): string {
    return OPERATIONAL_STATES[state] ?? state;
  }

  getStateBadgeClass(state: PlayerOperationalState): string {
    const map: Partial<Record<PlayerOperationalState, string>> = {
      'kyc-approved': 'badge-success',
      'launch-eligible': 'badge-success',
      'wallet-eligible': 'badge-success',
      'payment-ready': 'badge-success',
      'authenticated': 'badge-info',
      'rg-limited': 'badge-warning',
      'support-escalated': 'badge-danger',
    };
    return map[state] ?? 'badge-secondary';
  }

  getKycBadgeClass(status: KycStatus): string {
    const map: Record<KycStatus, string> = {
      approved: 'badge-success',
      pending: 'badge-warning',
      rejected: 'badge-danger',
      not_started: 'badge-secondary',
    };
    return map[status] ?? 'badge-secondary';
  }

  formatCurrency(amount: number, currency: string): string {
    return new Intl.NumberFormat('en-GB', {
      style: 'currency',
      currency: currency || 'EUR',
    }).format(amount / 100); // amounts stored in cents
  }

  trackById(_index: number, item: PlayerAccount): string {
    return item.id;
  }
}
