# Comprehensive iGaming White-Label Platform Analysis

## 1. Platform Overview

### What This Platform Is

This is a **white-label iGaming platform** that serves as the operational backbone for online casino brands. It is not a game developer -- it does not create games, run RNG engines, or calculate game outcomes. Instead, it is an **orchestration and integration layer** that:

- Launches games from external suppliers
- Manages player sessions and authentication
- Controls the central wallet (balance, bets, wins, refunds)
- Processes real-money deposits and withdrawals via PSPs
- Enforces responsible gambling and compliance rules
- Provides a CMS/frontend for game catalogues per brand

### Multi-Brand / Multi-Tenant

The platform operates as a **multi-brand system**. Key evidence:

- `GameToken` carries `brandId`, `playerId`, `gameId`, and `nodeId`
- The CMS supports game catalogues organized by brand, country, and category
- Game providers are mapped per brand with configurable blocks by market/country
- The frontend supports multiple casino skins on the same infrastructure

### Jurisdictions

The architecture shows multi-jurisdiction awareness:

- Launch flow resolves jurisdiction as part of `determineLaunchDetails()`
- Withdrawal processing validates KYC per jurisdiction
- Responsible gambling features are integrated at the platform level
- Supplier integrations support multi-currency and multi-language
- The ecosystem is oriented toward regulated markets (MGA, UKGC implied by supplier set)

### Revenue Model

Typical white-label platform revenue:

- **Platform fee** per brand (monthly SaaS or revenue share)
- **GGR percentage** from gameplay (the platform takes a cut of gross gaming revenue)
- **Integration fees** for onboarding new suppliers/PSPs
- **Managed services** for compliance, CRM, and operational support

---

## 2. Architecture Analysis

### Runtime Architecture: Monolith with Integration Layer

The platform is a **JVM monolith** deployed as a WAR on Tomcat, not a microservices architecture. Key characteristics:

| Aspect | Detail |
|--------|--------|
| **Language** | Scala 2.12 + Java (mixed codebase) |
| **Build** | Gradle 6.6 |
| **Runtime** | OpenJDK / Tomcat 9 |
| **Servlet API** | `javax.servlet` (traditional servlets) |
| **Web Services** | Apache CXF / JAX-WS for SOAP |
| **Async/Messaging** | Akka + Kafka |
| **ORM/DB Access** | Slick (Scala) + HikariCP connection pool |
| **Database** | PostgreSQL |
| **Serialization** | Json4s, scala-xml |
| **Deployment** | Docker (WAR in Tomcat container) |
| **Local Dev** | `docker-compose-loc.yml` |

This is a **modular monolith** -- separate concerns (launch, wallet, payments, suppliers) are organized into distinct packages but deployed as a single artifact.

### Database Architecture

- **Primary database**: PostgreSQL
- **Connection pooling**: HikariCP
- Separate conceptual schemas for:
  - Player accounts and balances
  - Transaction history (deposits, withdrawals, gameplay)
  - Game catalogue and provider metadata (in CMS database)
  - Payment records and audit trails
  - Session/token state

The CMS uses a separate database (`beast_cms.sql`) with tables for:
- `game_providers` (provider metadata, distributor mapping)
- Game catalogue (launchcode, RTP, categories, brands)
- Brand/country/category associations

### Caching Strategy

- No explicit distributed cache layer was documented (no Redis/Memcached references in the core analysis)
- The CMS likely uses server-side caching for game catalogues
- Token/session state appears to be managed in-memory or database-backed
- This represents a gap in a modern rebuild -- a proper cache layer would be essential

### Message Queue / Event Streaming

- **Kafka** is present in the dependency chain
- **Akka** is used for actor-based concurrency
- Private libraries (`sendto-segment-messages-akka`, `sendto-segment-messages-circe`) indicate event publishing to analytics/segment
- Transaction events are emitted after financial operations (deposits, gameplay)

### API Gateway Pattern

There is no standalone API gateway. Instead:

- The **servlet container itself** acts as the routing layer
- `GameLaunchServlet` handles `/launch/*` and `/launch-json/*`
- `PaymentGateway` handles `/payment/PROVIDER/COMMAND`
- Per-supplier gateways handle `/supplier/SUPPLIER/*`
- The `NamedProvider` pattern resolves the correct handler dynamically

