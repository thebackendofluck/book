<!--
Internal post-incident review for a cash-flow integrity incident. Extends the
Chapter 35 PIR format with cash-flow-specific sections: treasury impact,
regulatory exposure, and a clawback decision matrix. Blameless. Chapter 35b.
-->
# Post-Incident Review: {{incident_id}}

**Status:** {{pir_status}}  **Severity:** {{severity}}  **Author:** {{author}}
**Incident window:** {{start_utc}} → {{end_utc}}  **Time to detect / contain / resolve:** {{ttd}} / {{ttc}} / {{ttr}}

## 1. Summary

{{one_paragraph_summary}}

## 2. Timeline

| Time (UTC) | Event | Source |
|------------|-------|--------|
| {{t0}} | {{t0_event}} | {{t0_source}} |
| {{t1}} | {{t1_event}} | {{t1_source}} |

## 3. Root cause

{{root_cause}}

## 4. Detection and response

- **How it was detected:** {{detection_path}}
- **What worked:** {{what_worked}}
- **What slowed us down:** {{what_slowed_us}}

## 5. Treasury impact (cash-flow specific)

- **Gross funds exposed:** {{gross_exposure}}
- **Net realised loss:** {{net_loss}}
- **Reserve / float drawn:** {{reserve_drawn}}
- **Reconciliation status:** {{reconciliation_status}}

## 6. Regulatory exposure (cash-flow specific)

- **Jurisdictions notified:** {{jurisdictions_notified}}
- **Statutory deadlines met:** {{deadlines_met}}
- **Open regulator questions:** {{open_regulator_questions}}

## 7. Clawback decision matrix (cash-flow specific)

| Cohort | Overpaid amount | Recoverable? | Action | Rationale |
|--------|-----------------|--------------|--------|-----------|
| {{cohort_1}} | {{amount_1}} | {{recoverable_1}} | {{action_1}} | {{rationale_1}} |
| {{cohort_2}} | {{amount_2}} | {{recoverable_2}} | {{action_2}} | {{rationale_2}} |

Guidance: pursue clawback only where funds are clearly recoverable and the
player was not acting in good faith on a published balance; document every
decision for audit.

## 8. Action items

| # | Action | Owner | Due | Prevents recurrence of |
|---|--------|-------|-----|------------------------|
| 1 | {{action_item_1}} | {{owner_1}} | {{due_1}} | {{prevents_1}} |
| 2 | {{action_item_2}} | {{owner_2}} | {{due_2}} | {{prevents_2}} |
