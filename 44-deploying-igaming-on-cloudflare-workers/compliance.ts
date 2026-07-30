// Companion code for "The Backend of Luck" - Chapter 44, Deploying iGaming Platforms on Cloudflare Workers.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * AcmeToCasino Platform - Regulatory Compliance
 * AML checks, responsible gambling tools, jurisdiction controls, sanctions screening
 */

import {
  Env,
  successResponse,
  errorResponse,
  internalErrorResponse,
  parseJSON,
  getClientIP,
  getCountry,
} from './utils.js';
import { authenticateRequest, UserRow } from './auth.js';

// ─── Types ─────────────────────────────────────────────────────────────────

// Countries where online gambling is restricted/prohibited
const BLOCKED_JURISDICTIONS = new Set([
  'US', 'CU', 'IR', 'KP', 'SY', 'MM', 'AF',
]);

// Countries requiring enhanced due diligence
const HIGH_RISK_JURISDICTIONS = new Set([
  'RU', 'CN', 'UA', 'BY', 'VN', 'ID',
]);

interface ResponsibleGamblingSettings {
  daily_deposit_limit: number | null;
  weekly_deposit_limit: number | null;
  monthly_deposit_limit: number | null;
  session_reminder_minutes: number | null;
  self_exclusion_until: string | null;
  reality_check_minutes: number | null;
  cool_off_until: string | null;
}

interface SetLimitsBody {
  dailyDepositLimit?: number | null;
  weeklyDepositLimit?: number | null;
  monthlyDepositLimit?: number | null;
  sessionReminderMinutes?: number | null;
  realityCheckMinutes?: number | null;
}

interface SelfExclusionBody {
  durationDays: number;  // 1-3650 (up to 10 years)
  reason?: string;
}

// ─── Route handler ─────────────────────────────────────────────────────────

export async function handleCompliance(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const { method } = request;

  // Jurisdiction check is public — run before auth
  if (method === 'GET' && url.pathname === '/api/compliance/jurisdiction') {
    return handleJurisdictionCheck(request, env);
  }

  const user = await authenticateRequest(request, env);
  if (!user) return errorResponse('Unauthorized', 401);

  if (method === 'GET' && url.pathname === '/api/compliance/rg-settings') {
    return handleGetRGSettings(user, env);
  }
  if (method === 'PUT' && url.pathname === '/api/compliance/rg-settings') {
    return handleSetRGSettings(request, env, user);
  }
  if (method === 'POST' && url.pathname === '/api/compliance/self-exclude') {
    return handleSelfExclusion(request, env, user);
  }
  if (method === 'POST' && url.pathname === '/api/compliance/cool-off') {
    return handleCoolOff(request, env, user);
  }
  if (method === 'GET' && url.pathname === '/api/compliance/deposit-check') {
    return handleDepositLimitCheck(request, env, user);
  }

  return errorResponse('Route not found', 404);
}

// ─── Jurisdiction check ────────────────────────────────────────────────────

async function handleJurisdictionCheck(request: Request, _env: Env): Promise<Response> {
  const country = getCountry(request);
  const ip = getClientIP(request);

  if (BLOCKED_JURISDICTIONS.has(country)) {
    return successResponse({
      allowed: false,
      country,
      reason: 'Online gambling is not permitted in your jurisdiction.',
    });
  }

  const highRisk = HIGH_RISK_JURISDICTIONS.has(country);

  return successResponse({
    allowed: true,
    country,
    requiresEnhancedKyc: highRisk,
    ip,
  });
}

// ─── Responsible Gambling Settings ────────────────────────────────────────

async function handleGetRGSettings(user: UserRow, env: Env): Promise<Response> {
  try {
    const settings = await env.DB.prepare(
      'SELECT * FROM responsible_gambling_settings WHERE user_id = ?'
    )
      .bind(user.id)
      .first<ResponsibleGamblingSettings & { user_id: number }>();

    if (!settings) {
      // Return defaults
      return successResponse({
        dailyDepositLimit: null,
        weeklyDepositLimit: null,
        monthlyDepositLimit: null,
        sessionReminderMinutes: null,
        realityCheckMinutes: null,
        selfExclusionUntil: null,
        coolOffUntil: null,
        isExcluded: false,
      });
    }

    const now = new Date();
    const isExcluded =
      settings.self_exclusion_until != null &&
      new Date(settings.self_exclusion_until) > now;

    const isCoolOff =
      settings.cool_off_until != null &&
      new Date(settings.cool_off_until) > now;

    return successResponse({
      dailyDepositLimit: settings.daily_deposit_limit,
      weeklyDepositLimit: settings.weekly_deposit_limit,
      monthlyDepositLimit: settings.monthly_deposit_limit,
      sessionReminderMinutes: settings.session_reminder_minutes,
      realityCheckMinutes: settings.reality_check_minutes,
      selfExclusionUntil: settings.self_exclusion_until,
      coolOffUntil: settings.cool_off_until,
      isExcluded,
      isCoolOff,
    });
  } catch (err) {
    console.error('Get RG settings error:', err);
    return internalErrorResponse(env, err instanceof Error ? err.message : undefined);
  }
}