In a modern rebuild, this would be replaced by an actual API gateway (Kong, Envoy, or custom).

### Authentication / Session Management

- **GameToken**: Platform-generated token containing `brandId`, `playerId`, `gameId`, `nodeId`, operational flags, and expiry
- **GameTokenService**: Signs and encodes tokens
- **ONE_OFF_LAUNCH_TOKEN**: Single-use token consumed at game launch
- Token renewal: Some supplier gateways renew the token mid-session via `GameToken.renew(...)`
- Player authentication: `accountsApi.login(...)` followed by player sync/merge
- The token serves as a bridge between platform identity and supplier session identity

---

## 3. Game Supplier Integration

### Integration Model: Game Aggregation Layer (GAL)

The platform implements what the industry calls a **Game Aggregation Layer** -- an abstraction that normalizes communication with diverse game suppliers behind a unified internal interface.

**Key architectural insight**: The CMS registers 139 game studios, but the platform integrates with only 14 distributors/aggregators. Many studios are delivered through aggregators (e.g., NYX delivers 30+ studios, Leander delivers 43+, Microgaming delivers 37+, Relax delivers 29+).

### Supplier Abstraction Layer

Each supplier integration follows a consistent pattern with these components:

```
gameservice/suppliers/<supplier>/
    <Supplier>GameLauncher.scala    -- Launch logic
    <Supplier>Gateway.scala          -- Wallet callback handler
    <Supplier>Settings.scala         -- Configuration
    <Supplier>Utils.scala            -- Helpers
    <Supplier>WalletService.scala    -- SOAP wallet (when needed)
```

The central abstraction is `AccountsBridge.scala`, which provides:

| Method | Purpose |
|--------|---------|
| `login` | Authenticate player for gameplay |
| `getBalance` | Return current playable balance |
| `debit` | Process a bet (wager) |
| `credit` | Process a win (result) |
| `refundTransaction` | Rollback a failed/cancelled round |
| `debitAndCredit` | Atomic bet+win in single call |

### Wallet Integration: Seamless Model

The platform uses the **seamless wallet** model (not transfer wallet):

- The player has ONE balance maintained by the platform
- Suppliers call back to the platform for every bet/win/rollback
- No funds are "transferred" to a supplier-side wallet
- The platform is the single source of truth for balance

### RNG Handling

**No local RNG exists.** This is confirmed across all documents:

- No RNG engine implementation found
- No seed management
- No probability tables
- No hit rate calculations
- No outcome computation

The RNG is entirely on the supplier side. The platform trusts supplier-reported outcomes and only handles the financial accounting.

### Game Launch Flow

```
1. Player clicks game in frontend
2. Frontend sends request to GameLaunchServlet
3. determineSupplier() -- resolves supplier from game code prefix
4. buildLauncher() -- instantiates SupplierGameLauncher via NamedProvider
5. preparePlayer() -- login, sync player, generate GameToken
6. determineLaunchDetails() -- game, mode, language, currency, jurisdiction
7. SupplierGameLauncher -- builds launch URL/payload with token
8. Frontend receives redirect/HTML/JSON to open the game
9. Game loads in iframe/redirect from supplier servers
```

### Round Completion / Settlement Flow

```
1. Supplier receives player bet in-game
2. Supplier calls platform wallet endpoint (wager/bet)
3. Platform validates token, debits balance via AccountsBridge
4. Game round resolves on supplier side (RNG)
5. Supplier calls platform wallet endpoint (result/win)
6. Platform credits balance via AccountsBridge
7. If round fails: supplier calls rollback endpoint
8. Platform refunds via AccountsBridge.refundTransaction()
```

### Integrated Suppliers (14 Distributors)

