# Player Breach Notification Email
## GDPR Article 34 / UK GDPR Article 34 Compliant Template

**When to use:** When a breach is assessed as "likely to result in a high risk to the rights
and freedoms" of affected players. High risk includes: government ID exposure, payment data,
risk of identity theft, financial fraud, or harm to vulnerable individuals.

**Deadline:** Without undue delay after the decision to notify is made. Target: 48-72 hours
after the Art. 34 assessment is complete. Do not wait for the full forensic investigation.

**Channel:** Email (primary). SMS for players without confirmed email. In-app notification
for all active players regardless of email delivery status.

**Do NOT:** Send from a no-reply address. Players who receive this email will want to respond.
Use your support address or a dedicated breach-response address monitored by trained staff.

---

## Email: Subject Lines

Choose based on severity and data involved:

- `Important security notice regarding your [Brand] account`
- `Security update: Action required for your [Brand] account`
- `We need to tell you about a security incident affecting your account`

Avoid: "Data breach" in the subject line — this triggers spam filters and causes alarm before
the player has context. "Security notice" or "security update" performs better for delivery
and reads as more controlled. The body explains the full situation.

---

## Email: Body

---

Dear [Player First Name],

We are writing to let you know about a security incident that affected our systems and may
have involved your personal information. We believe in being transparent with you, and we
want to give you the facts clearly.

**What happened**

On [date], our security team detected that an unauthorised third party had accessed part
of our systems. The access occurred between approximately [start date] and [detection date].
We identified and stopped the intrusion at [detection time] and immediately took steps to
secure our systems.

**What information was involved**

The personal information that may have been accessed includes:

- Your name and email address
- Your username and account ID
- [Your date of birth]
- [Your deposit and withdrawal history — transaction amounts and dates, not full payment details]
- [A government-issued ID number you provided during identity verification]

**What was NOT involved:**

- Your full payment card numbers [we do not store these — card payments are processed by
  our PCI-DSS certified payment provider and card numbers never pass through our systems]
- Your full bank account details
- [Other exclusions specific to your architecture]

Your password is stored using one-way cryptographic hashing. We have no evidence that
password hashes were obtained in a usable form, but we are requiring password resets as a
precaution for all affected accounts.

**What we have done**

Within hours of detection:

- We isolated and shut down the unauthorised access
- We notified [the Information Commissioner's Office / relevant data protection authority]
  and [relevant gambling regulator] as required by law
- We engaged external cybersecurity experts to conduct a full forensic investigation
- We have strengthened our systems to prevent similar incidents

**What we recommend you do**

**1. Reset your password** — When you next log in to your [Brand] account, you will be
prompted to set a new password. Please choose one that is unique to your [Brand] account
and not used on any other website.

**2. Watch out for phishing** — Unfortunately, your email address and name may be used by
scammers in phishing attempts. We will never contact you asking for your full password,
payment card number, or to click a link to "verify" your account credentials. If you receive
a suspicious email claiming to be from us, forward it to security@[brand].com.

**3. Monitor your accounts** — Keep an eye on your bank accounts and email for unusual
activity. Contact your bank immediately if you notice anything suspicious.

**4. Consider a fraud alert** — [UK: You can add a CIFAS protective registration at
cifas.org.uk which alerts lenders to verify identity more carefully before approving credit.]
[EU: Contact your national credit bureau for similar protections.]

**A gesture of goodwill**

We are providing all affected players with [12 months] of complimentary identity
monitoring through [Provider Name]. This service will alert you if your personal
information appears on dark web sources or if new credit is applied for in your name.
You will receive a separate email from [Provider] within [X] days with activation
instructions — no credit card required.

**Your rights**

Under [UK GDPR / GDPR], you have the right to:

- Obtain confirmation of what data we hold about you (Subject Access Request)
- Request correction of inaccurate data
- Request deletion of your data in certain circumstances
- Lodge a complaint with your national data protection authority

To exercise any of these rights, or for more information about this incident, please
contact our Data Protection Officer at [dpo@brand.com].

If you are in the UK and wish to make a complaint to the regulator:
Information Commissioner's Office — ico.org.uk/make-a-complaint — 0303 123 1113

[EU players: Your national data protection authority — edpb.europa.eu/about-edpb/members_en]

**We are sorry**

This should not have happened. We have invested significantly in security and we fell short
of the standard our players and we ourselves expect. We are committed to making the changes
necessary to ensure it does not happen again, and to rebuilding the trust this incident
has affected.

If you have questions that this email doesn't answer, our dedicated breach support team
is available at [breach-support@brand.com] or [phone number], [hours of availability].

Sincerely,

[CEO or CISO Name]  
[Title]  
[Brand Name]

---

*[Unsubscribe link — though note: regulatory breach notifications should be sent to all
affected players regardless of marketing preferences. Make this clear in your template
system configuration so this email is not blocked by unsubscribe suppression.]*

---

## Implementation Notes

**Segmentation:** Send only to players whose accounts are confirmed in the affected dataset.
Do not send to your entire player base unless the entire database was affected.

**Personalisation variables:** [Player First Name], account-specific data affected, and
the identity monitoring provider activation link should be individually generated.

**A/B testing:** Do not A/B test this email. Send the same version to all affected players
and document what was sent, when, and to how many — regulators may ask.

**Delivery tracking:** Log send status, bounce rate, and delivery confirmation per player.
This record is required for regulatory compliance evidence.

**Timing:** Aim for daytime delivery in the player's timezone where feasible. Avoid sending
at 3 AM — a security notice email in the middle of the night feels more alarming than helpful.

**Follow-up:** If a player emails asking questions, respond within 24 hours. Staff your
breach support line adequately. The player experience after a breach is as important as
the technical response.