async function handleSetRGSettings(
  request: Request,
  env: Env,
  user: UserRow
): Promise<Response> {
  const body = await parseJSON<SetLimitsBody>(request);
  if (!body) return errorResponse('Invalid JSON body');

  const errors = validateLimits(body);
  if (errors.length > 0) return errorResponse(errors.join(', '), 422);

  try {
    await env.DB.prepare(
      `INSERT INTO responsible_gambling_settings
         (user_id, daily_deposit_limit, weekly_deposit_limit, monthly_deposit_limit,
          session_reminder_minutes, reality_check_minutes)
       VALUES (?, ?, ?, ?, ?, ?)
       ON CONFLICT(user_id) DO UPDATE SET
         daily_deposit_limit = excluded.daily_deposit_limit,
         weekly_deposit_limit = excluded.weekly_deposit_limit,
         monthly_deposit_limit = excluded.monthly_deposit_limit,
         session_reminder_minutes = excluded.session_reminder_minutes,
         reality_check_minutes = excluded.reality_check_minutes,
         updated_at = CURRENT_TIMESTAMP`
    )
      .bind(
        user.id,
        body.dailyDepositLimit ?? null,
        body.weeklyDepositLimit ?? null,
        body.monthlyDepositLimit ?? null,
        body.sessionReminderMinutes ?? null,
        body.realityCheckMinutes ?? null
      )
      .run();

    // Log the limits change for audit
    await logComplianceEvent(user.id, 'rg_limits_updated', body, env);

    return successResponse({ message: 'Responsible gambling settings updated.' });
  } catch (err) {
    console.error('Set RG settings error:', err);
    return internalErrorResponse(env, err instanceof Error ? err.message : undefined);
  }
}

// ─── Self-exclusion ────────────────────────────────────────────────────────

async function handleSelfExclusion(
  request: Request,
  env: Env,
  user: UserRow
): Promise<Response> {
  const body = await parseJSON<SelfExclusionBody>(request);
  if (!body) return errorResponse('Invalid JSON body');

  if (!body.durationDays || body.durationDays < 1 || body.durationDays > 3650) {
    return errorResponse('durationDays must be between 1 and 3650');
  }

  const exclusionUntil = new Date(
    Date.now() + body.durationDays * 24 * 60 * 60 * 1000
  ).toISOString();

  try {
    // Update RG settings
    await env.DB.prepare(
      `INSERT INTO responsible_gambling_settings (user_id, self_exclusion_until)
       VALUES (?, ?)
       ON CONFLICT(user_id) DO UPDATE SET
         self_exclusion_until = excluded.self_exclusion_until,
         updated_at = CURRENT_TIMESTAMP`
    )
      .bind(user.id, exclusionUntil)
      .run();

    // Update user status
    await env.DB.prepare(
      "UPDATE users SET status = 'self_excluded', updated_at = CURRENT_TIMESTAMP WHERE id = ?"
    )
      .bind(user.id)
      .run();

    // Revoke all active sessions
    await env.SESSIONS.delete(`session:${user.id}`);
    await env.CACHE.delete(`user:${user.id}`);

    await logComplianceEvent(user.id, 'self_exclusion', {
      durationDays: body.durationDays,
      until: exclusionUntil,
      reason: body.reason,
    }, env);

    return successResponse({
      message: `Self-exclusion applied until ${exclusionUntil}. You will not be able to access the platform during this period.`,
      exclusionUntil,
    });
  } catch (err) {
    console.error('Self-exclusion error:', err);
    return internalErrorResponse(env, err instanceof Error ? err.message : undefined);
  }
}

// ─── Cool-off ──────────────────────────────────────────────────────────────

async function handleCoolOff(
  request: Request,
  env: Env,
  user: UserRow
): Promise<Response> {
  const body = await parseJSON<{ hours: number }>(request);
  if (!body) return errorResponse('Invalid JSON body');

  if (!body.hours || body.hours < 1 || body.hours > 168) {
    return errorResponse('hours must be between 1 and 168 (1 week)');
  }

  const coolOffUntil = new Date(Date.now() + body.hours * 3600 * 1000).toISOString();

  try {
    await env.DB.prepare(
      `INSERT INTO responsible_gambling_settings (user_id, cool_off_until)
       VALUES (?, ?)
       ON CONFLICT(user_id) DO UPDATE SET
         cool_off_until = excluded.cool_off_until,
         updated_at = CURRENT_TIMESTAMP`
    )
      .bind(user.id, coolOffUntil)
      .run();

    await env.SESSIONS.delete(`session:${user.id}`);
    await env.CACHE.delete(`user:${user.id}`);

    await logComplianceEvent(user.id, 'cool_off', { hours: body.hours, until: coolOffUntil }, env);

    return successResponse({
      message: `Cool-off period applied until ${coolOffUntil}.`,
      coolOffUntil,
    });
  } catch (err) {
    console.error('Cool-off error:', err);
    return internalErrorResponse(env, err instanceof Error ? err.message : undefined);
  }
}

// ─── Deposit limit check ───────────────────────────────────────────────────