| Distributor | Protocol | Studios Delivered | Notes |
|-------------|----------|-------------------|-------|
| **NetEnt** | SOAP wallet | 1 (NetEnt) | Direct integration, session-based |
| **Microgaming** | XML/HTTP | 37 studios | `playtype` mapping, HTML5/Flash |
| **NYX** | HTTP/JSON | 30 studios | Major aggregator, simple gateway |
| **Leander** | -- | 43 studios | Largest studio count |
| **Relax** | -- | 29 studios | Modern aggregator |
| **Blueprint** | -- | 4 studios | |
| **Evolution** | -- | 1 (Evolution) | Live casino |
| **IGT** | -- | 2 variants | |
| **Play'n GO** | -- | 1 (Play'n GO) | Direct integration |
| **Red Tiger** | -- | 3 studios | |
| **Stakelogic** | -- | 1 (Stakelogic) | |
| **Greentube** | -- | 1 (Greentube) | |
| **Kambi** | -- | 1 (sportsbook) | Sports betting supplier |
| **1x2** | -- | 4 studios | |

**Total unique game studios in CMS**: ~139

### Protocol Variations

- **NetEnt**: SOAP/WSDL wallet service, proprietary session creation
- **Microgaming**: XML/HTTP wallet, `playtype` field determines operation
- **NYX**: HTTP gateway with simple action-based routing (`wager`, `result`, `rollback`, `ping`)
- Modern suppliers typically use REST/JSON

---

## 4. Payment and Withdrawal Architecture

### Architectural Separation

The platform enforces a strict separation between two financial domains:

1. **Cashier / Payments**: Entry and exit of real money (deposits, withdrawals via PSPs)
2. **Gameplay Wallet**: Bets, wins, and rollbacks during game sessions

These never cross -- the PSP does not update playable balance directly, and game suppliers do not control the cashier.

### Payment Processing Flow (Deposit)

```
1. Player selects amount/method in cashier
2. PaymentProvider.startPaymentProcess() creates local payment record
3. PSP returns redirect URL or hosted checkout
4. Player completes payment on PSP side
5. PSP sends callback to /payment/PROVIDER/COMMAND
6. PaymentGateway resolves provider via NamedProvider
7. provider.postProcessPayment() validates signature and state
8. Payments.completePayment() marks status as SUCCEEDED
9. DepositProcessor.processDeposit() executes:
   - Mark user as verified (if applicable)
   - Process bonus rules
   - Increment cash balance
   - Record DEPOSIT history
   - Emit transactional event
   - Update deposit summary and user flags
```

### Withdrawal Flow

```
1. Player requests withdrawal
2. WithdrawalSystem validates:
   - KYC status
   - Real balance sufficiency
   - Bonus/forfeit rules
   - Withdrawal limits
3. Cash balance is decremented immediately (reserved)
4. WithdrawVO created with initial status
5. Withdrawal enters state machine:
   - PENDING -> REVIEW -> PROCESSING -> completed/returned
6. Backoffice can: ACCEPT, REJECT, RETURN, REVERSE
7. PSP processes payout
8. If PSP fails/returns: WithdrawalSystem.undoWithdrawal() reverses
```

### Withdrawal States

| State | Description |
|-------|-------------|
| `PENDING` | Withdrawal requested, balance reserved |
| `REVIEW` | Manual review required (thresholds, flags) |
| `PROCESSING` | Sent to PSP for payout |
| `RETURNED` | PSP returned/failed, funds back to player |

### KYC Integration with Payments

- Withdrawal validation includes KYC check as a gate
- First deposit can trigger verification marking
- KYC status affects withdrawal approval flow
- Manual review can be triggered by KYC flags

### Multi-Currency Support

- Currency is part of the game launch context
- Suppliers receive currency in launch parameters
- The wallet operates in the player's registered currency
- PSP integrations handle currency at the provider level

### Reconciliation

- The platform is the **central ledger** -- it reconciles:
  - PSP confirmations against internal deposit records
  - Supplier gameplay transactions against internal wallet
- Payment records track full lifecycle with status transitions
- Audit trail is maintained for all financial operations

### Fraud Detection in Payments

- Withdrawal validation includes rule-based checks
- Bonus/forfeit rules prevent abuse
- Balance verification before withdrawal
- Manual review capability for suspicious withdrawals
- Anti-fraud mentioned as a migration target for new services

### Payment Provider: Trustly (Documented Example)

