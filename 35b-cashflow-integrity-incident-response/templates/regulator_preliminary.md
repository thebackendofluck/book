<!--
Preliminary regulator notification (under-1-hour, jurisdiction-agnostic).
Render-time substitution fills {{jurisdiction_legal_reference}} from the
notification matrix in Chapter 35b. Keep to the facts known at T+1h; a full
report follows within the statutory window.
-->
# Preliminary Incident Notification

**To:** {{regulator_name}} ({{jurisdiction}})
**From:** {{operator_legal_name}}, licence {{licence_number}}
**Classification:** Preliminary, cash-flow integrity incident
**Reported at:** {{report_timestamp_utc}} (within 1 hour of detection)
**Legal basis:** {{jurisdiction_legal_reference}}

## 1. What we know

- **Detected at:** {{detected_timestamp_utc}}
- **Nature:** {{incident_summary}}
- **Systems affected:** {{affected_systems}}
- **Player funds at risk (preliminary estimate):** {{funds_at_risk}}
- **Player-facing impact:** {{player_impact}}

## 2. Immediate actions taken

- {{containment_action_1}}
- {{containment_action_2}}
- Affected withdrawals placed under review (see player communication).

## 3. What is not yet known

{{open_questions}}

## 4. Next update

A fuller assessment will follow by {{next_update_deadline_utc}}, and the
complete report within the {{statutory_window}} statutory window.

**Incident contact:** {{incident_contact_name}}, {{incident_contact_email}}, {{incident_contact_phone}}
