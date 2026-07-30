# Security Solutions Comparison for iGaming — AcmeToCasino Platform

This document compares security tooling across eight categories relevant to the AcmeToCasino iGaming platform. Scores and assessments reflect the specific operational context of a regulated online gambling operator: high transaction volume, strict regulatory compliance (GDPR, PCI DSS, MGA/UKGC/Curaçao licensing), real-time fraud risk, and the combination of cloud and on-premise infrastructure.

**Rating methodology**
- **iGaming relevance (1–5)**: how well the tool addresses iGaming-specific threats (bonus abuse, payment fraud, geo-restriction enforcement, AML/KYC compliance logging)
- **Integration difficulty**: Easy = ready-to-use APIs or managed service; Medium = configuration work required; Hard = significant engineering effort
- Pricing is approximate (2025–2026 market rates). Always request a vendor quote.

---

## Table of Contents

1. [WAF — Cloud](#1-waf--cloud)
2. [WAF — On-Premise](#2-waf--on-premise)
3. [SIEM](#3-siem)
4. [SOAR](#4-soar)
5. [IDS/IPS](#5-idsips)
6. [DDoS Protection](#6-ddos-protection)
7. [Bot Management](#7-bot-management)
8. [Threat Intelligence](#8-threat-intelligence)
9. [Summary Comparison Tables](#9-summary-comparison-tables)

---

## 1. WAF — Cloud

### AWS WAF v2

| Attribute | Detail |
|---|---|
| **Vendor** | Amazon Web Services |
| **Type** | Cloud Service |
| **Best for** | Workloads already on AWS; tightly integrated with CloudFront, ALB, API Gateway |
| **Pricing** | Web ACL: $5/month; Rule: $1/month; $0.60 per 1M requests |
| **iGaming Relevance** | 5/5 |
| **Integration Difficulty** | Easy |

**Key Features**
- Rate-based rules (per-IP request counting over a 5-minute window)
- Managed rule groups: `AWSManagedRulesSQLiRuleSet`, `AWSManagedRulesKnownBadInputsRuleSet`, `AWSManagedRulesBotControlRuleSet`
- IP set management via API (used by `waf_auto_block.py` in this project)
- CAPTCHA and challenge actions
- CloudWatch metric integration
- Geo-match statements for jurisdiction enforcement
- Scope: CLOUDFRONT (global) or REGIONAL (per-region ALB/API GW)

**Pros**
- Native AWS integration — no additional infrastructure
- SOAR-controllable via boto3 API (critical for this platform)
- Pay-as-you-go pricing scales linearly
- Supports dual-scope blocking (CloudFront + ALB)

**Cons**
- Rate-based rules use a fixed 5-minute window (cannot tune to per-second granularity)
- Custom rules require familiarity with the WAF expression language
- Bot Control adds $10/month per Web ACL + per-request cost
- No built-in threat intelligence feed beyond managed rules

**Official Docs**: [https://docs.aws.amazon.com/waf/](https://docs.aws.amazon.com/waf/)

---

### Cloudflare WAF

| Attribute | Detail |
|---|---|
| **Vendor** | Cloudflare |
| **Type** | Cloud Service |
| **Best for** | Platforms requiring global CDN + WAF in a single product; DDoS + bot management bundled |
| **Pricing** | Pro: $20/month; Business: $200/month; Enterprise: custom |
| **iGaming Relevance** | 5/5 |
| **Integration Difficulty** | Easy |

**Key Features**
- Managed OWASP Core Rule Set
- Custom firewall rules with the Cloudflare Rules Language (full HTTP context)
- Rate limiting (per URL, per IP, per cookie/token)
- Bot Fight Mode / Super Bot Fight Mode / Bot Management
- Turnstile (CAPTCHA replacement)
- Workers for custom logic at the edge
- IP reputation scores from Cloudflare's global network (~20% of all internet traffic)
- Zone Lockdown and geo-blocking built-in

**Pros**
- Arguably the best DDoS protection available at any price tier
- Anycasted CDN means mitigation happens at the edge, not at origin
- Real-time analytics with Logpush to S3/SIEM
- REST API for rule management (suitable for SOAR integration)
- Free tier available for small operators

**Cons**
- Enterprise Bot Management is expensive (custom pricing, typically $3k+/month)
- All traffic transits Cloudflare — data residency concerns for GDPR jurisdictions
- Cannot be deployed on-premise
- Workers add complexity for custom logic

**Official Docs**: [https://developers.cloudflare.com/waf/](https://developers.cloudflare.com/waf/)

---

### Akamai App & API Protector

| Attribute | Detail |
|---|---|
| **Vendor** | Akamai Technologies |
| **Type** | Cloud Service |
| **Best for** | Large enterprises requiring global scale, dedicated support, and regulatory compliance features |
| **Pricing** | Enterprise custom pricing; typically $30k–$200k+/year |
| **iGaming Relevance** | 4/5 |
| **Integration Difficulty** | Medium |

**Key Features**
- Kona Site Defender WAF with adaptive security
- Prolexic DDoS scrubbing (up to 20 Tbps global capacity)
- Bot Manager Premier with device fingerprinting
- API Security (shadow API discovery)
- Edge DNS for availability
- Extensive compliance reporting (PCI DSS, SOC 2)
- Dedicated Security Operations Centre for enterprise clients

**Pros**
- Largest CDN footprint globally
- Industry-leading DDoS mitigation capacity
- Strong compliance documentation for regulators
- 24/7 managed service option

**Cons**
- High cost — difficult to justify for mid-size operators
- Complex onboarding process
- API less developer-friendly than Cloudflare
- Long-term contracts typically required

**Official Docs**: [https://www.akamai.com/products/app-and-api-protector](https://www.akamai.com/products/app-and-api-protector)

---

### Imperva Cloud WAF

| Attribute | Detail |
|---|---|
| **Vendor** | Imperva (Thales group) |
| **Type** | Cloud Service |
| **Best for** | Operators requiring both cloud WAF and on-premise SecureSphere under one vendor |
| **Pricing** | Essentials: ~$500/month; Professional: ~$2k/month; Enterprise: custom |
| **iGaming Relevance** | 4/5 |
| **Integration Difficulty** | Medium |

**Key Features**
- Crowdsourced threat intelligence from 50B+ monthly requests
- Advanced bot protection with device fingerprinting
- DDoS mitigation (up to 6 Tbps)
- API security with discovery and posture management
- Virtual patching for zero-days
- PCI compliance reporting

**Pros**
- Strong iGaming references (several major operators use Imperva)
- Consistent rule sets between cloud WAF and on-prem SecureSphere
- Advanced data masking in audit logs (PII protection)

**Cons**
- Premium pricing
- Less developer-friendly API than AWS WAF or Cloudflare
- Acquisition by Thales has slowed product innovation

**Official Docs**: [https://www.imperva.com/products/web-application-firewall-waf/](https://www.imperva.com/products/web-application-firewall-waf/)

---

### Azure WAF

| Attribute | Detail |
|---|---|
| **Vendor** | Microsoft Azure |
| **Type** | Cloud Service |
| **Best for** | Workloads on Azure; operators using Azure Front Door or Application Gateway |
| **Pricing** | Application Gateway WAF v2: ~$0.443/hour + $0.0125/10 rules; Front Door WAF: $5/policy/month |
| **iGaming Relevance** | 3/5 |
| **Integration Difficulty** | Easy (on Azure) / Medium (multi-cloud) |

**Key Features**
- OWASP 3.2 CRS managed rules
- Custom rules with full HTTP context
- Geo-filtering
- Rate limiting (on Front Door WAF)
- Bot protection ruleset
- DDoS Protection Standard integration

**Pros**
- Tight integration with Azure services
- Familiar interface for Microsoft shops
- Competitive pricing

**Cons**
- Feature parity with AWS WAF/Cloudflare still catching up
- Less suitable for multi-cloud or AWS-primary architectures
- SOAR API less mature than AWS equivalents

**Official Docs**: [https://learn.microsoft.com/en-us/azure/web-application-firewall/](https://learn.microsoft.com/en-us/azure/web-application-firewall/)

---

### GCP Cloud Armor

| Attribute | Detail |
|---|---|
| **Vendor** | Google Cloud Platform |
| **Type** | Cloud Service |
| **Best for** | GCP-native workloads; operators using Cloud Load Balancing |
| **Pricing** | Security policies: $5/policy/month; $0.75/million requests beyond 1M |
| **iGaming Relevance** | 3/5 |
| **Integration Difficulty** | Easy (on GCP) |

**Key Features**
- OWASP Top 10 pre-configured rules
- Adaptive Protection (ML-based DDoS detection)
- Bot management (reCAPTCHA Enterprise integration)
- IP/geo-based allow/deny lists
- Rate limiting with adaptive threshold

**Pros**
- Excellent DDoS L3/L4 coverage via Google's global network
- reCAPTCHA Enterprise integration is best-in-class for bot detection
- Competitive pricing

**Cons**
- Limited SOAR API surface compared to AWS WAF
- Best suited exclusively for GCP architectures

**Official Docs**: [https://cloud.google.com/armor/docs](https://cloud.google.com/armor/docs)

---

### Fastly Next-Gen WAF (Signal Sciences)

| Attribute | Detail |
|---|---|
| **Vendor** | Fastly |
| **Type** | Cloud Service / Hybrid (agent-based) |
| **Best for** | DevSecOps teams; operators wanting in-app WAF with high false-positive control |
| **Pricing** | Starting ~$2,500/month for production traffic |
| **iGaming Relevance** | 4/5 |
| **Integration Difficulty** | Medium |

**Key Features**
- Agent runs alongside application (not as a proxy) — ultra-low latency
- SmartParse for low false positives
- Power Rules (custom detection logic)
- API discovery and protection
- Multi-cloud and on-premise support (agent-based deployment)
- Real-time dashboards and alerting

**Pros**
- Very low false-positive rate — ideal for complex iGaming APIs
- Agent model means detection uses full application context
- Works across cloud providers and bare-metal

**Cons**
- Higher cost than cloud-native WAFs
- Agent deployment adds operational overhead per service
- Fastly acquisition integration still ongoing

**Official Docs**: [https://docs.fastly.com/products/ngwaf](https://docs.fastly.com/products/ngwaf)

---

### Sucuri Website Security

| Attribute | Detail |
|---|---|
| **Vendor** | Sucuri (GoDaddy) |
| **Type** | Cloud Service |
| **Best for** | Small to mid-size operators; WordPress-based sites |
| **Pricing** | Basic: $199/year; Pro: $299/year; Business: $499/year |
| **iGaming Relevance** | 2/5 |
| **Integration Difficulty** | Easy |

**Key Features**
- WAF + CDN bundled
- Malware scanning and removal
- DDoS protection (limited)
- SSL certificate management
- cPanel and WordPress integration

**Pros**
- Very affordable for small operators
- Easy setup
- Includes malware remediation

**Cons**
- Not designed for high-traffic iGaming platforms
- Limited API for SOAR integration
- DDoS capacity insufficient for sustained attacks
- Not suitable for PCI DSS Level 1

---

## 2. WAF — On-Premise

### ModSecurity v3

| Attribute | Detail |
|---|---|
| **Vendor** | OWASP / Open Source (Trustwave contributed, now community-maintained) |
| **Type** | Open Source |
| **Best for** | Nginx/Apache on-premise reverse proxies; OWASP CRS integration |
| **Pricing** | Free |
| **iGaming Relevance** | 5/5 |
| **Integration Difficulty** | Medium |

**Key Features**
- OWASP Core Rule Set (CRS) — maintained by OWASP, covers OWASP Top 10
- Nginx connector (`libmodsecurity3` + `libnginx-mod-security2`)
- SecRule language for custom detection logic
- Audit logging (full request/response capture)
- IP-based blocking, session tracking, rate limiting
- Dynamic rule loading without restart

**Pros**
- Industry standard for on-premise HTTP WAF
- OWASP CRS provides comprehensive coverage out of the box
- Full auditability — critical for iGaming compliance
- No licensing cost
- Deeply configurable

**Cons**
- v3 is a rewrite with some v2 feature gaps (no multi-threading issues, but some rules not yet ported)
- Rule tuning requires significant expertise to minimise false positives
- No SLA or commercial support (commercial support available from TrustWave / others)
- Management at scale requires a control plane

**This project uses**: ModSecurity v3 on Nginx (`onprem-waf/modsecurity/`)

**Official Docs**: [https://github.com/owasp-modsecurity/ModSecurity](https://github.com/owasp-modsecurity/ModSecurity)

---

### F5 BIG-IP Advanced WAF

| Attribute | Detail |
|---|---|
| **Vendor** | F5 Networks |
| **Type** | Commercial (hardware appliance + virtual edition) |
| **Best for** | Large enterprises requiring deep L7 inspection, SSL offload, and ADC in one appliance |
| **Pricing** | Virtual edition: ~$10k–$50k/year (license); hardware: $50k–$200k+ |
| **iGaming Relevance** | 4/5 |
| **Integration Difficulty** | Hard |

**Key Features**
- Full ADC (Application Delivery Controller) with WAF module
- iRules for highly custom traffic manipulation
- DataSafe for credential and PII masking in responses
- Bot signatures and proactive bot defence
- L7 DDoS protection
- OWASP CRS integration
- High availability (active/active, active/passive)

**Pros**
- Market leader for on-premise ADC/WAF
- Extremely powerful and configurable
- Excellent HA and failover capabilities
- Strong regulatory compliance documentation

**Cons**
- Very high cost and complexity
- Requires dedicated F5 expertise (F5 certification path)
- Slower to integrate with modern DevOps pipelines
- Hardware refresh cycles add operational burden

**Official Docs**: [https://techdocs.f5.com/en-us/bigip-15-1-0/big-ip-asm-implementations.html](https://techdocs.f5.com/en-us/bigip-15-1-0/big-ip-asm-implementations.html)

---

### Imperva SecureSphere WAF

| Attribute | Detail |
|---|---|
| **Vendor** | Imperva (Thales) |
| **Type** | Commercial (hardware + virtual) |
| **Best for** | Operators who need consistent policies across cloud WAF and on-premise |
| **Pricing** | Virtual: ~$20k–$80k/year; hardware: $50k–$150k+ |
| **iGaming Relevance** | 4/5 |
| **Integration Difficulty** | Hard |

**Key Features**
- ThreatRadar threat intelligence integration
- Dynamic profiling (learns application behaviour)
- Database activity monitoring (DAM) in the same platform
- Web scraping protection
- Audit trail for PCI DSS and GDPR compliance

**Pros**
- Consistent policy management between cloud and on-prem
- Strong database protection — important for player data
- PCI DSS and GDPR compliance templates

**Cons**
- High cost
- Complex management interface
- Imperva's direction under Thales ownership uncertain

---

### FortiWeb

| Attribute | Detail |
|---|---|
| **Vendor** | Fortinet |
| **Type** | Commercial (hardware + VM + cloud) |
| **Best for** | Organisations with existing Fortinet infrastructure (FortiGate, FortiSIEM) |
| **Pricing** | Virtual: ~$5k–$30k/year; hardware: $15k–$100k+ |
| **iGaming Relevance** | 3/5 |
| **Integration Difficulty** | Medium |

**Key Features**
- ML-based anomaly detection
- FortiGuard threat intelligence feed
- API security
- FortiView dashboard
- Integration with FortiGate NGFWs and FortiSIEM

**Pros**
- Good value within the Fortinet ecosystem
- Strong machine learning anomaly detection
- Unified security fabric reduces management overhead

**Cons**
- Less compelling outside the Fortinet ecosystem
- Smaller iGaming customer base than F5/Imperva

**Official Docs**: [https://docs.fortinet.com/product/fortiweb](https://docs.fortinet.com/product/fortiweb)

---

### Citrix ADC (formerly NetScaler) WAF

| Attribute | Detail |
|---|---|
| **Vendor** | Citrix / Cloud Software Group |
| **Type** | Commercial |
| **Best for** | Organisations with existing Citrix ADC deployments |
| **Pricing** | Enterprise: ~$10k–$50k/year |
| **iGaming Relevance** | 3/5 |
| **Integration Difficulty** | Medium |

**Key Features**
- WAF module integrated with ADC
- Positive and negative security models
- OWASP Top 10 coverage
- XML/JSON deep inspection
- SSL offloading + WAF in one appliance

**Pros**
- Familiar for Citrix shops
- Combined ADC + WAF reduces infrastructure

**Cons**
- Citrix's financial instability (multiple ownership changes) creates vendor risk
- Less innovation in WAF compared to cloud-native solutions

---

### Barracuda WAF

| Attribute | Detail |
|---|---|
| **Vendor** | Barracuda Networks |
| **Type** | Commercial (hardware + VM + cloud) |
| **Best for** | Mid-market enterprises seeking an affordable on-prem WAF with support |
| **Pricing** | ~$4k–$20k/year depending on throughput |
| **iGaming Relevance** | 3/5 |
| **Integration Difficulty** | Easy–Medium |

**Key Features**
- OWASP Top 10 protection
- Advanced Bot Protection
- DDoS mitigation
- SSL inspection
- Central management console (WAF Control Centre)

**Pros**
- More affordable than F5/Imperva
- Simpler management
- Good support quality

**Cons**
- Less powerful than enterprise alternatives
- Smaller market share = less iGaming-specific community knowledge

---

### Radware AppWall

| Attribute | Detail |
|---|---|
| **Vendor** | Radware |
| **Type** | Commercial (hardware + virtual + cloud) |
| **Best for** | Organisations who need AppWall bundled with Radware's DDoS DefensePro in one solution |
| **Pricing** | ~$10k–$50k/year |
| **iGaming Relevance** | 3/5 |
| **Integration Difficulty** | Medium |

**Key Features**
- Positive security model with auto-policy generation
- Emergency patching for zero-days
- API protection
- Integration with DefensePro for DDoS mitigation
- CSIRT Emergency Response Team (ERT)

**Pros**
- Tight integration with Radware DDoS protection
- Auto-learning mode reduces tuning effort
- 24/7 ERT for DDoS response

**Cons**
- Premium pricing
- Smaller ecosystem than F5/Imperva/Fortinet

---

### A10 Thunder WAF (AXAPI)

| Attribute | Detail |
|---|---|
| **Vendor** | A10 Networks |
| **Type** | Commercial |
| **Best for** | High-throughput environments needing SSL inspection at scale |
| **Pricing** | ~$8k–$40k/year |
| **iGaming Relevance** | 3/5 |
| **Integration Difficulty** | Medium |

**Key Features**
- AXAPI for automation and SOAR integration
- SSL/TLS offloading
- Advanced DDoS protection (CGN)
- OWASP Top 10 coverage
- Harmony controller for centralised management

**Pros**
- Strong performance benchmarks for SSL-heavy workloads
- AXAPI enables SOAR integration
- Competitive pricing vs F5

**Cons**
- Smaller market share than F5
- Less iGaming-specific documentation

---

## 3. SIEM

### Splunk Enterprise Security

| Attribute | Detail |
|---|---|
| **Vendor** | Splunk (Cisco) |
| **Type** | Commercial |
| **Best for** | Large enterprises with high log volume and complex correlation requirements |
| **Pricing** | ~$50k–$500k+/year (ingest-based pricing; $150–$300/GB/day is common) |
| **iGaming Relevance** | 5/5 |
| **Integration Difficulty** | Hard |

**Key Features**
- SPL (Search Processing Language) — highly expressive query language
- Enterprise Security app with pre-built iocs and detection rules
- UEBA (User and Entity Behaviour Analytics)
- Threat intelligence framework
- Risk-based alerting (RBA)
- SOAR integration via Splunk SOAR (formerly Phantom)
- 500+ data source integrations (add-ons)

**Pros**
- The market leader — largest ecosystem of integrations and community knowledge
- Extremely powerful search and correlation
- Risk-based alerting reduces alert fatigue
- Excellent for regulatory compliance reporting (GDPR, PCI DSS)
- Native SOAR integration (Splunk SOAR)

**Cons**
- Very expensive — ingest-based pricing punishes high-volume environments like iGaming
- Requires dedicated Splunk administrator
- On-prem infrastructure adds operational overhead
- Licensing complexity

**Official Docs**: [https://docs.splunk.com/Documentation/ES](https://docs.splunk.com/Documentation/ES)

---

### IBM QRadar SIEM

| Attribute | Detail |
|---|---|
| **Vendor** | IBM |
| **Type** | Commercial |
| **Best for** | Large financial and regulated enterprises with existing IBM infrastructure |
| **Pricing** | ~$30k–$200k+/year (events per second-based licensing) |
| **iGaming Relevance** | 4/5 |
| **Integration Difficulty** | Hard |

**Key Features**
- EPS-based (events per second) licensing — more predictable than ingest-based
- MITRE ATT&CK alignment
- QRadar SOAR (formerly Resilient) integration
- 450+ DSMs (Device Support Modules)
- Watson for Cyber Security integration
- Network Insights for flow analysis
- X-Force threat intelligence integration

**Pros**
- EPS pricing is predictable for iGaming platforms with bursty traffic
- Strong regulatory compliance framework
- IBM's X-Force threat intelligence is market-leading
- Good on-premise deployment story

**Cons**
- Complex and slow-to-innovate product line
- QRadar Cloud lagging behind AWS/Azure-native SIEMs
- IBM's acquisition history creates product roadmap uncertainty

**Official Docs**: [https://www.ibm.com/docs/en/qsip](https://www.ibm.com/docs/en/qsip)

---

### Microsoft Sentinel

| Attribute | Detail |
|---|---|
| **Vendor** | Microsoft |
| **Type** | Cloud Service (Azure-native) |
| **Best for** | Microsoft-heavy environments; operators using Azure |
| **Pricing** | Pay-as-you-go: ~$2.46/GB ingested; commitment tiers available |
| **iGaming Relevance** | 3/5 |
| **Integration Difficulty** | Medium |

**Key Features**
- Cloud-native SIEM + SOAR in one product
- 200+ data connectors including AWS, GCP, Okta, Salesforce
- KQL (Kusto Query Language) for analytics
- Microsoft Threat Intelligence integration
- UEBA built-in
- Playbooks (Azure Logic Apps) for SOAR automation

**Pros**
- Microsoft 365 and Azure native connectors are free ingestion
- Built-in SOAR via Logic Apps
- Competitive pricing for organisations ingesting mainly Microsoft data
- Rapid innovation pace

**Cons**
- KQL learning curve
- Less mature than Splunk for complex correlation
- Weaker community compared to Splunk for non-Microsoft data sources
- Azure dependency may not suit AWS-primary architectures

**Official Docs**: [https://learn.microsoft.com/en-us/azure/sentinel/](https://learn.microsoft.com/en-us/azure/sentinel/)

---

### Elastic Security (ELK)

| Attribute | Detail |
|---|---|
| **Vendor** | Elastic |
| **Type** | Open Source (basic tier) / Commercial (Platinum/Enterprise) |
| **Best for** | Teams with Elasticsearch expertise; log analytics + security in one stack |
| **Pricing** | Self-hosted: free (basic) / ~$95–$125/month per node (Platinum+); Elastic Cloud: usage-based |
| **iGaming Relevance** | 4/5 |
| **Integration Difficulty** | Medium |

**Key Features**
- Elasticsearch for log storage and full-text search
- Kibana SIEM app with pre-built detection rules
- Beats data shippers (Filebeat, Metricbeat) for log ingestion
- ECS (Elastic Common Schema) for log normalisation
- ML-based anomaly detection (Platinum tier)
- MITRE ATT&CK aligned detection rules
- Fleet for centralised agent management

**Pros**
- Open source — no licensing cost for basic tier
- Extremely scalable for high-volume log ingestion
- Full-text search is best-in-class for log analysis
- Filebeat/Fluent Bit are already standard in this project

**Cons**
- Detection capabilities less mature than Splunk ES
- SOAR requires separate integration (TheHive+Cortex or n8n)
- Elastic changed licensing to SSPL in 2021 (not OSI-approved) — OpenSearch fork available

**Official Docs**: [https://www.elastic.co/security](https://www.elastic.co/security)

---

### Wazuh

| Attribute | Detail |
|---|---|
| **Vendor** | Wazuh (Open Source) |
| **Type** | Open Source |
| **Best for** | Organisations wanting a free SIEM + HIDS + compliance platform |
| **Pricing** | Free (self-hosted); Wazuh Cloud: $300–$3k+/month |
| **iGaming Relevance** | 4/5 |
| **Integration Difficulty** | Medium |

**Key Features**
- HIDS (Host-based IDS) with agents on every server
- File integrity monitoring (FIM) — critical for PCI DSS
- Log collection and analysis (compatible with Elastic Stack)
- Vulnerability detection
- Security configuration assessment
- GDPR, PCI DSS, HIPAA compliance dashboards built-in
- Active response: runs scripts when alerts trigger

**Pros**
- Completely free and open source
- Built-in compliance dashboards for GDPR and PCI DSS — directly relevant
- Active response can trigger SOAR-like actions without n8n
- Large community

**Cons**
- Requires significant effort to tune and scale
- No commercial SLA
- Web interface (Kibana/OpenSearch Dashboards) needs Elastic/OpenSearch
- Alert correlation is less sophisticated than Splunk

**Official Docs**: [https://documentation.wazuh.com/](https://documentation.wazuh.com/)

---

### Graylog

| Attribute | Detail |
|---|---|
| **Vendor** | Graylog (Open Source core + commercial) |
| **Type** | Open Source / Commercial |
| **Best for** | Teams wanting an easy-to-operate log management + basic SIEM |
| **Pricing** | Open source: free; Graylog Security: ~$1,500–$5k+/month |
| **iGaming Relevance** | 3/5 |
| **Integration Difficulty** | Medium |

**Key Features**
- Centralised log management with GELF format
- Query language simpler than Lucene/KQL
- Alert notifications (email, Slack, PagerDuty)
- Dashboards and reports
- Graylog Security add-on: anomaly detection, sigma rules, threat intelligence

**Pros**
- Easier to operate than full ELK stack
- Good for mid-size deployments
- MongoDB + Elasticsearch backend

**Cons**
- Less sophisticated detection than Splunk/Elastic Security
- Graylog Security commercial add-on required for meaningful SIEM features
- Smaller ecosystem than ELK

**Official Docs**: [https://go2docs.graylog.org/](https://go2docs.graylog.org/)

---

## 4. SOAR

### n8n

| Attribute | Detail |
|---|---|
| **Vendor** | n8n GmbH (Open Source) |
| **Type** | Open Source / Commercial (n8n Cloud) |
| **Best for** | Developer-friendly SOAR / workflow automation with custom node development |
| **Pricing** | Self-hosted: free; n8n Cloud Starter: $20/month; Pro: $50/month; Enterprise: custom |
| **iGaming Relevance** | 5/5 |
| **Integration Difficulty** | Easy–Medium |

**Key Features**
- Visual workflow builder with code escape hatches (JavaScript/Python)
- 400+ built-in integrations
- Webhook triggers (used in this project)
- HTTP Request node for custom API calls
- Self-hosted — data never leaves your infrastructure
- Credentials encrypted at rest
- Execution history and audit logging

**Pros**
- Used in this project — proven for iGaming SOAR use case
- Developer-friendly: custom logic via code nodes
- Self-hosted option satisfies data residency requirements
- Cost-effective at any scale
- Active community

**Cons**
- Less mature than enterprise SOAR platforms for complex correlation
- No built-in threat intelligence management
- UI can be slow for very large workflows
- Community plugins vary in quality

**This project uses**: n8n as the primary SOAR engine

**Official Docs**: [https://docs.n8n.io/](https://docs.n8n.io/)

---

### Palo Alto XSOAR (Cortex)

| Attribute | Detail |
|---|---|
| **Vendor** | Palo Alto Networks |
| **Type** | Commercial |
| **Best for** | Large SOC teams needing enterprise-grade SOAR with Palo Alto ecosystem integration |
| **Pricing** | ~$50k–$200k+/year |
| **iGaming Relevance** | 4/5 |
| **Integration Difficulty** | Medium–Hard |

**Key Features**
- 900+ integration packs (including AWS WAF, JIRA, Slack, PagerDuty)
- Playbook visual designer
- War room for collaborative incident response
- Machine learning triage
- Case management built-in
- MITRE ATT&CK framework mapping

**Pros**
- Market-leading enterprise SOAR
- Massive integration library
- War room significantly improves team coordination during P1 incidents
- Strong case management

**Cons**
- Very expensive
- Requires dedicated SOAR engineering team
- Best value only when deeply integrated with Palo Alto NGFW/Prisma

**Official Docs**: [https://xsoar.pan.dev/docs/welcome](https://xsoar.pan.dev/docs/welcome)

---

### Splunk SOAR (formerly Phantom)

| Attribute | Detail |
|---|---|
| **Vendor** | Splunk (Cisco) |
| **Type** | Commercial |
| **Best for** | Organisations already using Splunk SIEM — native integration |
| **Pricing** | Bundled with Splunk Enterprise Security or standalone ~$30k+/year |
| **iGaming Relevance** | 4/5 |
| **Integration Difficulty** | Medium |

**Key Features**
- 300+ apps and integrations
- Playbook editor (Python-based)
- Mission Control dashboard
- Native bidirectional integration with Splunk SIEM
- Community and commercial apps

**Pros**
- Best-in-class if you already use Splunk
- Python playbooks offer full programming flexibility
- Community of 150k+ users

**Cons**
- Expensive as a standalone product
- Python playbooks require developer skills
- Tied to Splunk ecosystem

**Official Docs**: [https://docs.splunk.com/Documentation/SOAR](https://docs.splunk.com/Documentation/SOAR)

---

### Swimlane

| Attribute | Detail |
|---|---|
| **Vendor** | Swimlane |
| **Type** | Commercial |
| **Best for** | Mid-to-large SOC teams needing low-code SOAR with strong case management |
| **Pricing** | ~$40k–$150k/year |
| **iGaming Relevance** | 3/5 |
| **Integration Difficulty** | Medium |

**Key Features**
- Low-code playbook builder
- Native case management with SLA tracking
- Turbine platform (cloud-native, 2023)
- 400+ integrations
- Dashboards and reporting

**Pros**
- Strong case management — important for regulatory compliance
- Low-code approach reduces engineering overhead
- Active product development

**Cons**
- Smaller integration library than XSOAR
- Less community compared to Splunk/XSOAR
- Pricing is competitive but not budget-friendly

---

### Tines

| Attribute | Detail |
|---|---|
| **Vendor** | Tines |
| **Type** | Commercial (SaaS) |
| **Best for** | Security teams wanting developer-friendly, no-code-first SOAR with transparent pricing |
| **Pricing** | Community (free, 500 runs/day); Team: ~$500/month; Business: custom |
| **iGaming Relevance** | 4/5 |
| **Integration Difficulty** | Easy |

**Key Features**
- No-code workflow builder with full HTTP/API flexibility
- Story-based workflow model (easy to share and audit)
- Webhook triggers
- Built-in credential management
- Case management (Tines Cases)
- Free community tier with generous limits

**Pros**
- Very developer-friendly — JSON-based workflows version-controllable
- Transparent, predictable pricing
- Community tier is genuinely useful for small operators
- Fast onboarding

**Cons**
- Smaller integration library than enterprise SOAR
- Less mature ML/triage features
- Less battle-tested in large enterprise environments

**Official Docs**: [https://www.tines.com/docs](https://www.tines.com/docs)

---

### Shuffle SOAR

| Attribute | Detail |
|---|---|
| **Vendor** | Frikky (Open Source) |
| **Type** | Open Source |
| **Best for** | Teams wanting a free, self-hosted SOAR as an alternative to n8n |
| **Pricing** | Free (self-hosted); Shuffle Cloud: custom |
| **iGaming Relevance** | 3/5 |
| **Integration Difficulty** | Medium |

**Key Features**
- Docker-based self-hosted deployment
- OpenAPI integration builder
- MISP integration for threat intelligence
- TheHive integration for case management
- 700+ apps

**Pros**
- Free and open source
- Purpose-built for security (unlike n8n, which is general-purpose)
- Good TheHive/MISP integration for threat intel workflows

**Cons**
- Smaller community than n8n
- Less polished UI
- Documentation gaps compared to commercial alternatives

**Official Docs**: [https://shuffler.io/docs](https://shuffler.io/docs)

---

### TheHive + Cortex

| Attribute | Detail |
|---|---|
| **Vendor** | TheHive Project (Open Source) |
| **Type** | Open Source |
| **Best for** | SOC teams wanting free case management (TheHive) + automated analysis (Cortex) |
| **Pricing** | Free (self-hosted); TheHive5 commercial tier available |
| **iGaming Relevance** | 4/5 |
| **Integration Difficulty** | Medium |

**Key Features**
- **TheHive**: Incident case management with playbook templates, task assignment, SLA tracking
- **Cortex**: Analysis and response engine — 300+ analyzers (VirusTotal, MISP, Shodan, abuse.ch)
- **MISP** integration for IOC sharing
- REST API for SOAR integration
- Multi-tenancy support
- Audit log for compliance

**Pros**
- Best free case management for security operations
- Cortex analyzers eliminate manual threat investigation steps
- Strong MISP integration for threat intelligence
- Active community

**Cons**
- Requires significant operational effort to maintain
- TheHive5 commercial licence required for enterprise features
- No built-in SOAR playbook runner (use with Shuffle or n8n)

**Official Docs**: [https://docs.strangebee.com/](https://docs.strangebee.com/)

---

## 5. IDS/IPS

### Suricata

| Attribute | Detail |
|---|---|
| **Vendor** | OISF (Open Information Security Foundation) |
| **Type** | Open Source |
| **Best for** | High-performance network IDS/IPS; can run inline as IPS |
| **Pricing** | Free |
| **iGaming Relevance** | 4/5 |
| **Integration Difficulty** | Medium |

**Key Features**
- Multi-threaded packet processing (10+ Gbps on commodity hardware)
- Emerging Threats (ET) rule sets
- Protocol detection and file extraction
- Lua scripting for custom detections
- EVE JSON log output (Elasticsearch/SIEM-ready)
- AF_PACKET, DPDK, PF_RING capture modes

**Pros**
- Industry-standard open source IDS/IPS
- Excellent performance for high-traffic iGaming platforms
- EVE JSON output integrates directly with ELK stack
- Active development and community

**Cons**
- Rule tuning requires expertise
- IPS mode needs careful testing to avoid blocking legitimate traffic
- No web UI (use with Kibana or Scirius)

**Official Docs**: [https://suricata.io/documentation/](https://suricata.io/documentation/)

---

### Snort 3

| Attribute | Detail |
|---|---|
| **Vendor** | Cisco (Open Source) |
| **Type** | Open Source |
| **Best for** | Legacy IDS/IPS deployments; teams familiar with Snort rules |
| **Pricing** | Free; Snort Subscriber Rule Set: ~$30/year |
| **iGaming Relevance** | 3/5 |
| **Integration Difficulty** | Medium |

**Key Features**
- Signature-based detection (Snort rules — industry standard format)
- Snort 3 is a major rewrite with multi-threading and improved performance
- Talos Intelligence rule sets (Cisco)
- Protocol normalisation
- OpenAppID for application detection

**Pros**
- Most widely known IDS rule format — largest community
- Talos rules are high-quality
- Snort 3 significantly improves performance vs Snort 2

**Cons**
- Suricata has largely surpassed Snort in performance and features
- Snort 3 adoption slower than expected
- Fewer pre-built SIEM integrations

**Official Docs**: [https://www.snort.org/documents](https://www.snort.org/documents)

---

### Zeek (formerly Bro)

| Attribute | Detail |
|---|---|
| **Vendor** | Zeek Project (Open Source) |
| **Type** | Open Source |
| **Best for** | Network traffic analysis and protocol-level forensics |
| **Pricing** | Free |
| **iGaming Relevance** | 3/5 |
| **Integration Difficulty** | Hard |

**Key Features**
- Protocol-level deep inspection (100+ protocols)
- Zeek scripting language for custom analysis
- Connection logs, HTTP logs, DNS logs, TLS logs in structured format
- Passive analysis (IDS, not IPS)
- Complementary to Suricata (different detection approach)

**Pros**
- Unparalleled protocol analysis depth
- Zeek logs are the gold standard for network forensics
- Excellent for post-breach investigation

**Cons**
- Not an IPS — cannot block inline
- Steep learning curve (Zeek scripting language)
- Resource-intensive

**Official Docs**: [https://docs.zeek.org/](https://docs.zeek.org/)

---

### OSSEC

| Attribute | Detail |
|---|---|
| **Vendor** | Open Source (Trend Micro commercial fork: OSSEC+ / Atomic OSSEC) |
| **Type** | Open Source |
| **Best for** | HIDS on older infrastructure; predecessor to Wazuh |
| **Pricing** | Free |
| **iGaming Relevance** | 3/5 |
| **Integration Difficulty** | Medium |

**Key Features**
- Log analysis and correlation
- File integrity monitoring
- Rootkit detection
- Active response (block IPs via iptables)
- Agent-based deployment

**Pros**
- Mature and stable
- Active response can integrate with firewall (similar to Wazuh)
- Lightweight agent

**Cons**
- Wazuh is a more feature-rich fork — prefer Wazuh for new deployments
- Less active community than Wazuh
- Web UI limited

---

### CrowdSec

| Attribute | Detail |
|---|---|
| **Vendor** | CrowdSec (Open Source) |
| **Type** | Open Source / Commercial |
| **Best for** | Collaborative threat intelligence with community-sourced blocklists |
| **Pricing** | Open source: free; Premium blocklists: ~$500–$2k+/month |
| **iGaming Relevance** | 5/5 |
| **Integration Difficulty** | Easy |

**Key Features**
- Community blocklist: IP addresses reported by 50k+ servers globally
- Local behaviour engine (parses logs like fail2ban, but smarter)
- Bouncers: pluggable remediation components (nginx, iptables, Cloudflare, AWS WAF)
- REST API for SOAR integration
- Free access to community intelligence

**Pros**
- Community blocklists are highly effective against known botnets and scanners
- AWS WAF bouncer means blocklists can be pushed directly to WAF
- Extremely relevant for iGaming (credential stuffing, scraping, bonus fraud bots)
- Low operational overhead

**Cons**
- Premium threat intelligence feeds required for best coverage (paid)
- False positives possible from community data
- Newer product — less battle-tested than fail2ban at scale

**Official Docs**: [https://doc.crowdsec.net/](https://doc.crowdsec.net/)

---

### fail2ban

| Attribute | Detail |
|---|---|
| **Vendor** | Open Source (Cyril Jaquier) |
| **Type** | Open Source |
| **Best for** | Simple, lightweight brute-force protection on Linux servers |
| **Pricing** | Free |
| **iGaming Relevance** | 4/5 |
| **Integration Difficulty** | Easy |

**Key Features**
- Parses log files for pattern matches (regex-based)
- Bans IPs via iptables/nftables/firewalld
- Configurable ban duration, retry counts, and find windows
- Jails for each service (SSH, nginx, custom application)
- Whitelisting support

**Pros**
- Dead simple to operate
- Extremely low resource overhead
- Works without n8n — operates autonomously
- Already widely deployed in the industry

**Cons**
- Single-server scope — no centralised management
- Regex-only detection — cannot detect statistical anomalies
- Not suitable as a primary threat detection system

**This project uses**: fail2ban as a supplementary auth-layer defence alongside n8n+WAF

**Official Docs**: [https://www.fail2ban.org/wiki/index.php/Main_Page](https://www.fail2ban.org/wiki/index.php/Main_Page)

---

### OpenVAS / Greenbone Vulnerability Manager

| Attribute | Detail |
|---|---|
| **Vendor** | Greenbone Networks (Open Source core) |
| **Type** | Open Source / Commercial |
| **Best for** | Vulnerability scanning and management; not a real-time IDS |
| **Pricing** | Community: free; Greenbone Enterprise: ~$5k–$30k/year |
| **iGaming Relevance** | 3/5 |
| **Integration Difficulty** | Medium |

**Key Features**
- 100k+ vulnerability tests (NVTs)
- Scheduled scanning and delta reports
- Integration with Wazuh, Splunk, Elastic
- CVE/CVSS severity scoring
- Compliance scan profiles (PCI DSS, GDPR)

**Pros**
- Industry standard for free vulnerability management
- PCI DSS compliance scanning built-in
- Regular NVT updates

**Cons**
- Not a real-time detection tool — periodic scanning only
- Resource-intensive scan runs

**Official Docs**: [https://docs.greenbone.net/](https://docs.greenbone.net/)

---

## 6. DDoS Protection

### AWS Shield Advanced

| Attribute | Detail |
|---|---|
| **Vendor** | Amazon Web Services |
| **Type** | Cloud Service |
| **Best for** | AWS-hosted platforms needing L3/L4/L7 DDoS protection with 24/7 DRT access |
| **Pricing** | $3,000/month subscription + data transfer fees |
| **iGaming Relevance** | 5/5 |
| **Integration Difficulty** | Easy |

**Key Features**
- L3, L4, and L7 DDoS protection
- 24/7 AWS DDoS Response Team (DRT) access during active attacks
- Automatic attack detection and mitigation
- Cost protection (credit for scaling costs during attacks)
- Real-time dashboards and CloudWatch integration
- Protected resources: EC2, ELB, CloudFront, Route 53, Global Accelerator

**Pros**
- Essential for any serious AWS-hosted iGaming platform
- DRT access is invaluable during sustained attacks
- Cost protection prevents bill shock from traffic surge
- Integrated with this project's architecture

**Cons**
- $3k/month minimum — significant for small operators
- Best value only when all resources are on AWS

**Official Docs**: [https://docs.aws.amazon.com/waf/latest/developerguide/shield-chapter.html](https://docs.aws.amazon.com/waf/latest/developerguide/shield-chapter.html)

---

### Cloudflare DDoS Protection

| Attribute | Detail |
|---|---|
| **Vendor** | Cloudflare |
| **Type** | Cloud Service |
| **Best for** | Operators routing traffic through Cloudflare CDN |
| **Pricing** | Included in all Cloudflare plans (even free) |
| **iGaming Relevance** | 5/5 |
| **Integration Difficulty** | Easy |

**Key Features**
- Unmetered DDoS protection (no traffic caps)
- Up to 321 Tbps global network capacity
- Magic Transit for network-layer (L3) protection of on-premise infrastructure
- HTTP DDoS Attack Protection ruleset (L7)
- Bot Fight Mode

**Pros**
- Unlimited DDoS mitigation bandwidth at no extra cost
- Fastest time-to-mitigate in the industry
- Magic Transit extends cloud protection to on-prem

**Cons**
- Requires routing all traffic through Cloudflare
- Magic Transit requires BGP peering (complex for small operators)

---

### Radware DefensePro

| Attribute | Detail |
|---|---|
| **Vendor** | Radware |
| **Type** | Commercial (hardware appliance + virtual) |
| **Best for** | On-premise DDoS scrubbing with guaranteed SLA |
| **Pricing** | ~$30k–$200k+ (hardware); ~$10k–$50k/year (virtual) |
| **iGaming Relevance** | 3/5 |
| **Integration Difficulty** | Hard |

**Key Features**
- Behavioural-based DDoS detection
- SSL attack protection
- Network Behavioural Analysis (NBA)
- 24/7 Emergency Response Team (ERT)
- AppWall WAF integration

**Pros**
- On-premise solution — traffic stays in datacenter
- ERT provides human support during attacks
- Proven at carrier-grade deployments

**Cons**
- High cost
- Requires dedicated network expertise
- Less relevant for pure-cloud architectures

---

## 7. Bot Management

### Cloudflare Bot Management

| Attribute | Detail |
|---|---|
| **Vendor** | Cloudflare |
| **Type** | Cloud Service |
| **Best for** | Operators already using Cloudflare WAF |
| **Pricing** | Bot Fight Mode: free/Pro; Bot Management: Enterprise only (~$2k+/month) |
| **iGaming Relevance** | 5/5 |
| **Integration Difficulty** | Easy |

**Key Features**
- ML bot scoring (0–99 scale)
- Verified bot allowlists (Googlebot, payment processors)
- Tor/VPN detection
- Device fingerprinting
- Headless browser detection
- JavaScript challenge and Turnstile CAPTCHA

**Pros**
- Best-in-class bot detection from 20%+ of global traffic visibility
- Turnstile is a privacy-respecting alternative to reCAPTCHA
- Real-time bot analytics

**Cons**
- Full Bot Management feature is Enterprise tier only

---

### DataDome

| Attribute | Detail |
|---|---|
| **Vendor** | DataDome |
| **Type** | Commercial (SaaS) |
| **Best for** | iGaming platforms needing specialised bot protection for scraping, credential stuffing, bonus fraud |
| **Pricing** | ~$2k–$10k+/month depending on traffic |
| **iGaming Relevance** | 5/5 |
| **Integration Difficulty** | Medium |

**Key Features**
- ML models trained specifically on online gaming and betting bot patterns
- Account takeover (ATO) protection
- Carding and payment fraud detection
- SDK for mobile app bot detection
- Device fingerprinting
- Real-time dashboards

**Pros**
- Purpose-built for e-commerce and gaming verticals
- Excellent detection rates for bonus abusers and scrapers
- Multi-platform: web, mobile, API

**Cons**
- Expensive at scale
- Requires all traffic to route through DataDome's infrastructure

---

### HUMAN (PerimeterX)

| Attribute | Detail |
|---|---|
| **Vendor** | HUMAN Security |
| **Type** | Commercial (SaaS) |
| **Best for** | Large platforms needing enterprise bot management and ad fraud protection |
| **Pricing** | Enterprise custom pricing (~$3k–$20k+/month) |
| **iGaming Relevance** | 4/5 |
| **Integration Difficulty** | Medium |

**Key Features**
- Bot Defender for web and API
- Account Defender for ATO protection
- Code Defender (client-side JavaScript protection)
- Human Verification Engine (HVE)

**Pros**
- Large threat intelligence network
- Strong ATO protection relevant for player account security

**Cons**
- Enterprise-only pricing
- Less iGaming-specific than DataDome

---

## 8. Threat Intelligence

### MISP (Malware Information Sharing Platform)

| Attribute | Detail |
|---|---|
| **Vendor** | CIRCL / Open Source |
| **Type** | Open Source |
| **Best for** | Teams wanting to share and consume IOCs with trusted partners and communities |
| **Pricing** | Free |
| **iGaming Relevance** | 4/5 |
| **Integration Difficulty** | Medium |

**Key Features**
- IOC management and sharing (IPs, domains, hashes, malware patterns)
- STIX/TAXII standard support for industry interoperability
- Galaxy framework (threat actors, attack patterns, tools)
- REST API for SOAR integration
- Correlation engine
- 30+ default sharing communities (CIRCL, FS-ISAC)

**Pros**
- Industry standard for threat intelligence sharing
- Integrates with Cortex, TheHive, Shuffle, Wazuh
- Free and self-hosted — important for data sovereignty
- Can ingest commercial feeds (AlienVault OTX, FIRST, etc.)

**Cons**
- Requires dedicated operations to maintain
- Data quality depends on the sharing community
- Steeper learning curve than commercial TI platforms

**Official Docs**: [https://www.misp-project.org/documentation/](https://www.misp-project.org/documentation/)

---

### Recorded Future

| Attribute | Detail |
|---|---|
| **Vendor** | Recorded Future (Mastercard) |
| **Type** | Commercial (SaaS) |
| **Best for** | Enterprises needing rich, curated threat intelligence with analyst tools |
| **Pricing** | ~$25k–$200k+/year |
| **iGaming Relevance** | 4/5 |
| **Integration Difficulty** | Medium |

**Key Features**
- Threat intelligence from dark web, technical sources, and open sources
- Risk scoring for IPs, domains, hashes
- Analyst workbench
- SIEM and SOAR integrations (Splunk, QRadar, XSOAR, n8n via API)
- Threat actor profiling

**Pros**
- Best contextual threat intelligence in the market
- Dark web monitoring relevant for player data breach detection
- API enables SOAR enrichment in n8n

**Cons**
- Very expensive
- Mastercard acquisition raises data sharing concerns for some operators

---

### AlienVault OTX (Open Threat Exchange)

| Attribute | Detail |
|---|---|
| **Vendor** | AT&T Cybersecurity |
| **Type** | Open Source (community-driven) |
| **Best for** | Free community threat intelligence consumption |
| **Pricing** | Free |
| **iGaming Relevance** | 3/5 |
| **Integration Difficulty** | Easy |

**Key Features**
- 20M+ IOCs from 100k+ contributing researchers
- Pulse-based threat intelligence sharing
- REST API (DirectConnect)
- Integrations: Splunk, QRadar, Elastic, MISP

**Pros**
- Free and easy to use
- Large community
- DirectConnect API enables automated enrichment in n8n

**Cons**
- Quality varies — community-submitted data has no SLA
- Cannot replace commercial threat intelligence for serious operations

**Official Docs**: [https://otx.alienvault.com/api](https://otx.alienvault.com/api)

---

### AbuseIPDB

| Attribute | Detail |
|---|---|
| **Vendor** | Marathon Studios (Open community) |
| **Type** | Free Tier / Commercial |
| **Best for** | Rapid IP reputation checking in SOAR enrichment workflows |
| **Pricing** | Basic (free, 1k queries/day); Premium: $50–$150/month |
| **iGaming Relevance** | 5/5 |
| **Integration Difficulty** | Easy |

**Key Features**
- Community-reported IP abuse database (scanning, brute force, DDoS sources)
- Confidence score per IP
- REST API — simple to integrate into n8n enrichment node
- Report submission from your own data

**Pros**
- Extremely easy API integration
- Highly relevant for iGaming (most attacker IPs appear in AbuseIPDB)
- Free tier sufficient for enrichment of P1/P2 events

**Cons**
- Community data — not curated
- Rate limits on free tier

**Official Docs**: [https://www.abuseipdb.com/api](https://www.abuseipdb.com/api)

---

## 9. Summary Comparison Tables

### Cloud WAF Comparison

| Solution | Type | iGaming Score | Integration | Price Range | SOAR API |
|---|---|---|---|---|---|
| AWS WAF v2 | Cloud | 5/5 | Easy | Low ($) | Yes — boto3 |
| Cloudflare WAF | Cloud | 5/5 | Easy | Low–Medium ($–$$) | Yes — REST |
| Akamai App & API Protector | Cloud | 4/5 | Medium | High ($$$) | Yes |
| Imperva Cloud WAF | Cloud | 4/5 | Medium | Medium–High ($$–$$$) | Yes |
| Azure WAF | Cloud | 3/5 | Easy (Azure) | Low ($) | Yes |
| GCP Cloud Armor | Cloud | 3/5 | Easy (GCP) | Low ($) | Yes |
| Fastly NGWAF | Cloud/Hybrid | 4/5 | Medium | Medium–High ($$–$$$) | Yes |
| Sucuri | Cloud | 2/5 | Easy | Very Low ($) | Limited |

### On-Premise WAF Comparison

| Solution | Type | iGaming Score | Integration | Price Range | Open Source |
|---|---|---|---|---|---|
| ModSecurity v3 | On-Prem | 5/5 | Medium | Free | Yes |
| F5 BIG-IP | On-Prem | 4/5 | Hard | Very High ($$$) | No |
| Imperva SecureSphere | On-Prem | 4/5 | Hard | High ($$$) | No |
| FortiWeb | On-Prem | 3/5 | Medium | Medium–High ($$–$$$) | No |
| Citrix ADC WAF | On-Prem | 3/5 | Medium | Medium ($$) | No |
| Barracuda WAF | On-Prem | 3/5 | Easy–Medium | Medium ($$) | No |
| Radware AppWall | On-Prem | 3/5 | Medium | High ($$$) | No |
| A10 Thunder WAF | On-Prem | 3/5 | Medium | Medium–High ($$–$$$) | No |

### SIEM Comparison

| Solution | Type | iGaming Score | Integration | Price Range | Open Source |
|---|---|---|---|---|---|
| Splunk Enterprise Security | Commercial | 5/5 | Hard | Very High ($$$) | No |
| IBM QRadar | Commercial | 4/5 | Hard | High ($$$) | No |
| Microsoft Sentinel | Cloud | 3/5 | Medium | Medium ($$) | No |
| Elastic Security | Open/Commercial | 4/5 | Medium | Low–Medium ($–$$) | Core: Yes |
| Wazuh | Open Source | 4/5 | Medium | Free | Yes |
| Graylog | Open/Commercial | 3/5 | Medium | Free–Medium | Core: Yes |

### SOAR Comparison

| Solution | Type | iGaming Score | Integration | Price Range | Open Source |
|---|---|---|---|---|---|
| n8n | Open/Commercial | 5/5 | Easy–Medium | Free–Low ($) | Yes |
| Palo Alto XSOAR | Commercial | 4/5 | Medium–Hard | Very High ($$$) | No |
| Splunk SOAR | Commercial | 4/5 | Medium | High ($$$) | No |
| Tines | Commercial | 4/5 | Easy | Low–Medium ($–$$) | No |
| Swimlane | Commercial | 3/5 | Medium | High ($$$) | No |
| Shuffle SOAR | Open Source | 3/5 | Medium | Free | Yes |
| TheHive + Cortex | Open Source | 4/5 | Medium | Free | Yes |

### IDS/IPS Comparison

| Solution | Type | iGaming Score | Integration | Price Range | Open Source |
|---|---|---|---|---|---|
| CrowdSec | Open/Commercial | 5/5 | Easy | Free–Medium | Yes |
| Suricata | Open Source | 4/5 | Medium | Free | Yes |
| fail2ban | Open Source | 4/5 | Easy | Free | Yes |
| Wazuh | Open Source | 4/5 | Medium | Free | Yes |
| Snort 3 | Open Source | 3/5 | Medium | Free | Yes |
| Zeek | Open Source | 3/5 | Hard | Free | Yes |
| OSSEC | Open Source | 3/5 | Medium | Free | Yes |
| OpenVAS/Greenbone | Open/Commercial | 3/5 | Medium | Free–High | Core: Yes |

### Recommended Stack for AcmeToCasino

Based on the architecture requirements, regulatory context, and the existing codebase, the recommended tooling stack is:

| Layer | Primary Tool | Rationale |
|---|---|---|
| Cloud WAF | AWS WAF v2 | Already integrated via boto3 in this project |
| Cloud DDoS | AWS Shield Advanced | Essential for AWS-hosted iGaming |
| CDN + Backup WAF | Cloudflare | Failover route + superior DDoS mitigation |
| On-prem WAF | ModSecurity v3 | Already deployed on Nginx; zero licensing cost |
| On-prem Firewall | nftables + ipset | Already integrated via `firewall_manager.py` |
| Supplementary IDS | CrowdSec | Community blocklists directly relevant to iGaming threats |
| Brute-force ban | fail2ban | Already deployed; low overhead |
| SOAR | n8n | Already integrated; self-hosted; developer-friendly |
| Case management | TheHive + Cortex | Free; integrates with n8n; audit trail for compliance |
| SIEM | Wazuh + Elastic | Cost-effective; GDPR/PCI compliance dashboards; Filebeat already in use |
| Threat Intelligence | MISP + AbuseIPDB | Free; easy n8n enrichment via REST API |
| Bot Management | Cloudflare Bot Management | Via Cloudflare CDN integration |