Trustly integration shows the full PSP lifecycle:

- `startPaymentProcess()` initiates deposit
- `postProcessPayment()` handles callbacks: `status`, `success`, `fail`, `withdrawal/{id}`
- `processPaymentReceipt()` interprets events: `credit`, `debit`, `account`, `cancel`, `pending`
- Confirmed `credit` triggers `completePayment()`
- Withdrawal callbacks can trigger `undoWithdrawal()`

---

## 5. Migration Strategy

### Source and Target

- **From**: JVM monolith (Scala 2.12 + Java, Tomcat, WAR deployment, SOAP integrations)
- **To**: Modern polyglot architecture (Python, Go, Node/TypeScript, Kotlin, Cloudflare Workers)

### Recommended Strategy: Strangler Fig Pattern

Full rewrite rejected as too risky. Instead, domain-by-domain extraction.

### Migration Phases

#### Phase 1: Freeze and Observe
- Freeze current contracts
- Map all real endpoints
- Add observability
- Clearly separate: cashier, gameplay wallet, supplier launch

#### Phase 2: Extract Edge Services
- New services for catalogue and new APIs (Python/Node/Go)
- Extract simple callbacks
- Add edge/proxy layer (Cloudflare Workers)
- Keep core on JVM

#### Phase 3: Payment Orchestration
- Move deposit/withdrawal to new orchestrators (Python FastAPI / Go)
- Keep central ledger in monolith
- Introduce async reconciliation

#### Phase 4: Supplier Migration
- Encapsulate supplier integrations
- Migrate supplier by supplier
- Only then consider moving core wallet

### What Migrates First (Low Risk)
- Game catalogue and metadata APIs
- New cashier APIs
- Callback/webhook handlers
- Anti-fraud and analytics services

### What Migrates Last (High Risk)
- Central wallet/ledger
- Legacy servlet-coupled launch flow
- SOAP integrations with old suppliers

### Rollback Strategy
- Strangler pattern inherently supports rollback (old system still runs)
- New services sit alongside, not replacing, until proven
- Traffic can be shifted gradually via routing layer

### Zero-Downtime Requirements
- Edge proxy handles routing between old and new
- Both systems can run in parallel during transition
- Database migration would require careful dual-write or event-sourcing approach

### Key Risks
- Balance divergence between old and new systems
- Breaking compatibility with legacy suppliers
- Duplicate callback processing
- Inconsistency in rollback/refund flows
- Regulatory regression
- Loss of audit trail
- Premature migration of critical components

---

## 6. What Would Be Needed to Build This

### Architecture Components Checklist

- [ ] API Gateway (routing, rate limiting, authentication, geo-routing)
- [ ] Player Account Management (PAM) -- registration, profile, KYC status, preferences
- [ ] Wallet Service -- central ledger, balance management, transaction history
- [ ] Game Aggregation Layer (GAL) -- supplier abstraction, launch, wallet callbacks
- [ ] Session/Token Service -- game token generation, renewal, validation
- [ ] Bonus Engine -- deposit bonuses, wagering requirements, forfeit rules
- [ ] Compliance Engine -- responsible gambling, self-exclusion, session limits, reality checks
- [ ] Payment Gateway -- PSP integration, deposit flow, callback processing
- [ ] Withdrawal Service -- withdrawal requests, review queue, payout processing
- [ ] KYC Service -- identity verification, document processing, status management
- [ ] Responsible Gaming Module -- deposit limits, loss limits, session time limits
- [ ] Backoffice Admin -- operator dashboard, player management, financial operations
- [ ] CMS / Game Catalogue -- game metadata, provider mapping, brand/market configuration
- [ ] CRM / Marketing -- player segmentation, communications, retention
- [ ] Reporting / Analytics -- GGR reports, player activity, regulatory reporting
- [ ] Fraud Detection -- rule engine, velocity checks, pattern detection
- [ ] Event Bus / Streaming -- real-time events for analytics, compliance, triggers
- [ ] Audit Service -- complete transaction trail, regulatory compliance

### Per-Component Detail

