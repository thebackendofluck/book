// Companion code for "The Backend of Luck" - Chapter 10, Complete Platform Architecture.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * rules.ts
 * --------
 * 19 alert rules derived from the risk-alerting service.
 *
 * Each rule is a pure function: (PlayerContext) => AlertResult | null
 * A null return means the rule did not fire.
 *
 * Rules are grouped by category:
 *   - Velocity (deposit/withdrawal frequency)
 *   - Amount (single transaction thresholds)
 *   - Behaviour (session, game, time patterns)
 *   - AML (structuring, layering signals)
 *   - Responsible gambling (loss limits, chasing)
 */

export type RiskLevel = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";

export interface PlayerContext {
  playerId: string;
  sessionDurationMinutes: number;
  depositCount24h: number;
  depositAmountGBP24h: number;
  withdrawalCount24h: number;
  withdrawalAmountGBP24h: number;
  depositCount7d: number;
  depositAmountGBP7d: number;
  singleDepositAmountGBP: number;
  singleWithdrawalAmountGBP: number;
  netLoss24h: number;
  netLoss7d: number;
  netLoss30d: number;
  hourOfDay: number;                 // 0-23 UTC
  isFirstDeposit: boolean;
  daysAccountAge: number;
  uniqueGamesPlayed24h: number;
  rapidBetChangeDetected: boolean;   // stake escalation in <5 min
  countryCode: string;
  isSelfExcluded: boolean;
  pendingKycDocuments: boolean;
  loginCount24h: number;
  failedLoginCount24h: number;
  kycStatus: "NONE" | "PENDING" | "APPROVED" | "REJECTED";
}

export interface AlertResult {
  ruleId: string;
  ruleName: string;
  riskLevel: RiskLevel;
  description: string;
  suggestedAction: "MONITOR" | "REVIEW" | "BLOCK" | "ESCALATE";
}

type Rule = (ctx: PlayerContext) => AlertResult | null;

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------

function alert(
  ruleId: string,
  ruleName: string,
  riskLevel: RiskLevel,
  description: string,
  suggestedAction: AlertResult["suggestedAction"],
): AlertResult {
  return { ruleId, ruleName, riskLevel, description, suggestedAction };
}

// ---------------------------------------------------------------------------
// Velocity rules
// ---------------------------------------------------------------------------

const R01_DepositVelocity24h: Rule = (ctx) =>
  ctx.depositCount24h >= 10
    ? alert("R01", "Deposit velocity 24h", "HIGH",
        `${ctx.depositCount24h} deposits in 24h`, "REVIEW")
    : null;

const R02_DepositAmountThreshold24h: Rule = (ctx) =>
  ctx.depositAmountGBP24h >= 5000
    ? alert("R02", "High deposit amount 24h", "HIGH",
        `£${ctx.depositAmountGBP24h.toFixed(2)} deposited in 24h`, "REVIEW")
    : null;

const R03_WithdrawalVelocity24h: Rule = (ctx) =>
  ctx.withdrawalCount24h >= 5
    ? alert("R03", "Withdrawal velocity 24h", "MEDIUM",
        `${ctx.withdrawalCount24h} withdrawals in 24h`, "REVIEW")
    : null;

const R04_LargeWithdrawal: Rule = (ctx) =>
  ctx.singleWithdrawalAmountGBP >= 10000
    ? alert("R04", "Large single withdrawal", "HIGH",
        `Single withdrawal of £${ctx.singleWithdrawalAmountGBP.toFixed(2)}`, "REVIEW")
    : null;

// ---------------------------------------------------------------------------
// Amount rules
// ---------------------------------------------------------------------------

const R05_LargeDeposit: Rule = (ctx) =>
  ctx.singleDepositAmountGBP >= 2000
    ? alert("R05", "Large single deposit", "MEDIUM",
        `Single deposit of £${ctx.singleDepositAmountGBP.toFixed(2)}`, "MONITOR")
    : null;

const R06_CriticalDeposit: Rule = (ctx) =>
  ctx.singleDepositAmountGBP >= 10000
    ? alert("R06", "Critical single deposit", "CRITICAL",
        `Single deposit of £${ctx.singleDepositAmountGBP.toFixed(2)} requires SAR`, "ESCALATE")
    : null;

const R07_WeeklyDepositVolume: Rule = (ctx) =>
  ctx.depositAmountGBP7d >= 20000
    ? alert("R07", "High weekly deposit volume", "HIGH",
        `£${ctx.depositAmountGBP7d.toFixed(2)} deposited in 7 days`, "ESCALATE")
    : null;

// ---------------------------------------------------------------------------
// Behaviour rules
// ---------------------------------------------------------------------------

const R08_LongSession: Rule = (ctx) =>
  ctx.sessionDurationMinutes >= 240
    ? alert("R08", "Long session detected", "MEDIUM",
        `Session duration ${ctx.sessionDurationMinutes} minutes`, "MONITOR")
    : null;

const R09_LateNightActivity: Rule = (ctx) =>
  ctx.hourOfDay >= 1 && ctx.hourOfDay <= 5
    ? alert("R09", "Late-night activity", "LOW",
        `Activity detected at ${ctx.hourOfDay}:00 UTC`, "MONITOR")
    : null;

