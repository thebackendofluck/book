# Dashboard v3 Cutover Plan

Owner: Platform / Ops Dashboard
Date: 2026-04-14
Status: in-progress (parity gate + flag registry shipped)

This plan governs the legacy → v3 cutover for `new.acmetocasino.com/dashboard.html`
(legacy) and `new.acmetocasino.com/v2/dashboard/` (v3, 41 tabs).

The cutover is **per-tab**. Each tab moves through four phases:

1. Dual-render (default today): both surfaces live, banner invites users to v3.
2. Opt-in: users individually enable v3 for the tab via the banner.
3. Default-on: legacy still served on direct link, but `redirect_<tab>_to_v3`
   is flipped, so the legacy route 302s to v3.
4. Decommissioned: legacy DOM nodes removed, parity script tolerates absence.

Every transition is one feature-flag flip — no redeploy required.

---

## 1. Per-tab promotion criteria

A tab may advance to **default-on** only when **all** of the following hold for
**7 consecutive days**:

| Gate                  | Threshold                                                  | Source                                                |
| --------------------- | ---------------------------------------------------------- | ----------------------------------------------------- |
| Parity (DOM vs API)   | `match_pct ≥ 99%` for that tab's IDs                       | `scripts/compare-parity.ts` CI report (daily run)     |
| Sentry error rate     | `< 0.1%` of v3 sessions for the tab                        | Sentry project `ops-dashboard-v3`, route filter       |
| Opt-in volume         | `≥ 30 distinct ops users` opted-in via the banner          | telemetry event `migration.banner.opt_in` w/ tab tag  |
| Performance budget    | `p95 LCP ≤ 2.5s`, `p95 INP ≤ 200ms`                        | Vercel Speed Insights / Web Vitals                    |
| No open Sev≤2 tickets | zero unresolved migration tickets tagged `dashboard-v3`    | issue tracker                                         |

Gates are checked by the `migration-status` GitHub Action, which reads the
parity report and the Sentry/telemetry summaries and refuses to flip the
`redirect_<tab>_to_v3` flag until they all pass.

---

## 2. Cutover order

Easier surfaces first; defer anything with regulator exposure to last.

1. **overview** — read-only KPIs, low risk.
2. **finops** — single source-of-truth (warehouse), already audited.
3. **fraud** — high traffic but read-only.
4. **compliance** — regulator-touched; coordinate with legal sign-off.
5. **infrastructure** — depends on Prom/Loki passthrough.
6. Remaining 36 tabs in alphabetical order, batched 4 per week.

---

## 3. Rollback

**Single-command rollback** for any tab:

```bash
# server-side (preferred)
curl -X POST https://new.acmetocasino.com/api/v3/flags \
  -H "Authorization: Bearer $OPS_ADMIN_TOKEN" \
  -d '{"flag":"redirect_overview_to_v3","value":false}'

# or, env-only (next deploy)
NEXT_PUBLIC_MIGRATION_FLAGS="redirect_overview_to_v3=false" pnpm deploy
```

Effects:

- `redirect_<tab>_to_v3=false` → legacy route stops 302-ing.
- `v3_enabled_for_<tab>=false` → v3 hides the tab and shows a "temporarily
  unavailable, use legacy" banner.
- `show_v3_banner=false` → kills the legacy banner entirely (kill-switch).

All flags are flat booleans in `src/lib/migration-flags.ts`. localStorage
overrides on the user's device are cleared with `clearUserPreference(flag)`.

---

## 4. Communication

### 4.1 Email template (sent T-7 days before each tab default-on)

```
Subject: Dashboard v3 — <Tab Name> goes default on <YYYY-MM-DD>

Hi ops team,

The <Tab Name> tab in our internal dashboard has met all parity, error and
adoption gates for 7 consecutive days. Starting <YYYY-MM-DD>, opening
https://new.acmetocasino.com/dashboard.html#<tab> will redirect to
https://new.acmetocasino.com/v2/dashboard/<tab>.

What you need to do
  • Nothing if you've already been using v3.
  • If you must temporarily reach the legacy view, append ?legacy=1 to the
    URL — this bypasses the redirect for the current session only.

What changes
  • Visual refresh (new tokens / layout — see Figma file linked in #ops).
  • Same KPIs, same numbers (parity gate enforces ≤ 1% drift).
  • New keyboard shortcut: g <tab-letter> jumps directly.

Rollback
  • Sev≤2 issue? Reply-all and an on-caller will flip
    `redirect_<tab>_to_v3=false` within 5 minutes — no deploy needed.

Thanks,
Platform
```

### 4.2 In-app

- Banner copy in legacy: "Try the new dashboard — opt in" (existing v3-toast).
- Sticky note in v3 for first 7 days post-default: "Legacy still reachable at
  `?legacy=1` until <date+30d>".

---

## 5. Go / no-go checklist (12 items)

Run before flipping each tab to default-on. All must be ticked.

- [ ] Parity report shows `match_pct ≥ 99%` for this tab — last 7 daily runs.
- [ ] Sentry error rate `< 0.1%` for v3 routes of this tab — last 7 days.
- [ ] At least 30 distinct ops users have opt-in events for this tab.
- [ ] p95 LCP ≤ 2.5s and p95 INP ≤ 200ms on the v3 tab in Speed Insights.
- [ ] No open Sev≤2 tickets tagged `dashboard-v3:<tab>`.
- [ ] Visual redesign for this tab signed off by design (linked Figma).
- [ ] All v3 endpoints for this tab pass the contract tests in CI.
- [ ] Idempotency keys verified on any mutating endpoints used by this tab
      (see `project_idempotency_2026_04_14`).
- [ ] Email announcement scheduled / sent T-7d.
- [ ] On-caller paged-in for the cutover window; rollback command rehearsed.
- [ ] Audit log entry created in `dashboard-v3-migration` channel with the
      flag values pre-flip.
- [ ] Post-flip smoke check: open the legacy URL, verify 302 to v3, verify v3
      tab loads with non-zero data.

---

## 6. Decommissioning

A tab is removed from the legacy `dashboard.html` only after **30 days** of
default-on with no rollback events. Removal PR must:

- Delete the legacy tab DOM and any tab-only JS.
- Update `ID_MAP` in `scripts/compare-parity.ts` to drop the now-absent ids.
- Set `v3_enabled_for_<tab>` and `redirect_<tab>_to_v3` to compile-time
  constants (remove from registry once all tabs are decommissioned).

When the last tab is decommissioned, redirect `dashboard.html` itself to
`/v2/dashboard/` and archive the legacy file.