#### API Gateway
- **Purpose**: Single entry point for all client requests, authentication, rate limiting, routing
- **Key endpoints**: `/launch/*`, `/payment/*`, `/supplier/*`, `/api/v1/*`
- **Database tables**: `api_keys`, `rate_limits`, `route_config`
- **External integrations**: CDN, WAF, DDoS protection
- **Book chapter**: Chapter 20 (API Gateway)

#### Player Account Management (PAM)
- **Purpose**: Player registration, authentication, profile management, multi-brand identity
- **Key endpoints**: `/register`, `/login`, `/profile`, `/verify`
- **Database tables**: `players`, `player_profiles`, `player_brands`, `player_preferences`, `login_history`
- **External integrations**: Email service, SMS, identity providers
- **Book chapter**: Chapter 1 (Platform Core), Chapter 26 (Global ID)

#### Wallet Service
- **Purpose**: Central financial ledger -- balance, transactions, gameplay accounting
- **Key endpoints**: `login`, `getBalance`, `debit`, `credit`, `refundTransaction`, `debitAndCredit`
- **Database tables**: `accounts`, `transactions`, `transaction_history`, `balance_snapshots`
- **External integrations**: Game suppliers (via callbacks), payment service (for deposits/withdrawals)
- **Book chapter**: Chapter 10 (Casino Game Integration), Chapter 36 (Balance Management)

#### Game Aggregation Layer (GAL)
- **Purpose**: Normalize integration with diverse game suppliers behind a unified interface
- **Key endpoints**: `/launch/*`, `/supplier/<name>/wallet`, `/supplier/<name>/token`
- **Database tables**: `suppliers`, `supplier_games`, `game_launch_codes`, `supplier_config`, `game_rounds`
- **External integrations**: 14+ supplier APIs (SOAP, XML, REST, JSON)
- **Book chapter**: Chapter 10 (Casino Game Integration), Chapter 15 (Game Service)

#### Session / Token Service
- **Purpose**: Generate, validate, renew, and expire game session tokens
- **Key endpoints**: `/token/create`, `/token/validate`, `/token/renew`
- **Database tables**: `game_tokens`, `session_state`, `one_off_tokens`
- **External integrations**: Supplier session APIs
- **Book chapter**: Chapter 10, Chapter 20

#### Bonus Engine
- **Purpose**: Manage deposit bonuses, free spins, wagering requirements, forfeit rules
- **Key endpoints**: `/bonus/award`, `/bonus/check-wagering`, `/bonus/forfeit`
- **Database tables**: `bonuses`, `bonus_rules`, `player_bonuses`, `wagering_progress`, `bonus_transactions`
- **External integrations**: Wallet service, CRM
- **Book chapter**: Chapter 17 (Promotions/Bonuses)

#### Compliance Engine
- **Purpose**: Enforce responsible gambling rules, session limits, reality checks, self-exclusion
- **Key endpoints**: `/rg/check-limits`, `/rg/reality-check`, `/rg/self-exclude`
- **Database tables**: `player_limits`, `self_exclusions`, `reality_check_log`, `session_limits`
- **External integrations**: National exclusion registries (e.g., GAMSTOP), regulator APIs
- **Book chapter**: Chapter 6 (Compliance), Chapter 9 (Responsible Gaming), Chapter 23 (Compliance as Code)

#### Payment Gateway
- **Purpose**: Integrate with PSPs for deposits, handle callbacks, confirm payments
- **Key endpoints**: `/payment/start`, `/payment/PROVIDER/COMMAND`, `/payment/complete`
- **Database tables**: `payments`, `payment_methods`, `payment_providers`, `payment_audit`, `payment_status_history`
- **External integrations**: Trustly, Skrill, Neteller, Visa/Mastercard processors, bank transfers
- **Book chapter**: Chapter 12 (Money Operations), Chapter 36 (Payment Processing)

#### Withdrawal Service
- **Purpose**: Process withdrawal requests with validation, review, and payout
- **Key endpoints**: `/withdraw`, `/withdraw/review`, `/withdraw/accept`, `/withdraw/reject`, `/withdraw/reverse`
- **Database tables**: `withdrawals`, `withdrawal_methods`, `withdrawal_queue`, `withdrawal_audit`
- **External integrations**: PSPs (payout APIs), KYC service, compliance engine
- **Book chapter**: Chapter 12 (Money Operations), Chapter 36