const R10_RapidStakeEscalation: Rule = (ctx) =>
  ctx.rapidBetChangeDetected
    ? alert("R10", "Rapid stake escalation", "HIGH",
        "Bet size increased >10x within 5 minutes", "REVIEW")
    : null;

const R11_HighGameVariety: Rule = (ctx) =>
  ctx.uniqueGamesPlayed24h >= 20
    ? alert("R11", "High game variety 24h", "LOW",
        `${ctx.uniqueGamesPlayed24h} unique games played in 24h`, "MONITOR")
    : null;

const R12_NewAccountHighDeposit: Rule = (ctx) =>
  ctx.daysAccountAge <= 7 && ctx.depositAmountGBP24h >= 1000
    ? alert("R12", "New account high deposit", "HIGH",
        `Account aged ${ctx.daysAccountAge}d deposited £${ctx.depositAmountGBP24h.toFixed(2)}`, "REVIEW")
    : null;

// ---------------------------------------------------------------------------
// Responsible gambling rules
// ---------------------------------------------------------------------------

const R13_NetLoss24h: Rule = (ctx) =>
  ctx.netLoss24h >= 1000
    ? alert("R13", "High net loss 24h", "MEDIUM",
        `Net loss of £${ctx.netLoss24h.toFixed(2)} in 24h`, "REVIEW")
    : null;

const R14_NetLoss7d: Rule = (ctx) =>
  ctx.netLoss7d >= 5000
    ? alert("R14", "High net loss 7 days", "HIGH",
        `Net loss of £${ctx.netLoss7d.toFixed(2)} in 7 days`, "ESCALATE")
    : null;

const R15_NetLoss30d: Rule = (ctx) =>
  ctx.netLoss30d >= 15000
    ? alert("R15", "High net loss 30 days", "CRITICAL",
        `Net loss of £${ctx.netLoss30d.toFixed(2)} in 30 days — mandatory interaction`, "ESCALATE")
    : null;

// ---------------------------------------------------------------------------
// AML rules
// ---------------------------------------------------------------------------

const R16_Structuring: Rule = (ctx) =>
  ctx.depositCount24h >= 5 &&
  ctx.singleDepositAmountGBP <= 1000 &&
  ctx.depositAmountGBP24h >= 4000
    ? alert("R16", "Possible structuring", "CRITICAL",
        `${ctx.depositCount24h} deposits averaging £${(ctx.depositAmountGBP24h / ctx.depositCount24h).toFixed(2)} — structuring signal`, "ESCALATE")
    : null;

const R17_KycDepositMismatch: Rule = (ctx) =>
  ctx.kycStatus !== "APPROVED" && ctx.depositAmountGBP24h >= 500
    ? alert("R17", "KYC not approved — high deposits", "HIGH",
        `Deposited £${ctx.depositAmountGBP24h.toFixed(2)} with KYC status: ${ctx.kycStatus}`, "BLOCK")
    : null;

// ---------------------------------------------------------------------------
// Account security rules
// ---------------------------------------------------------------------------

const R18_FailedLoginSpike: Rule = (ctx) =>
  ctx.failedLoginCount24h >= 10
    ? alert("R18", "Failed login spike", "HIGH",
        `${ctx.failedLoginCount24h} failed login attempts in 24h`, "BLOCK")
    : null;

const R19_SelfExclusionDepositAttempt: Rule = (ctx) =>
  ctx.isSelfExcluded
    ? alert("R19", "Self-excluded player activity", "CRITICAL",
        "Player is self-excluded — all activity must be blocked", "BLOCK")
    : null;

// ---------------------------------------------------------------------------
// Exported rule set
// ---------------------------------------------------------------------------

export const ALL_RULES: Rule[] = [
  R01_DepositVelocity24h,
  R02_DepositAmountThreshold24h,
  R03_WithdrawalVelocity24h,
  R04_LargeWithdrawal,
  R05_LargeDeposit,
  R06_CriticalDeposit,
  R07_WeeklyDepositVolume,
  R08_LongSession,
  R09_LateNightActivity,
  R10_RapidStakeEscalation,
  R11_HighGameVariety,
  R12_NewAccountHighDeposit,
  R13_NetLoss24h,
  R14_NetLoss7d,
  R15_NetLoss30d,
  R16_Structuring,
  R17_KycDepositMismatch,
  R18_FailedLoginSpike,
  R19_SelfExclusionDepositAttempt,
];

/**
 * Evaluate all rules against a player context.
 * Returns all fired alerts sorted by severity.
 */
export function evaluateRules(ctx: PlayerContext): AlertResult[] {
  const severityOrder: Record<RiskLevel, number> = {
    CRITICAL: 4,
    HIGH: 3,
    MEDIUM: 2,
    LOW: 1,
  };

  return ALL_RULES.flatMap((rule) => {
    const result = rule(ctx);
    return result ? [result] : [];
  }).sort(
    (a, b) => severityOrder[b.riskLevel] - severityOrder[a.riskLevel],
  );
}

/**
 * Compute overall risk score (0–100) from fired alerts.
 */
export function computeRiskScore(alerts: AlertResult[]): number {
  const weights: Record<RiskLevel, number> = {
    CRITICAL: 40,
    HIGH: 20,
    MEDIUM: 10,
    LOW: 5,
  };

  const raw = alerts.reduce((sum, a) => sum + weights[a.riskLevel], 0);
  return Math.min(100, raw);
}