async function handleDepositLimitCheck(
  request: Request,
  env: Env,
  user: UserRow
): Promise<Response> {
  try {
    const url = new URL(request.url);
    const amount = parseFloat(url.searchParams.get('amount') ?? '0');

    if (isNaN(amount) || amount <= 0) {
      return errorResponse('amount query parameter is required');
    }

    const settings = await env.DB.prepare(
      'SELECT * FROM responsible_gambling_settings WHERE user_id = ?'
    )
      .bind(user.id)
      .first<ResponsibleGamblingSettings>();

    if (!settings) return successResponse({ allowed: true });

    // Check self-exclusion / cool-off
    const now = new Date();
    if (settings.self_exclusion_until && new Date(settings.self_exclusion_until) > now) {
      return successResponse({ allowed: false, reason: 'self_excluded' });
    }
    if (settings.cool_off_until && new Date(settings.cool_off_until) > now) {
      return successResponse({ allowed: false, reason: 'cool_off_active' });
    }

    // Check deposit limits
    if (settings.daily_deposit_limit) {
      const dailyTotal = await getDepositTotal(user.id, 1, env);
      if (dailyTotal + amount > settings.daily_deposit_limit) {
        return successResponse({
          allowed: false,
          reason: 'daily_limit_exceeded',
          limit: settings.daily_deposit_limit,
          used: dailyTotal,
        });
      }
    }

    if (settings.weekly_deposit_limit) {
      const weeklyTotal = await getDepositTotal(user.id, 7, env);
      if (weeklyTotal + amount > settings.weekly_deposit_limit) {
        return successResponse({
          allowed: false,
          reason: 'weekly_limit_exceeded',
          limit: settings.weekly_deposit_limit,
          used: weeklyTotal,
        });
      }
    }

    if (settings.monthly_deposit_limit) {
      const monthlyTotal = await getDepositTotal(user.id, 30, env);
      if (monthlyTotal + amount > settings.monthly_deposit_limit) {
        return successResponse({
          allowed: false,
          reason: 'monthly_limit_exceeded',
          limit: settings.monthly_deposit_limit,
          used: monthlyTotal,
        });
      }
    }

    return successResponse({ allowed: true });
  } catch (err) {
    console.error('Deposit limit check error:', err);
    return internalErrorResponse(env, err instanceof Error ? err.message : undefined);
  }
}

// ─── Exported compliance check for use by other modules ───────────────────

export async function checkUserCompliance(
  user: UserRow,
  env: Env
): Promise<{ allowed: boolean; reason?: string }> {
  if (user.status === 'self_excluded') return { allowed: false, reason: 'self_excluded' };
  if (user.status === 'suspended') return { allowed: false, reason: 'account_suspended' };

  const settings = await env.DB.prepare(
    'SELECT self_exclusion_until, cool_off_until FROM responsible_gambling_settings WHERE user_id = ?'
  )
    .bind(user.id)
    .first<Pick<ResponsibleGamblingSettings, 'self_exclusion_until' | 'cool_off_until'>>();

  if (!settings) return { allowed: true };

  const now = new Date();
  if (settings.self_exclusion_until && new Date(settings.self_exclusion_until) > now) {
    return { allowed: false, reason: 'self_excluded' };
  }
  if (settings.cool_off_until && new Date(settings.cool_off_until) > now) {
    return { allowed: false, reason: 'cool_off_active' };
  }

  return { allowed: true };
}

export function isBlockedJurisdiction(country: string): boolean {
  return BLOCKED_JURISDICTIONS.has(country);
}

// ─── Internal helpers ──────────────────────────────────────────────────────

async function getDepositTotal(userId: number, days: number, env: Env): Promise<number> {
  const since = new Date(Date.now() - days * 24 * 3600 * 1000).toISOString();
  const result = await env.DB.prepare(
    `SELECT COALESCE(SUM(amount), 0) as total FROM transactions
     WHERE user_id = ? AND type = 'deposit' AND status = 'completed' AND created_at >= ?`
  )
    .bind(userId, since)
    .first<{ total: number }>();
  return result?.total ?? 0;
}

async function logComplianceEvent(
  userId: number,
  eventType: string,
  details: unknown,
  env: Env
): Promise<void> {
  await env.DB.prepare(
    'INSERT INTO compliance_events (user_id, event_type, details) VALUES (?, ?, ?)'
  )
    .bind(userId, eventType, JSON.stringify(details))
    .run();
}

function validateLimits(body: SetLimitsBody): string[] {
  const errors: string[] = [];
  const checkLimit = (val: number | null | undefined, name: string) => {
    if (val !== undefined && val !== null && (typeof val !== 'number' || val <= 0)) {
      errors.push(`${name} must be a positive number or null`);
    }
  };
  checkLimit(body.dailyDepositLimit, 'dailyDepositLimit');
  checkLimit(body.weeklyDepositLimit, 'weeklyDepositLimit');
  checkLimit(body.monthlyDepositLimit, 'monthlyDepositLimit');
  checkLimit(body.sessionReminderMinutes, 'sessionReminderMinutes');
  checkLimit(body.realityCheckMinutes, 'realityCheckMinutes');
  return errors;
}