#### KYC Service
- **Purpose**: Identity verification, document upload/review, verification status management
- **Key endpoints**: `/kyc/submit`, `/kyc/status`, `/kyc/verify`
- **Database tables**: `kyc_documents`, `kyc_status`, `kyc_checks`, `verification_history`
- **External integrations**: Onfido, Jumio, GBG, or similar identity verification providers
- **Book chapter**: Chapter 2 (Compliance Reports), Chapter 6

#### Backoffice Admin
- **Purpose**: Operator control panel for managing players, finances, games, compliance
- **Key endpoints**: Full CRUD for all entities, reporting dashboards, manual operations
- **Database tables**: `admin_users`, `admin_roles`, `admin_audit_log`, `admin_actions`
- **External integrations**: All internal services
- **Book chapter**: Chapter 8 (Backoffice)

#### CMS / Game Catalogue
- **Purpose**: Game metadata, provider/distributor mapping, brand-specific catalogues
- **Key endpoints**: `/games/search`, `/games/categories`, `/games/by-brand`
- **Database tables**: `games`, `game_providers`, `game_categories`, `brand_game_blocks`, `game_metadata`
- **External integrations**: Supplier content APIs, CDN for game assets
- **Book chapter**: Chapter 10, Chapter 15

#### CRM / Marketing
- **Purpose**: Player segmentation, email campaigns, retention, lifecycle management
- **Key endpoints**: `/crm/segment`, `/crm/campaign`, `/crm/trigger`
- **Database tables**: `segments`, `campaigns`, `player_tags`, `communications_log`
- **External integrations**: Email providers (ExactTarget/Salesforce), SMS gateways, push notification services
- **Book chapter**: Chapter 37 (Mailer/ExactTarget), Chapter 34 (Analytics)

#### Reporting / Analytics
- **Purpose**: GGR/NGR reporting, player activity, regulatory reports, financial reconciliation
- **Key endpoints**: `/reports/ggr`, `/reports/player-activity`, `/reports/regulatory`
- **Database tables**: `report_snapshots`, `ggr_daily`, `player_activity_summary`, `financial_reconciliation`
- **External integrations**: Data warehouse, BI tools, regulator reporting endpoints
- **Book chapter**: Chapter 34 (Analytics), Chapter 2 (Compliance Reports)

#### Fraud Detection
- **Purpose**: Real-time and batch fraud detection for payments and gameplay
- **Key endpoints**: `/fraud/check`, `/fraud/flag`, `/fraud/review`
- **Database tables**: `fraud_rules`, `fraud_flags`, `fraud_scores`, `fraud_review_queue`
- **External integrations**: Third-party fraud scoring (MaxMind, Sift), IP geolocation
- **Book chapter**: Chapter 25 (Security Controls)

#### Event Bus / Streaming
- **Purpose**: Publish domain events for analytics, compliance triggers, and cross-service communication
- **Database tables**: `event_log`, `event_subscriptions`
- **External integrations**: Kafka (already in use), potentially Segment for analytics
- **Book chapter**: Chapter 18 (Consensus/Messaging), Chapter 28 (CQRS/Event Sourcing)

### Infrastructure Requirements

| Component | Technology Options | Purpose |
|-----------|-------------------|---------|
| **Compute** | Kubernetes (EKS/GKE), EC2, or bare metal | Application hosting |
| **Database** | PostgreSQL (primary), read replicas | Transactional data |
| **Cache** | Redis / Memcached | Session state, game catalogue, rate limiting |
| **Message Queue** | Apache Kafka | Event streaming, async processing |
| **CDN** | Cloudflare / CloudFront | Static assets, game launcher pages, edge caching |
| **Monitoring** | Datadog / Prometheus + Grafana | Metrics, alerting, APM |
| **Logging** | ELK Stack / Datadog Logs | Centralized logging, audit trail |
| **CI/CD** | GitHub Actions / GitLab CI | Build, test, deploy pipelines |
| **Secrets** | HashiCorp Vault / AWS Secrets Manager | API keys, PSP credentials, supplier secrets |
| **WAF** | Cloudflare / AWS WAF | DDoS protection, bot mitigation |
| **Container Registry** | ECR / GCR / private registry | Docker image storage |
| **Object Storage** | S3 / GCS | KYC documents, reports, backups |
| **DNS** | Cloudflare / Route53 | Multi-brand domain management |

