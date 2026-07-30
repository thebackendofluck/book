# Policy diff — device-continuity identifier for fraud prevention

This document records the exact wording changes the DPO must sign off on
before the `receipt_id ↔ player_id` binding is activated in production.
Target policies: `thebackendofluck.com/privacy.html`, `/cookies.html`,
`portrasdasorte.com.br/privacy.html`, `/cookies.html`.

The changes are additive — no existing text is removed.

## 1. Cookie Policy — Strictly necessary category

**Current row:**

> Strictly necessary — Required for login, wallet and fraud prevention.
> Cannot be switched off.

**New wording:**

> Strictly necessary — Required for login, wallet, session integrity and
> fraud prevention. Includes a **device-continuity identifier**
> (`cc_v1.receipt_id` in `localStorage`) that stays stable while your
> consent is valid. This is the tag we use to detect account takeover,
> multi-account abuse and impossible-travel patterns. You can reset it
> any time by opening *Cookie settings* and clicking *reset* — a new
> identifier will be issued on your next visit. Because this protects
> the contract (Art. 6(1)(b)) and legitimate security interest (Art.
> 6(1)(f)), it falls under the ePrivacy Art. 5(3) exemption and is
> not itself subject to opt-in consent.

## 2. Privacy Notice — new row in the Purposes table

Insert a new row after "Fraud prevention and license enforcement":

| Purpose | Legal basis | Retention |
|---|---|---|
| Device-continuity signal for fraud prevention and responsible-gambling enforcement | GDPR Art. 6(1)(f) legitimate interest; DPIA filed | 12 months after last sign-in, or immediately on erasure request |

## 3. Privacy Notice — Brazilian addendum (LGPD)

Portuguese addition under §2.3 (new subsection):

> 2.3. Identificador de continuidade de dispositivo
>
> Para prevenir fraude, uso indevido de múltiplas contas e padrões de
> acesso impossíveis (viagem-impossível), vinculamos o identificador
> `cc_v1.receipt_id` armazenado no seu navegador ao seu `player_id`
> após o login. Essa vinculação é feita com base em legítimo interesse
> (LGPD Art. 7, IX) com análise de impacto de proteção de dados (DPIA)
> registrada. Você pode apagar o identificador a qualquer momento em
> *Configurações de cookies → redefinir*. No caso de exercício do
> direito de eliminação (LGPD Art. 18, VI), a vinculação e todos os
> registros correlatos são apagados no mesmo fluxo, antes do encerramento
> da solicitação.

## 4. Privacy Notice — section 7a (granular consent) — appended paragraph (EN)

> The device-continuity identifier described above is never shared with
> any marketing vendor and never appears in the newsletter opt-in list.
> Our server-side ACLs prevent the marketing process from reading the
> binding store; marketing uses its own opaque identifier.

## 5. Data-flow summary for the DPO

- `cc_v1.receipt_id` created by the consent banner on first visit.
- POSTed to `/api/v2/consent/record` — stored in `dash:consent:log` (Redis DB 0 masked in dashboard).
- On login, bound to `player_id` in `auth:device_binding:<player_id>` (Redis DB 0) and persisted to Postgres `player_device_bindings` for long-term retention.
- Fraud service reads binding on each transaction, emits signals: `stable_days`, `multi_account_count`, `consent_reset_count_30d`, `impossible_travel_flag`.
- On Art. 17 / Art. 18 VI erasure, the DSR workflow deletes the binding, the consent log entries, and the Postgres row in a single cascade before the DSR is marked `completed`.

## 6. What we do NOT do

- Fingerprinting (canvas, WebGL, fonts).
- Cross-site tracking.
- Re-use of the receipt for marketing attribution.
- Export of the identifier to third parties.

Signature block:

| Role | Name | Date | Signature |
|---|---|---|---|
| DPO | ____________________ | ____________________ |   |
| Engineering | ____________________ | ____________________ |   |
| Privacy counsel | ____________________ | ____________________ |   |

Effective only after all three signatures.
