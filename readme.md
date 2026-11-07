# AI-Powered Marketplace Financial Reconciliation
## Updated Product Definition, Strategy and Technical Architecture

**Document status:** Updated product direction and MVP scope  
**Target audience:** Marketplace finance and financial operations teams  
**Primary payment integration:** PayPal Complete Payments Platform for Marketplaces and Platforms (multiparty), both `INSTANT` and `DELAYED` disbursement  
**Product name:** Not yet decided

> **Amended 2026-11-06.** This document previously described the marketplace as
> merchant of record — collecting payments centrally and paying sellers through
> the PayPal Payouts API. The target is PayPal's **multiparty** model, where
> PayPal distributes funds to sellers and remits the marketplace's platform fee
> to the marketplace. The decision and its full consequences are in
> `specs/multiparty-direction.md`. Phase 0 established this before any
> implementation existed.

---

# 0. Running it

Python 3.12, managed with [uv](https://docs.astral.sh/uv/). Docker for the local
database.

```bash
docker compose up -d --wait                 # PostgreSQL 16 on 5432
uv sync --frozen                            # the lockfile is authoritative
export DATABASE_URL="postgres://reckonai:devpassword@localhost:5432/reckonai"

uv run alembic upgrade head                 # apply migrations
uv run pytest                               # the test suite
uv run ruff check                           # lint

uv run uvicorn reckonai.app:app --reload    # the API on :8000
uv run python -m reckonai.worker            # the worker, in another shell
```

`GET /health` is liveness and touches no database; `GET /health/ready` runs
`SELECT 1` and answers `503` when the database is unreachable.

The compose credentials above are local-only development values and are not
secrets. Real credentials never enter this repository: see §12 "Render" for how
`DATABASE_URL` reaches a deployed service, and `.env.example` for the PayPal
sandbox variables.

Deployed at **https://reckonai-api.onrender.com** — see §12 "Render" for the
topology. Provisioning is declared in `render.yaml`, and the phase documentation
lives in `specs/`. Start with `specs/mission.md`.

---

# 1. Executive Summary

Marketplaces operate complex financial systems involving customers, independent sellers, payment service providers and internal financial records.

A single customer payment can create multiple financial obligations: seller entitlements, marketplace commissions, payment processing fees, refunds, adjustments and the eventual disbursement of funds to sellers.

Under PayPal's multiparty model the provider performs the money movement. It routes each seller's share to that seller — **immediately at capture under `INSTANT` disbursement, or after holding it until the marketplace releases it under `DELAYED`** — **remits the marketplace's platform fee to the marketplace**, and executes refunds. Under `DELAYED`, PayPal releases held funds automatically after 28 days if the marketplace has not instructed their release.

The platform fee is declared per transaction but **does not arrive per transaction**. PayPal settles partner fees once daily, consolidating a day's fees into a single next-day payment into a bank account rather than a PayPal balance. So the marketplace's commission revenue must be reconciled **many-to-one against a lagging aggregate** — a reconciliation shape that does not exist anywhere else in this product. The seller sees the same fee on their side as "Partner commissions", deducted from their funds.

**One thing here is unverified and must not be designed around:** whether the platform fee is taken at capture or at release under `DELAYED` disbursement. PayPal documents neither. Whether the consolidated settlement is visible through any API is also open. Both are tracked in `specs/multiparty-direction.md` §8.

The marketplace nonetheless maintains its own orders, commission rules and seller ledger. Ensuring those records agree with PayPal's across the complete transaction lifecycle is the problem this product solves. **Our application never moves money**; it verifies the financial outcomes PayPal produced against what the marketplace's own rules say should have happened.

The proposed solution is an AI-powered financial reconciliation agent designed specifically for marketplace operators.

It will connect to a marketplace's backend and PayPal, retrieve financial records, calculate expected seller entitlements, identify discrepancies and investigate their underlying causes.

The system will support:

- **Historical reconciliation:** Reconcile previously recorded transactions over a selected period.
- **Event-driven reconciliation:** Process and update reconciliation records as new financial events occur.
- **Multi-currency reconciliation:** Account for original transaction currencies, settlement currencies, exchange rates and seller disbursement currencies. PayPal supplies a real conversion rate and settlement amount but **no per-transaction conversion fee** — see §8.1 and `specs/paypal-fields.md`.
- **AI-powered exception investigation:** Investigate discrepancies, identify probable root causes, present supporting evidence and recommend resolutions.

The product will prioritise financial accuracy, traceability and human oversight. Monetary calculations and reconciliation decisions will be grounded in deterministic rules and verifiable records, while AI will handle investigation, explanation and recommendations.

The MVP will focus on one marketplace connector, PayPal integration and a unified reconciliation engine.

# 2. Target Customer

## Marketplace operators

The primary target customers are online marketplaces that facilitate transactions between buyers and independent sellers, **running on PayPal Complete Payments Platform for Marketplaces and Platforms** — PayPal's multiparty model.

Examples include:
- Multi-vendor e-commerce marketplaces.
- Digital product marketplaces.
- Service marketplaces.
- Platforms connecting independent merchants with customers.

**The integration model is part of the target definition, not an implementation detail.** It determines who holds the money and therefore what reconciliation means. A marketplace that is its own merchant of record — collecting centrally and paying sellers through the Payouts API — has a different problem and is explicitly **not** the target. Both PayPal disbursement modes are in scope:

| Mode | Who holds the seller's funds | What the marketplace owes the seller |
|---|---|---|
| `INSTANT` | nobody; PayPal pays the seller at capture | nothing — already settled |
| `DELAYED` | PayPal, until release or the 28-day automatic disbursement | the release itself |

The initial users within these organisations are:

- Finance operations teams.
- Financial controllers.
- Reconciliation analysts.
- Marketplace operations teams.

## Why marketplaces?

A conventional e-commerce merchant primarily manages transactions involving its own business.

A marketplace has additional financial responsibilities because it facilitates transactions involving multiple independent sellers.

For example, a customer might purchase products from two sellers in a single checkout.

The marketplace must determine:
- How much each seller is entitled to receive.
- How much commission the marketplace retains.
- Which payment processing fees apply.
- How refunds affect seller balances.
- Whether the seller ledger reflects the correct obligations.
- Whether the correct amounts were eventually paid to sellers.

These responsibilities become more complicated when transactions involve multiple currencies, partial refunds, disputes, delayed payment events and aggregated payouts.

# 3. Problem Definition

Marketplace financial reconciliation involves ensuring that financial records across different systems accurately represent what happened during a transaction's lifecycle.

The problem is not simply determining whether a customer payment was successful.

It is establishing whether the financial consequences of that payment have been correctly recorded and allocated.

## 3.1 Fragmented financial records

Relevant information is distributed across multiple systems.

For example:

| System | Records |
|---|---|
| Marketplace backend | Orders, sellers, order items and commission rules |
| Marketplace financial ledger | Seller balances, commissions and adjustments |
| PayPal | Payments, captures, refunds, disputes and available payout information |
| Marketplace payout system | Seller payout batches and payout items |

These records may use different identifiers, data structures, naming conventions and financial statuses.

Connecting them and establishing their relationships can require significant manual investigation.

## 3.2 Seller entitlement discrepancies

A marketplace must calculate what each seller is owed based on its commercial rules, and then check that PayPal's record of what the seller actually received agrees with it.

A discrepancy can occur when:
- Commission calculations are incorrect, or the platform fee PayPal actually took differs from the commission the marketplace's rules specify.
- Seller balances are not updated after refunds.
- Ledger entries are duplicated.
- Transactions are assigned to the wrong seller — the `payee` on the PayPal purchase unit does not match the marketplace's own seller attribution.
- The amount PayPal credited the seller does not agree with the recorded seller obligation.
- **Under `DELAYED` disbursement only:** funds were captured and held but never released, or released for the wrong amount.

The comparison target differs by disbursement mode, and both are in scope:

| Mode | Expected entitlement is compared against |
|---|---|
| `INSTANT` | the seller-level transaction PayPal created at capture |
| `DELAYED` | the captured transaction first, then the release instruction and the actual disbursement |

Entitlement calculation itself is mode-independent — it always runs against the captured transaction. `DELAYED` adds a second stage rather than replacing the first.

## 3.3 Refunds and disputes

Refunds and disputes can change the financial position of a transaction after the original payment has been recorded.

For example:
- A partial refund may require a corresponding reduction in seller entitlement. It also returns part of the platform fee **automatically and pro rata** — observed 2026-11-07: a 30.00 refund of a 100.00 capture returned 3.00 of a 10.00 commission with no instruction sent. So a refund that leaves commission untouched **is** an anomaly worth investigating. Overriding the reversal amount is a separate matter and needs an account enablement.
- A dispute may introduce a financial exposure.
- A chargeback may occur after a seller has already been disbursed — at capture under `INSTANT`, or after release under `DELAYED`. **PayPal places the liability on the seller:** "in connected integration, merchants assume financial liability and handle disputes and chargebacks." Whether the *marketplace* is exposed is governed by its own seller terms, not by PayPal, so the engine must treat any marketplace-side recovery obligation as rule-driven, never as something inferred from a PayPal record.
- A refund may be processed by PayPal but not reflected in the marketplace ledger.

PayPal calls this model a **connected integration**. What happens to the platform fee when a dispute or chargeback occurs is **not documented** and is a Phase 0b question.

The system must understand these events in the context of the original transaction and the marketplace's financial rules.

## 3.4 Asynchronous financial events

Payment and marketplace events do not necessarily occur simultaneously.

A payment may be captured before the marketplace records the corresponding ledger entry. A refund may be processed before the seller balance is updated.

Consequently, an unmatched record is not automatically evidence of a financial error.

The system must distinguish between:
- Genuine discrepancies.
- Delayed events.
- Incomplete transaction lifecycles.
- Missing records.
- Duplicate events.
- Records awaiting additional evidence.

## 3.5 Multi-currency complexity

Marketplaces may operate across countries and process transactions in different currencies.

A customer may pay in EUR, the marketplace may maintain its financial records in USD, and a seller may receive a payout in NGN.

Comparing the raw amounts without considering their currencies and conversion records would produce misleading results.

The reconciliation process must account for:
- Original transaction amount and currency.
- Marketplace financial-record currency.
- Settlement amount and currency.
- Exchange rates.
- Conversion timestamps.
- Seller disbursement amount and currency.

**PayPal does not supply two of these.** It returns a real conversion rate and settlement amount on a cross-currency capture, but **no per-transaction conversion fee**, and **no conversion timestamp** except on reporting records that lag behind the transaction. Each gap becomes a labelled derivation or a stated limitation, never an estimate. Field-level detail: `specs/paypal-fields.md`.

The system must preserve original amounts and currencies rather than replacing them with converted values.

# 4. Product Vision and Proposed Solution

The proposed solution is an AI-powered financial reconciliation and investigation agent for marketplaces.

Its primary responsibility is to identify discrepancies between PayPal transaction records and marketplace seller financial records, investigate their causes and recommend appropriate resolutions.

The product will have three core capabilities.

## 4.1 Marketplace and PayPal data integration

The system connects to:
- One marketplace backend through a configurable API connector.
- PayPal through its available APIs, toolkit capabilities and webhooks.

It retrieves the financial and operational records required for reconciliation.

## 4.2 Deterministic reconciliation engine

The reconciliation engine:
- Calculates expected seller entitlements.
- Matches marketplace records against PayPal records.
- Validates commissions and deductions.
- Accounts for refunds and disputes.
- Compares seller obligations against ledger and payout records.
- Handles currency-aware comparisons.
- Identifies missing, duplicate, delayed and inconsistent records.

All monetary calculations must use deterministic code and appropriate decimal precision, not LLM-generated arithmetic.

## 4.3 AI-powered exception investigation

When the reconciliation engine identifies a discrepancy, the AI agent investigates it.

It should:
1. Retrieve the relevant financial records.
2. Establish relationships between the original transaction and subsequent events.
3. Examine the applicable marketplace rules.
4. Identify the probable root cause.
5. Present the evidence supporting its conclusion.
6. Recommend a resolution.
7. Escalate uncertain cases for human review.

The agent must be able to report insufficient evidence rather than inventing an explanation or forcing an incorrect match.

Financially consequential actions such as issuing refunds, changing seller balances or initiating payouts will require human approval.

# 5. Agreed MVP Scope

The MVP will focus on three major product capabilities.

## 5.1 Historical reconciliation

Historical reconciliation enables marketplace operators to reconcile previously recorded financial transactions over a selected period.

Example workflow:

1. Administrator connects the marketplace API.
2. The application retrieves historical marketplace records.
3. The application retrieves corresponding PayPal transaction records.
4. The reconciliation engine calculates expected seller entitlements.
5. It compares expected values against actual marketplace ledger and payout records.
6. Discrepancies are classified and made available for investigation.
7. The AI agent investigates selected exceptions.

Historical reconciliation may be initiated manually or through a scheduled reconciliation run.

## 5.2 Event-driven reconciliation

Event-driven reconciliation processes new financial events as they occur.

Examples include:
- Payment captured.
- Refund issued.
- Dispute opened or updated.
- Seller ledger entry created.
- Seller payout initiated.
- Seller payout completed or failed.

When an event is received, the system identifies related records and updates the corresponding reconciliation state.

The system must support:
- Webhook signature verification where supported.
- Duplicate event detection.
- Idempotent event processing.
- Retry handling.
- Periodic backfills to recover missed events.
- Reprocessing when previously unavailable records arrive.

Historical and event-driven reconciliation will use the same reconciliation engine and canonical data model.

## 5.3 Multi-currency reconciliation

Multi-currency support is included in the MVP.

The system will distinguish between:

**Transaction currency:** The currency in which the customer originally paid.

**Functional currency:** The currency in which the marketplace maintains its financial records, where applicable.

**Settlement currency:** The currency in which the payment provider settles funds.

**Payout currency:** The currency in which the seller receives their payout.

The reconciliation engine must preserve the original amount and currency alongside any converted amount.

It must account for available exchange-rate and conversion-fee evidence when validating transactions.

For the MVP, the primary demonstration will cover:
- Same-currency payment and disbursement.
- Customer payment in one currency with settlement in another.
- Exchange-rate differences.

**Not conversion-fee differences.** PayPal declares no per-transaction conversion fee, so there is no provider figure to contradict a marketplace's claimed conversion fee. The demonstration injects an incorrect **rate or settlement value**, which is backed by real provider evidence. See §13 Scenario 3.

Different seller and marketplace functional currencies will be supported by the data model, with a focused demonstration if implementation time permits.

The system must not substitute public exchange-rate estimates for actual payment-provider conversion records when those records are available.

# 6. Reconciliation Logic and Financial Rules

The reconciliation engine is the financial foundation of the product.

It should establish the expected financial position using marketplace rules and compare that position against actual records.

## 6.1 Seller entitlement calculation

Consider a marketplace transaction involving two sellers.

A customer purchases:

| Item | Seller | Amount |
|---|---|---:|
| Product A | Seller A | $100 |
| Product B | Seller B | $60 |
| **Total** | | **$160** |

Assume a 10% marketplace commission.

| Financial component | Amount |
|---|---:|
| Customer payment | $160 |
| Seller A entitlement | $90 |
| Seller B entitlement | $54 |
| Marketplace commission | $16 |

The example excludes processor fees, taxes and other adjustments.

The engine should independently calculate expected entitlements using the marketplace's commission rules.

It should then compare the results against **two independent sets of records**:

1. The marketplace's own seller ledger entries.
2. **PayPal's records of what actually happened** — the seller-level transaction for each `payee`, the platform fee PayPal actually took, and under `DELAYED` the release instruction and the resulting disbursement.

The second is what makes the check independent. Under the multiparty model PayPal splits the USD 160 itself: USD 90 to Seller A, USD 54 to Seller B, and the USD 16 commission to the marketplace as a platform fee. So the marketplace's commission is no longer merely whatever it kept — there is a provider figure to compare it against, and a disagreement is evidence rather than an internal inconsistency.

**Note the settlement shape.** Those USD 16 do not arrive with the transaction; they arrive inside the next day's consolidated partner fee settlement, together with every other fee from the same day. Validating commission therefore happens at two levels: per transaction against the platform fee PayPal recorded, and in aggregate against what actually settled.

## 6.2 Partial refund handling

Assume a $40 refund is issued against Seller A's product.

If the marketplace's refund policy allocates the refund proportionally:

- Seller A's entitlement decreases by $36.
- Marketplace commission decreases by $4.
- Seller B's entitlement remains $54.

The final financial position is:

| Component | Amount |
|---|---:|
| Customer payment after refund | $120 |
| Seller A entitlement | $54 |
| Seller B entitlement | $54 |
| Marketplace commission | $12 |

This allocation is illustrative. Actual calculations must follow the marketplace's configured refund and commission policies.

If Seller A has already received the funds — at capture under `INSTANT`, or after release under `DELAYED` — the system should identify the resulting recovery or offset obligation rather than silently changing the historical disbursement record.

Two multiparty specifics apply. **PayPal reverses the commission itself, pro rata, without being asked** — corrected 2026-11-07, having previously been recorded here as a partner-instructed step. So the engine's expected position after a refund must already assume a proportional commission reversal, and a refund where commission did *not* come back is an exception rather than a normal case. Specifying a non-proportional reversal is possible in principle but needs a PayPal account enablement, which returns `PLATFORM_FEE_NOT_ENABLED` without it. And under `DELAYED`, a refund issued while the funds are still held creates no recovery obligation at all — there is nothing to recover, because the seller was never paid.

## 6.3 Reconciliation categories

The MVP should identify:

- Missing payment records.
- Missing seller ledger entries.
- Duplicate financial entries.
- Incorrect commission calculations.
- Incorrect seller entitlements.
- Refunds not reflected in seller balances.
- Disputed transactions with outstanding financial impact.
- Currency conversion discrepancies.
- Delayed or incomplete transaction lifecycles.

Plus the categories the multiparty model introduces:

- **Platform fee mismatch** — the fee PayPal actually took differs from the commission the marketplace's rules specify.
- **Platform fee settlement mismatch** — the daily consolidated settlement does not equal the sum of the fees it should cover. **Deferred past the MVP**: this is settlement-account reconciliation, which `specs/mission.md` §7 excludes, and the partner fee cost detail arrives only as a monthly SFTP report. Listed here so the canonical model leaves room for it.
- **Seller attribution mismatch** — the `payee` on PayPal's purchase unit does not match the marketplace's own seller for that order.
- **Disbursement amount mismatch** (`DELAYED`) — the released amount differs from the recorded obligation.
- **Unreleased held funds** (`DELAYED`) — captured and held beyond the marketplace's expected release window, with the 28-day automatic disbursement approaching.

## 6.4 Financial lifecycle tracking

Transactions should move through meaningful financial states, including:

- Pending.
- Captured.
- Partially refunded.
- Refunded.
- Disputed.
- Failed.
- Delayed.
- Unmatched.
- Under investigation.
- Resolved.

Plus the disbursement states, which differ by mode:

| State | Applies to |
|---|---|
| Disbursed at capture | `INSTANT` |
| Seller funds held by PayPal | `DELAYED` |
| Disbursement requested by the marketplace | `DELAYED` |
| Disbursement completed | `DELAYED` |
| Disbursement failed or still pending | `DELAYED` |

The system must distinguish incomplete transactions from genuine financial discrepancies.

**Held funds are not an unpaid obligation.** Under `DELAYED`, "seller funds held by PayPal" and "disbursement requested" are the expected intermediate states of a healthy transaction, not a marketplace failure to pay. An engine that classifies them as discrepancies turns every delayed transaction into an exception. PayPal also releases held funds **automatically after 28 days**, so the absence of a marketplace instruction does not mean the funds are stuck.

# 7. Marketplace Connector and Data Acquisition

The application will use a connector-based architecture rather than relying on CSV uploads as its primary data acquisition method.

## 7.1 Connector setup experience

Marketplace administrators should be able to configure their marketplace integration through a guided setup interface.

The proposed workflow:

1. Connect marketplace API.
2. Authenticate using supported credentials or OAuth.
3. Discover available endpoints and response schemas.
4. Suggest mappings between marketplace fields and the canonical data model.
5. Allow administrators to review and edit mappings.
6. Validate mappings against sample API records.
7. Configure relationships between entities.
8. Activate historical extraction and supported event subscriptions.

## 7.2 Field mapping

Different marketplaces may use different field names for equivalent concepts.

For example:

| Marketplace field | Canonical field |
|---|---|
| `vendor_id` | `seller_id` |
| `order_ref` | `order_id` |
| `line_item_ref` | `order_item_id` |
| `item_total` | `item_amount` |
| `currency_code` | `currency` |

The application should suggest mappings based on field names, descriptions, sample values and data types.

Administrators should be able to correct mappings before activation.

AI-assisted mapping must be treated as a recommendation. Required fields and relationships must pass deterministic validation.

## 7.3 Relationship mapping

Field mapping alone is insufficient.

The system must establish how records relate across different marketplace endpoints.

For example:
- An order item belongs to an order.
- An order item belongs to a seller.
- A payment is associated with an order.
- A seller ledger entry references an order or transaction.
- A payout item references a seller ledger obligation.

Where direct identifiers are unavailable, the reconciliation engine may use other evidence to establish relationships, while retaining confidence and provenance information.

## 7.4 Hackathon implementation

The hackathon prototype will implement one API-backed simulated multi-vendor marketplace.

The simulated marketplace should expose endpoints for:
- Sellers.
- Orders.
- Order items.
- Commission rules.
- Payments or payment references.
- Refunds.
- Disputes.
- Seller ledger entries.
- Payout batches.
- Payout items.

It should support sample webhook events and deliberately inconsistent records.

This will demonstrate automatic API-based data acquisition without requiring the development of multiple production marketplace connectors.

CSV import may remain available as a fallback, but it will not be the primary integration experience.

# 8. Canonical Financial Data Model

The canonical model provides a consistent representation of financial records regardless of the marketplace or payment provider.

It must preserve source identifiers, original values and relationships to support auditability.


## 8.1 Currency representation

Every monetary amount must have an explicit currency.

The system must never assume that two amounts are comparable simply because their numerical values are similar.

Currency conversion records should preserve:
- Source amount and currency.
- Target amount and currency.
- Exchange rate.
- Conversion timestamp.
- Conversion fee, where available.
- Conversion source or provider.
- Original source record reference.
- **Whether each value came from the provider or was derived by us.**

The last of these is not optional, because PayPal does not supply every field above. A timestamp may be provider-supplied or derived, and a derived value must be flagged as such and never presented as provider evidence. A conversion record whose fee is absent must stay absent — not zero. The money rules are in `specs/tech-stack.md` §2; the field-level evidence is in `specs/paypal-fields.md`.

The system should use appropriate decimal arithmetic and currency precision rules.

## 8.2 Source data preservation

The canonical model should not discard the original marketplace or PayPal records.

The application should retain sufficient source evidence to reconstruct how a reconciliation decision was reached.

# 9. AI Investigation Agent

The AI agent is responsible for investigating exceptions identified by the deterministic reconciliation engine.

It should not replace the financial calculation engine.

## 9.1 Investigation workflow

1. Receive an exception from the reconciliation engine.
2. Retrieve associated order, payment, refund, seller ledger and payout records.
3. Reconstruct the transaction lifecycle.
4. Retrieve the applicable commission and refund rules.
5. Validate the relevant financial calculations.
6. Identify possible causes.
7. Rank explanations according to supporting evidence.
8. Produce an investigation report.
9. Recommend a resolution.
10. Escalate uncertain cases for human review.

## 9.2 Example investigation

Suppose a $20 partial refund is successfully recorded in PayPal, but the seller ledger still reflects the original entitlement.

The agent should identify:

- The original order.
- The original payment and capture.
- The refund record.
- The affected seller.
- The applicable refund allocation rule.
- The expected adjustment.
- The actual seller ledger balance.
- The difference between the expected and recorded amounts.

It could then explain that the seller ledger does not reflect the refund adjustment and recommend creating the appropriate adjustment entry.

The recommendation must be supported by retrieved records and deterministic calculations.

## 9.3 Exception prioritisation

The investigation queue should prioritise exceptions using factors such as:

- Financial impact.
- Number of affected sellers or transactions.
- Age of the exception.
- Relevant settlement or payout deadlines.
- Confidence that the issue represents a genuine discrepancy.

The system should distinguish between confirmed discrepancies and suspected issues requiring further investigation.

# 10. Technical Architecture

The proposed architecture separates data acquisition, financial normalisation, reconciliation and AI investigation.

## 10.1 Architecture components

**Frontend**
- React-based application.
- AG Grid / AG Studio for financial data exploration and dashboard experiences.

**Backend**
- FastAPI application.
- Authentication and authorisation.
- Marketplace connector management.
- PayPal integration.
- Reconciliation orchestration.
- Investigation endpoints.

**Data storage**
- PostgreSQL for canonical financial records, reconciliation results, connector configurations and investigation history.
- Raw source payload references for audit and traceability.

**Event processing**
- Webhook ingestion endpoints.
- Event deduplication and idempotency.
- Background processing for reconciliation jobs.
- Retry and backfill mechanisms.

**AI investigation**
- LLM with tool-calling capabilities.
- Controlled access to financial records and reconciliation tools.
- Evidence retrieval and structured investigation output.

**Hosting**
- Render for application deployment and background workers, subject to the available hackathon infrastructure.

## 10.2 End-to-end data flow

1. Marketplace administrator configures the marketplace connector.
2. The connector discovers and maps available API fields.
3. Historical records are extracted.
4. PayPal transaction records are retrieved.
5. Both sources are normalised into the canonical financial model.
6. The reconciliation engine calculates expected financial positions.
7. Historical reconciliation results are stored.
8. New webhook events trigger event-driven reconciliation.
9. Exceptions are passed to the AI investigation agent.
10. Investigation results are presented through the financial operations dashboard.

# 11. PayPal Integration Strategy

PayPal is the primary payment provider integration for the MVP.

The integration should use the PayPal AI Toolkit where its exposed capabilities are appropriate, alongside supported PayPal APIs and webhooks where necessary.

The capabilities identified in the toolkit research include:
- Transaction retrieval.
- Merchant reporting and insights.
- Dispute and claim retrieval.
- Refund-related functionality.
- Payout batch and item information.
- Webhook events.

The implementation must verify the actual available fields and permissions rather than assume that every financial record is exposed by the toolkit.

The application should use stable identifiers to associate PayPal records with marketplace records wherever possible.

Webhook processing must account for duplicate deliveries and retries.

Where the toolkit does not expose sufficient settlement, balance or FX information, the product should use another supported data source or clearly identify the limitation.

The application must not fabricate missing settlement or conversion information.

## 11.1 What the multiparty model requires

The integration reads as the **platform**, not as the merchant, which changes access as well as content. The endpoints, headers and auth mechanics are locked in `specs/tech-stack.md` §8; their verification status is tracked in `specs/multiparty-direction.md` §8.

Two consequences belong at product level, because they bound what the MVP can do:

- **Capabilities are granted per seller at onboarding**, not as account-level switches, and PayPal's simpler onboarding flow excludes Platform Fee, Delayed Disbursement, Partner Reporting and the Disputes API outright. The onboarding path a marketplace chose therefore determines which parts of this product apply to it.
- **Several of those capabilities are unconfirmed for our own sandbox**, including the ability to read a seller's transactions at all. If that one does not hold, entitlement validation narrows to what the platform can see of its own fees. That is the largest open risk in the plan.

## 11.2 Reading PayPal is not uniform, and the rules are locked elsewhere

Field availability depends on which endpoint a record is read from, the reporting APIs lag behind the resource endpoints, and PayPal returns fields its own contracts do not declare. None of that is inferable from the API reference, so the resulting client rules are locked as technology decisions in **`specs/tech-stack.md` §8**, and the field-by-field evidence behind them is in **`specs/paypal-fields.md`**.

The consequence for this document is only this: the integration must be built against observed behaviour, not against the contract, and any field the contract promises but PayPal does not deliver is recorded as a limitation rather than assumed.

# 12. Sponsor Technology Strategy

Sponsor tools should contribute meaningfully to the solution rather than being included merely to increase the number of integrations.

## AG Grid / AG Studio

Primary purpose:
- Financial transaction exploration.
- Reconciliation result tables.
- Exception investigation interface.
- Seller financial position views.
- Interactive dashboards and relevant agent-driven visualisations.

AG Studio should contribute to the actual financial operations experience.

## APIMatic

Primary purpose:
- API documentation and context for PayPal and marketplace API integration.
- Support the coding workflow and reduce integration errors.

## Render

Primary purpose:
- Application hosting.
- Backend deployment.
- Background processing and scheduled reconciliation jobs.

The topology is declared in `render.yaml`, a Blueprint committed to this
repository rather than configured by hand, so it is version controlled and
reviewable. Three components, applied against `main`:

| Component | Name | Instance |
|---|---|---|
| Web service, FastAPI | `reckonai-api` | Starter, Frankfurt |
| Worker, queue consumer | `reckonai-worker` | Starter, Frankfurt |
| Managed PostgreSQL 16 | `reckonai-db` | Basic-256mb (`0.1c-256mb`), Frankfurt |

The job queue is tables inside that same database, not a fourth service. That
co-location is deliberate: it lets a dedupe marker and the financial write it
guards commit or fail together.

**No database credential exists in this repository.** Render generates the
password when it creates the instance and injects the connection string through
`fromDatabase`, so the value lives only in Render's store and in the running
process's environment. Secrets that do need a value — the PayPal credentials in
Phase 7, the Anthropic key in Phase 12 — are declared `sync: false`, which puts
the key name in version control and the value only in the dashboard.

Two operational details worth knowing before a deploy: migrations run in the web
service's `preDeployCommand`, never from a start command, so a failing migration
blocks the deploy instead of racing across restarting instances; and the
database accepts internal Render traffic only, so debugging against deployed
data goes through Render's dashboard psql rather than a local client.

## Bryntum

Optional.

Could support investigation scheduling or payout-related timelines, but should only be included if it adds genuine value without compromising core development.

# 13. MVP Demonstration Scenarios

The demonstration should tell a coherent financial investigation story.

## Scenario 1: Partial refund not reflected in seller ledger

1. Customer places an order involving two sellers.
2. PayPal captures the payment.
3. Marketplace records seller entitlements.
4. A partial refund is processed.
5. Marketplace ledger fails to record the corresponding adjustment.
6. Reconciliation engine detects the discrepancy.
7. AI investigates and identifies the missing adjustment.
8. Dashboard presents the evidence and recommended resolution.

## Scenario 2: Incorrect seller commission

1. Marketplace records an order.
2. PayPal confirms the payment.
3. Marketplace calculates commission incorrectly.
4. Reconciliation engine independently calculates the expected entitlement.
5. The difference is detected.
6. AI identifies the incorrect commission calculation and explains its financial impact.

## Scenario 3: Currency conversion discrepancy

1. Customer pays in EUR.
2. PayPal records the original transaction.
3. Settlement occurs in USD.
4. The marketplace records an expected settlement using an incorrect conversion **value**.
5. Reconciliation engine compares the recorded conversion evidence against the expected financial position.
6. AI explains the discrepancy.

**Step 4 previously read "an incorrect conversion value or fee". The fee half has been removed** (amended 2026-11-06). PayPal declares no per-transaction conversion fee anywhere, so there would be no provider figure to contradict a claimed conversion fee, and the agent's only honest answer would be `insufficient_evidence`. The value half is fully supported by real provider evidence — a rate at 15 decimal places and a reported settlement amount.

Two constraints govern this scenario, and either one breaks the demo if ignored: compare against PayPal's **reported** settlement amount rather than recomputing it, and keep the rate at full precision. Both are stated as money rules in `specs/tech-stack.md` §2.

All scenarios should use source records and deliberately introduced inconsistencies, not pre-written explanations that the agent simply repeats.

# 14. Evaluation Metrics

The product should be evaluated on financial accuracy and investigation quality.

| Metric | Definition |
|---|---|
| Auto-match rate | Percentage of eligible records matched without manual intervention |
| Discrepancy detection rate | Percentage of deliberately introduced discrepancies identified |
| False-positive rate | Percentage of valid records incorrectly flagged |
| Root-cause accuracy | Percentage of investigated exceptions assigned the correct underlying cause |
| Exception resolution time | Time required to investigate and reach a resolution recommendation |
| Evidence completeness | Percentage of investigation conclusions supported by traceable records |
| Currency calculation accuracy | Accuracy of calculations involving original and converted monetary values |

The test dataset should contain normal transactions and deliberate edge cases, including:
- Duplicate records.
- Missing ledger entries.
- Incorrect commissions.
- Partial refunds.
- Disputes.
- Delayed events.
- Incorrect payouts.
- Currency conversion differences.
- Duplicate webhook deliveries.

# 15. Reliability, Security and Financial Controls

Because the application handles financial information, reliability and traceability must be core design requirements.

The system should implement:

- Secure credential storage.
- Authentication and role-based access.
- Tenant isolation.
- Webhook signature verification where supported.
- Idempotent event processing.
- Duplicate event detection.
- Appropriate retry and backfill mechanisms.
- Deterministic financial calculations.
- Currency-aware comparisons.
- Immutable or auditable reconciliation history.
- Traceable AI investigation evidence.
- Human approval for consequential financial actions.

The system must not automatically modify seller balances, issue refunds or initiate payouts based solely on an AI recommendation.

# 16. Explicitly Deferred Features

To keep the hackathon implementation achievable, the following capabilities are outside the initial MVP:

| Feature | Reason for deferral |
|---|---|
| Bank reconciliation | Adds another integration and financial reconciliation layer |
| Multiple payment service providers | PayPal is sufficient for the initial demonstration |
| Generic integration builder | One configurable marketplace connector is sufficient |
| Multiple production marketplace connectors | Requires substantial integration work |
| Advanced financial workflow automation | Recommendations and human approval are sufficient initially |
| Seller-facing dashboards | Initial users are marketplace finance operators |
| Comprehensive settlement reporting | Depends on availability of suitable PayPal settlement data |

The architecture should allow these capabilities to be introduced later without redesigning the core reconciliation engine.

# 17. Product Differentiation and Positioning

The product should not be positioned as another generic reconciliation dashboard.

Existing payment providers and reconciliation platforms already offer transaction reporting, matching and exception management.

The product's differentiation should centre on understanding marketplace-specific financial relationships.

It should understand:
- What a marketplace owes each seller.
- Which commercial rule determines the entitlement.
- How refunds and disputes affect that obligation.
- Whether the marketplace ledger reflects the correct financial position.
- Whether what PayPal actually paid each seller, and the platform fee it took, agree with those obligations.
- How currency conversion affects the financial outcome.
- Why a discrepancy occurred and what evidence supports the conclusion.

**Proposed positioning:**

An AI-powered financial reconciliation and investigation agent that helps marketplaces identify discrepancies between PayPal transactions and seller financial records, trace their causes across the transaction lifecycle and recommend evidence-based resolutions.

# 18. Long-Term Product Vision

The hackathon MVP establishes the foundation for a broader marketplace financial operations platform.

Potential future capabilities include:
- Additional marketplace integrations.
- Multiple payment provider integrations.
- Bank settlement reconciliation.
- More advanced FX and multi-currency analysis.
- Continuous financial monitoring.
- Automated investigation workflows.
- Controlled financial adjustments.
- More sophisticated seller financial reporting.

These are future opportunities, not commitments for the initial implementation.

---

# 19. Final Product Definition

**Target customer:** Marketplace finance and financial operations teams.

**Core problem:** Discrepancies between payment records and marketplace seller financial obligations are difficult to identify and investigate across fragmented systems.

**Proposed solution:** An AI-powered reconciliation agent that connects marketplace financial records with PayPal, performs historical and event-driven reconciliation, supports multi-currency financial comparisons, detects discrepancies and investigates their underlying causes.

**Core capabilities:**
1. Marketplace and PayPal data integration.
2. Historical reconciliation.
3. Event-driven reconciliation.
4. Multi-currency reconciliation.
5. Deterministic seller entitlement and payout validation.
6. AI-powered exception investigation and evidence-based recommendations.

**Primary outcome:** More accurate seller financial records, faster discrepancy investigation and reduced manual reconciliation effort.

**MVP principle:** Solve one difficult marketplace financial reconciliation problem exceptionally well before expanding into a broader financial operations platform.