### Documentation Needed

#### API Documentation
- [ ] Internal API reference (all endpoints, auth, payloads)
- [ ] Supplier integration API spec (wallet callback contract)
- [ ] Payment provider integration spec
- [ ] Backoffice API documentation

#### Integration Guides
- [ ] Per-supplier onboarding guide (14+ suppliers)
- [ ] PSP integration guide (per provider)
- [ ] KYC provider integration guide
- [ ] Analytics/event integration guide

#### Operational Playbooks
- [ ] Incident response for payment failures
- [ ] Supplier outage handling
- [ ] Balance reconciliation procedures
- [ ] Withdrawal review process
- [ ] Player dispute resolution

#### Compliance Documentation
- [ ] MGA license application technical annex
- [ ] UKGC compliance technical standards
- [ ] Per-jurisdiction responsible gaming requirements
- [ ] AML/KYC policy and procedures
- [ ] Data protection (GDPR) documentation
- [ ] Audit trail specifications

### Team Required

| Role | Headcount | Responsibility |
|------|-----------|----------------|
| **Technical Lead / Architect** | 1 | System design, supplier integration patterns, technical decisions |
| **Backend Engineers (Senior)** | 4-6 | Core services: wallet, GAL, payments, compliance |
| **Backend Engineers (Mid)** | 3-4 | Supplier adapters, APIs, background jobs |
| **Frontend Engineers** | 2-3 | Player-facing UI, cashier, game launcher, CMS |
| **Backoffice Engineers** | 2 | Admin panel, reporting dashboards |
| **DevOps / SRE** | 2 | Infrastructure, CI/CD, monitoring, scaling |
| **QA Engineers** | 2-3 | Integration testing, supplier certification, regression |
| **DBA** | 1 | Database design, performance, migrations |
| **Security Engineer** | 1 | Penetration testing, compliance, audit |
| **Product Manager** | 1 | Roadmap, supplier prioritization, market requirements |
| **Compliance Officer** | 1 | Regulatory requirements, license applications |
| **Project Manager** | 1 | Delivery coordination, supplier onboarding timelines |
| **TOTAL** | **21-26** | |

### Timeline

| Phase | Duration | Deliverables |
|-------|----------|------------|
| **Phase 1: Foundation** | 3-4 months | PAM, Wallet, basic API gateway, database schema, CI/CD, infrastructure |
| **Phase 2: Game Integration** | 3-4 months | GAL framework, first 2-3 supplier integrations, game catalogue, launch flow |
| **Phase 3: Payments** | 2-3 months | Payment gateway, deposit/withdrawal flows, first 2 PSP integrations |
| **Phase 4: Compliance** | 2-3 months | KYC, responsible gaming, audit trail, first jurisdiction certification |
| **Phase 5: Backoffice** | 2-3 months | Admin panel, player management, financial operations, reporting |
| **Phase 6: Scale** | 2-3 months | Additional suppliers (target 10+), additional PSPs, CRM, bonus engine |
| **Phase 7: Go-Live** | 1-2 months | Production hardening, load testing, security audit, license compliance |
| **TOTAL** | **15-22 months** | Full platform with 10+ suppliers, 3+ PSPs, 1-2 jurisdictions |

Post-launch ongoing work:
- Additional supplier integrations: 2-4 weeks each
- Additional PSP integrations: 1-3 weeks each
- New jurisdiction certification: 2-4 months each
- Continuous compliance updates

---

## 7. Mapping to Book Chapters

