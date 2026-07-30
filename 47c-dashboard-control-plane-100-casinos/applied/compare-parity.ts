#!/usr/bin/env -S node --experimental-strip-types
// Companion code for "The Backend of Luck" - Chapter 47c, Operating 100 Casinos From One Dashboard.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: BUSL-1.1
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * compare-parity.ts
 *
 * Migration parity gate: legacy dashboard.html vs v3 API.
 *
 * - Reads the legacy HTML either from a local file or, when `--ssh` is passed,
 *   spawns `ssh <host> cat <remote-path>` (no shell, argv-based — safe from
 *   command injection).
 * - DOM-parses every element with `id="..."` and extracts its rendered value.
 * - Maps each id to a `(slug, jsonPath)` pair and queries
 *   `<base-url>/api/v3/<slug>` for each unique slug.
 * - Writes a JSON diff report to `tmp/parity-report-YYYY-MM-DD.json`.
 *
 * CI gate:
 *   exit 0  → match rate >= MATCH_THRESHOLD (default 95%)
 *   exit 1  → match rate <  MATCH_THRESHOLD
 *   exit 2  → I/O / parse error
 *
 * Usage:
 *   pnpm tsx scripts/compare-parity.ts \
 *     [--url http://localhost:3010] \
 *     [--ssh deploy@new.acmetocasino.com] \
 *     [--legacy-path /var/www/new.acmetocasino.com/dashboard.html] \
 *     [--threshold 95] \
 *     [--out tmp/parity-report-2026-04-14.json]
 */
import { spawn } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const DEFAULT_LEGACY_REMOTE = "/var/www/new.acmetocasino.com/dashboard.html";
const DEFAULT_LEGACY_LOCAL = resolve(
  process.cwd(),
  "../../website/dashboard/dashboard.html",
);
const DEFAULT_THRESHOLD_PCT = 95;
const NUMERIC_TOLERANCE_PCT = 1; // values within 1% are considered "match"

type FieldKind = "number" | "string";
interface FieldMapping {
  slug: string;
  path: string;
  kind: FieldKind;
}

/**
 * Map legacy DOM ids to a v3 endpoint slug + dotted path inside the JSON
 * payload. Extend as more tabs migrate.
 */
const ID_MAP: Record<string, FieldMapping> = {
  // overview / wallet
  balanceDisplay: { slug: "overview", path: "wallet.balance_eur", kind: "number" },
  depositDisplay: { slug: "overview", path: "wallet.deposits_eur", kind: "number" },
  withdrawDisplay: { slug: "overview", path: "wallet.withdrawals_eur", kind: "number" },
  pnlDisplay: { slug: "overview", path: "wallet.pnl_eur", kind: "number" },
  sessionsVal: { slug: "overview", path: "sessions_24h", kind: "number" },
  wageredVal: { slug: "overview", path: "bets_today", kind: "number" },
  wonVal: { slug: "overview", path: "wins_today", kind: "number" },
  winrateVal: { slug: "overview", path: "winrate_pct", kind: "number" },

  // legacy ov* aliases
  ovPlayers: { slug: "overview", path: "players_online", kind: "number" },
  ovDeposits: { slug: "overview", path: "deposits_today_eur", kind: "number" },
  ovBets: { slug: "overview", path: "bets_today", kind: "number" },
  ovWins: { slug: "overview", path: "wins_today", kind: "number" },
  ovSessions: { slug: "overview", path: "sessions_24h", kind: "number" },
  ovRounds: { slug: "overview", path: "rounds_24h", kind: "number" },
  ovUptime: { slug: "overview", path: "uptime_pct", kind: "number" },
  ovDbLatency: { slug: "overview", path: "db_latency_ms", kind: "number" },
  ovRedisLatency: { slug: "overview", path: "redis_latency_ms", kind: "number" },
  ovRps: { slug: "overview", path: "req_per_sec", kind: "number" },
  ovP99: { slug: "overview", path: "p99_ms", kind: "number" },
  ovErrorRate: { slug: "overview", path: "error_rate_pct", kind: "number" },

  // payments
  payVolume24h: { slug: "payments", path: "volume_24h_eur", kind: "number" },
  payApprovalRate: { slug: "payments", path: "approval_rate_pct", kind: "number" },
  payChargebackRate: { slug: "payments", path: "chargeback_rate_pct", kind: "number" },

  // finops
  "lf-grossRevenue": { slug: "finops", path: "gross_revenue_eur", kind: "number" },
  "lf-netRevenue": { slug: "finops", path: "net_revenue_eur", kind: "number" },
  "lf-ggr": { slug: "finops", path: "ggr_eur", kind: "number" },
  "lf-ngr": { slug: "finops", path: "ngr_eur", kind: "number" },

  // fraud
  "fr-blockedTx24h": { slug: "fraud", path: "blocked_tx_24h", kind: "number" },
  "fr-riskScore": { slug: "fraud", path: "risk_score", kind: "number" },

  // compliance
  "comp-kycPending": { slug: "compliance", path: "kyc_pending", kind: "number" },
  "comp-amlAlerts": { slug: "compliance", path: "aml_alerts_open", kind: "number" },

  // infrastructure
  "inf-uptimePct": { slug: "infrastructure", path: "uptime_pct", kind: "number" },
  "inf-podCount": { slug: "infrastructure", path: "pod_count", kind: "number" },
};

// ---------------------------------------------------------------------------
// CLI arg parsing
// ---------------------------------------------------------------------------

function arg(name: string): string | undefined {
  const i = process.argv.indexOf(name);
  return i >= 0 ? process.argv[i + 1] : undefined;
}

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

// ---------------------------------------------------------------------------
// HTML extraction
// ---------------------------------------------------------------------------

const ID_VALUE_RE =
  /<([a-zA-Z][a-zA-Z0-9]*)\b[^>]*\bid=["']([^"']+)["'][^>]*>([\s\S]*?)<\/\1>/g;
const TAG_STRIP = /<[^>]+>/g;
const ENTITY_MAP: Record<string, string> = {
  "&amp;": "&",
  "&lt;": "<",
  "&gt;": ">",
  "&quot;": '"',
  "&#39;": "'",
  "&#8364;": "€",
  "&euro;": "€",
  "&nbsp;": " ",
};

function decodeEntities(s: string): string {
  return s
    .replace(/&#(\d+);/g, (_, code) => String.fromCharCode(Number(code)))
    .replace(/&[a-z]+;|&#\d+;/gi, (m) => ENTITY_MAP[m] ?? m);
}

function cleanText(inner: string): string {
  return decodeEntities(inner.replace(TAG_STRIP, "").replace(/\s+/g, " ")).trim();
}

function parseNumber(raw: string): number | null {
  if (!raw) return null;
  const cleaned = raw.replace(/[^0-9.\-]/g, "");
  if (!cleaned || cleaned === "-" || cleaned === ".") return null;
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : null;
}

interface LegacyValue {
  rawText: string;
  number: number | null;
}

function extractLegacyIds(html: string): Map<string, LegacyValue> {
  const out = new Map<string, LegacyValue>();
  ID_VALUE_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = ID_VALUE_RE.exec(html)) !== null) {
    const id = m[2];
    const text = cleanText(m[3]);
    out.set(id, { rawText: text, number: parseNumber(text) });
  }
  return out;
}

// ---------------------------------------------------------------------------
// Legacy HTML loaders
// ---------------------------------------------------------------------------

function spawnCapture(cmd: string, args: string[]): Promise<string> {
  return new Promise((resolveP, rejectP) => {
    // shell:false — argv is passed directly to execve, no injection surface.
    const child = spawn(cmd, args, { shell: false });
    const chunks: Buffer[] = [];
    const errs: Buffer[] = [];
    child.stdout.on("data", (c: Buffer) => chunks.push(c));
    child.stderr.on("data", (c: Buffer) => errs.push(c));
    child.on("error", rejectP);
    child.on("close", (code) => {
      if (code === 0) resolveP(Buffer.concat(chunks).toString("utf8"));
      else rejectP(new Error(`${cmd} exited ${code}: ${Buffer.concat(errs).toString("utf8")}`));
    });
  });
}

async function loadLegacyHtml(opts: {
  ssh?: string;
  legacyPath?: string;
}): Promise<{ html: string; source: string }> {
  if (opts.ssh) {
    const remotePath = opts.legacyPath ?? DEFAULT_LEGACY_REMOTE;
    const html = await spawnCapture("ssh", [opts.ssh, "cat", remotePath]);
    return { html, source: `ssh://${opts.ssh}${remotePath}` };
  }
  const localPath = opts.legacyPath ?? DEFAULT_LEGACY_LOCAL;
  const html = await readFile(localPath, "utf8");
  return { html, source: localPath };
}

// ---------------------------------------------------------------------------
// V3 fetch
// ---------------------------------------------------------------------------

function getByPath(obj: unknown, path: string): unknown {
  return path.split(".").reduce<unknown>((acc, key) => {
    if (acc && typeof acc === "object" && key in (acc as Record<string, unknown>)) {
      return (acc as Record<string, unknown>)[key];
    }
    return undefined;
  }, obj);
}

async function fetchSlug(baseUrl: string, slug: string): Promise<unknown> {
  const url = `${baseUrl.replace(/\/$/, "")}/api/v3/${slug}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`GET ${url} → HTTP ${res.status}`);
  return res.json();
}

// ---------------------------------------------------------------------------
// Diff
// ---------------------------------------------------------------------------

type DiffStatus =
  | "match"
  | "diverge"
  | "missing_legacy"
  | "missing_v3"
  | "endpoint_error";

interface Diff {
  id: string;
  slug: string;
  path: string;
  kind: FieldKind;
  legacy: number | string | null;
  v3: number | string | null;
  driftPct: number | null;
  status: DiffStatus;
  note?: string;
}

function compare(map: FieldMapping, legacy: LegacyValue | undefined, v3Val: unknown): Diff {
  const base: Diff = {
    id: "",
    slug: map.slug,
    path: map.path,
    kind: map.kind,
    legacy: null,
    v3: null,
    driftPct: null,
    status: "missing_legacy",
  };

  if (!legacy) return base;

  if (map.kind === "number") {
    base.legacy = legacy.number;
    if (v3Val === undefined) {
      base.status = "missing_v3";
      return base;
    }
    if (typeof v3Val !== "number") {
      base.v3 = v3Val == null ? null : String(v3Val);
      base.status = "diverge";
      return base;
    }
    base.v3 = v3Val;
    if (legacy.number === null) {
      base.status = "diverge";
      base.note = `legacy text "${legacy.rawText}" not numeric`;
      return base;
    }
    const drift =
      legacy.number === 0
        ? v3Val === 0
          ? 0
          : 100
        : Math.abs((v3Val - legacy.number) / legacy.number) * 100;
    base.driftPct = Number(drift.toFixed(3));
    base.status = drift <= NUMERIC_TOLERANCE_PCT ? "match" : "diverge";
    return base;
  }

  // string compare
  base.legacy = legacy.rawText;
  if (v3Val === undefined) {
    base.status = "missing_v3";
    return base;
  }
  base.v3 = v3Val == null ? null : String(v3Val);
  base.status = String(v3Val).trim() === legacy.rawText ? "match" : "diverge";
  return base;
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main(): Promise<number> {
  const baseUrl = arg("--url") ?? "http://localhost:3010";
  const ssh = arg("--ssh");
  const legacyPath = arg("--legacy-path");
  const threshold = Number(arg("--threshold") ?? DEFAULT_THRESHOLD_PCT);
  const outPath =
    arg("--out") ??
    resolve(process.cwd(), `tmp/parity-report-${todayIso()}.json`);

  let legacyHtml: string;
  let legacySource: string;
  try {
    const loaded = await loadLegacyHtml({ ssh, legacyPath });
    legacyHtml = loaded.html;
    legacySource = loaded.source;
  } catch (err) {
    console.error("[parity] failed to read legacy HTML:", err);
    return 2;
  }

  const legacyMap = extractLegacyIds(legacyHtml);

  const slugs = Array.from(new Set(Object.values(ID_MAP).map((m) => m.slug)));
  const slugData = new Map<string, unknown>();
  const slugErrors = new Map<string, string>();
  await Promise.all(
    slugs.map(async (slug) => {
      try {
        slugData.set(slug, await fetchSlug(baseUrl, slug));
      } catch (err) {
        slugErrors.set(slug, err instanceof Error ? err.message : String(err));
      }
    }),
  );

  const diffs: Diff[] = [];
  for (const [id, mapping] of Object.entries(ID_MAP)) {
    if (slugErrors.has(mapping.slug)) {
      diffs.push({
        id,
        slug: mapping.slug,
        path: mapping.path,
        kind: mapping.kind,
        legacy: legacyMap.get(id)?.rawText ?? null,
        v3: null,
        driftPct: null,
        status: "endpoint_error",
        note: slugErrors.get(mapping.slug),
      });
      continue;
    }
    const v3Val = getByPath(slugData.get(mapping.slug), mapping.path);
    const diff = compare(mapping, legacyMap.get(id), v3Val);
    diff.id = id;
    diffs.push(diff);
  }

  const totals = {
    fields: diffs.length,
    match: diffs.filter((d) => d.status === "match").length,
    diverge: diffs.filter((d) => d.status === "diverge").length,
    missing_legacy: diffs.filter((d) => d.status === "missing_legacy").length,
    missing_v3: diffs.filter((d) => d.status === "missing_v3").length,
    endpoint_error: diffs.filter((d) => d.status === "endpoint_error").length,
  };
  const matchPct = totals.fields === 0 ? 0 : (totals.match / totals.fields) * 100;

  const report = {
    generated_at: new Date().toISOString(),
    base_url: baseUrl,
    legacy_source: legacySource,
    threshold_pct: threshold,
    numeric_tolerance_pct: NUMERIC_TOLERANCE_PCT,
    match_pct: Number(matchPct.toFixed(2)),
    totals,
    slug_errors: Object.fromEntries(slugErrors),
    diffs,
  };

  await mkdir(dirname(outPath), { recursive: true });
  await writeFile(outPath, JSON.stringify(report, null, 2) + "\n");
  process.stdout.write(`[parity] match=${report.match_pct}% report=${outPath}\n`);

  return matchPct >= threshold ? 0 : 1;
}

main().then((code) => process.exit(code)).catch((err) => {
  console.error(err);
  process.exit(2);
});