| Platform Component | Book Chapter(s) |
|-------------------|-----------------|
| Platform Core / PAM | Chapter 1 (Platform Core) |
| Compliance Reports / KYC | Chapter 2 (Compliance Reports) |
| Market Analysis / Jurisdiction Selection | Chapter 3 (Market Analysis) |
| Licensing Strategy | Chapter 4 (Licensing) |
| Legal Framework | Chapter 5 (Legal) |
| Responsible Gaming / Compliance | Chapter 6 (Compliance), Chapter 9 (Responsible Gaming) |
| Infrastructure / Server Architecture | Chapter 7 (Infrastructure) |
| Backoffice Admin | Chapter 8 (Backoffice) |
| Game Integration / GAL / Wallet | Chapter 10 (Casino Game Integration) |
| Poker (if applicable) | Chapter 11 (Online Poker) |
| Deposits / Withdrawals / Money Operations | Chapter 12 (Money Operations) |
| Live Casino Streaming | Chapter 13 (Live Casino) |
| Mobile Platform | Chapter 14 (Mobile) |
| Game Service / Catalogue | Chapter 15 (Game Service) |
| Cryptocurrency Payments | Chapter 16 (Crypto) |
| Bonus Engine / Promotions | Chapter 17 (Promotions) |
| Event Streaming / Messaging | Chapter 18 (Consensus/Messaging) |
| Multi-brand Architecture | Chapter 19 (Multi-brand) |
| API Gateway | Chapter 20 (API Gateway) |
| Caching Strategy | Chapter 21 (Cache Patterns) |
| Containerization / Docker | Chapter 22 (Docker/Containers) |
| Compliance as Code | Chapter 23 (Compliance as Code) |
| DevSecOps / Edge Security | Chapter 24 (DevSecOps) |
| Security Controls / Fraud | Chapter 25 (Security Controls) |
| Global Player ID / Multi-tenant | Chapter 26 (Global ID) |
| Data Residency / Encryption | Chapter 27 (Data Residency) |
| CQRS / Event Sourcing | Chapter 28 (CQRS) |
| Deployment / Release | Chapter 29 (Deployment) |
| FinOps / Cost Management | Chapter 30 (FinOps) |
| Performance Testing / Monitoring | Chapter 31 (Performance) |
| Cashless / Modern Payments | Chapter 32 (Cashless) |
| Hardware Monitoring | Chapter 33 (Hardware Monitoring) |
| Analytics / BI | Chapter 34 (Analytics) |
| Incident Management / Observability | Chapter 35 (Incident Management) |
| Balance Management / Payment Processing | Chapter 36 (Balance/Payments) |
| CRM / Email Marketing | Chapter 37 (Mailer/CRM) |
| Migration / Cloud Strategy | Chapter 38 (Cloud Migration) |
| Incident Response / Security | Chapter 39 (Incident Response) |
| Market Launch Playbook | Chapter 40 (Market Launch) |
| Scaling for Events | Chapter 41 (Event Scaling) |
| War Stories / Lessons Learned | Chapter 42 (War Stories) |
| Future Technology | Chapter 43 (Future Tech) |
| Multi-brand Deployment | Chapter 44 (Deploy All Brands) |
| Infrastructure Automation | Chapter 45 (Ansible/Automation) |
| Regional Platform (e.g., Brazil) | Chapter 46 (Regional Platforms) |

---

## Summary

This analysis covers a mature iGaming white-label platform that has been operating at scale with 14 supplier integrations (covering 139+ game studios), multiple PSPs, and multi-jurisdictional compliance. The platform is architecturally a JVM monolith (Scala + Java on Tomcat) that functions as an orchestration and integration layer -- it does not run games or generate random outcomes, but manages sessions, wallets, payments, and compliance.

Building an equivalent system from scratch would require approximately 21-26 people over 15-22 months for initial launch, with ongoing investment for additional suppliers, payment providers, and jurisdictions. The critical architectural decisions are:

1. **Seamless wallet model** -- one central balance, suppliers call back for every transaction
2. **Supplier abstraction** -- normalized interface hiding protocol differences (SOAP, XML, REST)
3. **Cashier/wallet separation** -- deposits and withdrawals are a completely separate domain from gameplay transactions
4. **Multi-brand/multi-jurisdiction** -- every component is aware of brand, currency, language, and regulatory context
5. **Platform as ledger** -- the platform is the single source of financial truth, not the PSPs or suppliers
