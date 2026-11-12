# ReckonAI

**AI-powered financial reconciliation and investigation for marketplace operators using PayPal.**

ReckonAI helps marketplace finance teams identify discrepancies between PayPal transaction records and their internal seller financial records, investigate why those discrepancies occurred, and make evidence-backed decisions.

I built ReckonAI to combine deterministic financial reconciliation with AI-assisted investigation. The reconciliation engine identifies and quantifies discrepancies; the AI investigates the available evidence and recommends possible explanations. A human remains responsible for approving or rejecting a resolution.

**Live Demo:** [Try ReckonAI](https://reckonai-api.onrender.com)  
**GitHub Repository:** [Nancy9ice/ReckonAI](https://github.com/Nancy9ice/ReckonAI)

---

## Table of Contents

- [The Problem](#the-problem)
- [How ReckonAI Works](#how-reckonai-works)
- [Core Capabilities](#core-capabilities)
- [Technology and Sponsor Integrations](#technology-and-sponsor-integrations)
- [Try ReckonAI: Judge Testing Guide](#try-reckonai-judge-testing-guide)
- [AI Investigation](#ai-investigation)
- [System Architecture](#system-architecture)
- [Evaluation and Testing](#evaluation-and-testing)
- [Developer Setup](#developer-setup)
- [Current Limitations](#current-limitations)
- [Roadmap](#roadmap)
- [Project Documentation](#project-documentation)
- [License](#license)

---

## The Problem

When a customer pays for an order on a marketplace, that transaction can create several financial obligations:

- The marketplace must account for the seller's entitlement.
- The marketplace may retain a commission.
- PayPal processes the payment and routes the seller's share.
- Refunds, disputes, fees, currency conversions and disbursements can change the final amounts.

These events are represented across different systems. PayPal has its transaction records, while the marketplace has its orders, commission rules, seller ledger and refund records.

Finance teams must establish whether the money recorded by each system agrees with what should have happened.

A discrepancy may be caused by an incorrect commission, a missing refund adjustment, a duplicate ledger entry, a delayed provider record or a currency conversion difference. Identifying the discrepancy is only the beginning; understanding its cause requires tracing the transaction lifecycle across multiple records.

ReckonAI is designed to make that process more systematic, explainable and efficient.

## How ReckonAI Works

ReckonAI connects two financial perspectives:

| Marketplace records | PayPal records |
|---|---|
| Orders and order items | Payments and captures |
| Seller entitlements | Seller-level financial transactions |
| Commission rules and booked commissions | Partner/platform fees |
| Refund records and ledger adjustments | Refunds |
| Internal seller ledger | Disbursements and related transaction records |

The reconciliation process follows four stages:

1. **Ingest:** Collect marketplace financial records and PayPal transaction evidence.
2. **Reconcile:** Calculate expected financial outcomes using deterministic rules and compare them with recorded outcomes.
3. **Investigate:** Classify discrepancies and use AI to examine relevant records, rank probable causes and cite supporting evidence.
4. **Review:** Present findings to a human reviewer, who can approve or reject a proposed resolution.

![ReckonAI Reconciliation Workflow](<images/ReckonAI Reconciliation Workflow.png>)

## Core Capabilities

### 1. Deterministic Financial Reconciliation

ReckonAI calculates expected seller entitlements and commissions using explicit financial rules and decimal arithmetic.

It compares expected amounts with the available marketplace and PayPal records, identifying discrepancies such as:

- Commission mismatches
- Missing marketplace refund adjustments
- Duplicate ledger entries
- Payout or disbursement mismatches
- Currency conversion differences

The reconciliation engine—not the language model—performs monetary calculations and determines the recorded financial differences.

### 2. AI-Assisted Investigation

When a discrepancy is identified, the investigation agent retrieves relevant records and examines the transaction lifecycle.

It returns:

- Ranked probable causes
- Supporting record references
- An explanation of the discrepancy
- A confidence assessment
- An `insufficient_evidence` outcome when the available records do not support a reliable conclusion

The agent is intended to help finance teams understand what happened, not to invent missing financial evidence.

### 3. Exception Review and Prioritisation

ReckonAI provides a central exception queue where reviewers can:

- Inspect discrepancies and their supporting records
- Compare PayPal amounts with marketplace-recorded amounts
- Review AI investigation findings
- Filter, group and search exceptions
- Save table views and export records to CSV
- Approve or reject proposed resolutions with a recorded approver name

Plain-English queue queries can help change the grid's filters and grouping. They do not send financial records or amounts to the language model to determine the underlying reconciliation results.

![Exceptions Queue](<images/Exceptions Queue.png>)


## Technology and Sponsor Integrations

ReckonAI uses PayPal, OpenAI, AG Grid Enterprise and Render as functional parts of its reconciliation workflow. Each integration addresses a different part of the problem: obtaining payment evidence, investigating discrepancies, reviewing exceptions, and running the system reliably.

### 1. PayPal + AI

**PayPal provides the financial evidence. AI helps explain discrepancies in that evidence.**

ReckonAI integrates with PayPal Complete Payments Platform for Marketplaces and Platforms (multiparty), supporting both `INSTANT` and `DELAYED` disbursement flows.

| Technology / Feature | Implementation in ReckonAI | Purpose | Evidence in the application |
|---|---|---|---|
| PayPal Orders API | Creates and retrieves real PayPal Sandbox orders | Provides real provider-side transaction records for reconciliation | Judge scenario creation and PayPal order records |
| PayPal webhooks | Verifies webhook signatures, records events and retrieves the underlying PayPal resource | Receives provider notifications without treating the notification itself as the financial record | Ingestion records and source evidence |
| PayPal transaction normalisation | Converts provider records into ReckonAI's canonical financial model while retaining source payloads | Makes provider and marketplace records comparable | PayPal evidence in the investigation drawer |
| Multiparty disbursement | Handles `INSTANT` and `DELAYED` seller disbursement scenarios | Distinguishes funds paid to sellers from funds still held by PayPal | Clean and held-funds judge scenarios |
| Deterministic reconciliation | Calculates expected seller entitlements and commissions, then compares them with recorded outcomes | Establishes the financial discrepancy without relying on an LLM | Exception amounts, currencies and reconciliation results |
| OpenAI GPT-4.1 investigation agent | Examines relevant records, reconstructs transaction lifecycles and returns ranked probable causes with cited evidence | Reduces the manual effort required to investigate exceptions | Investigation drawer, cited record references and investigation report |
| Evidence-aware abstention | Returns `insufficient_evidence` when the available records do not support a conclusion | Prevents the model from presenting unsupported explanations as facts | Evidence-removed evaluation cases |

**What makes this integration meaningful**

The AI does not calculate financial balances, determine seller entitlements or invent missing PayPal figures. Those responsibilities remain with deterministic application logic. The AI receives relevant evidence for investigation and explains what may have caused a discrepancy.

The workflow is therefore:

1. PayPal and marketplace records are ingested.
2. Deterministic logic calculates expected financial outcomes and identifies exceptions.
3. Only records requiring investigation are sent to the AI agent.
4. The agent returns evidence-backed findings or explicitly abstains.
5. A human reviewer makes the final decision.

**See it:** Create the judge transactions, run reconciliation, open an exception and select **Ask the AI to investigate**.

### 2. AG Grid

**AG Grid Enterprise powers the financial exception-review workspace.**

ReckonAI uses AG Grid as the operational interface through which finance users explore, organise and act on reconciliation findings. It is not simply displaying a list of records: the grid supports the review workflow.

| Technology / Feature | Implementation in ReckonAI | Purpose | Evidence in the application |
|---|---|---|---|
| Enterprise Data Grid | Displays reconciliation exceptions in an interactive review queue | Gives finance users one workspace for investigating discrepancies | Exception Queue screenshot |
| Row grouping | Groups exceptions by relevant dimensions such as seller or exception category | Helps users identify recurring patterns across transactions | Group records in the Exception Queue |
| Filtering and quick search | Filters the queue and searches across records | Helps users narrow large exception sets to the items they need | Queue filters and search |
| Multi-row selection | Allows users to select multiple exceptions | Supports batch investigation rather than opening every finding individually | Select multiple rows and use **Investigate selected** |
| Saved views | Saves and restores grid state | Lets users return to frequently used review configurations | Saved queue views |
| CSV export | Exports queue and decision-log data | Supports downstream analysis and operational reporting | Export actions |
| Grid API integration | Connects the natural-language queue assistant to AG Grid's view-state controls | Lets users describe how they want the queue organised without manually configuring every grid control | Ask the queue to group or filter records |

**What makes this integration meaningful**

AG Grid is central to the exception-review experience. Its grouping, filtering, selection and saved-view capabilities support real financial operations workflows.

The natural-language assistant is deliberately constrained: it changes the grid view, not financial records or reconciliation outcomes. It does not send the underlying financial amounts and records to the model to calculate or alter them.

**See it:** Open the Exception Queue, group or filter findings, save a view, select multiple exceptions and export the results.

### 3. Render

**Render hosts the deployed application and runs the background processes that make reconciliation operational.**

ReckonAI uses a version-controlled Render Blueprint to provision its production infrastructure.

| Technology / Feature | Implementation in ReckonAI | Purpose | Evidence in the application |
|---|---|---|---|
| Render Web Service | Hosts the FastAPI application and serves the dashboard | Makes ReckonAI accessible through a public deployment | Live demo |
| Render Background Worker | Runs queued background jobs, including reconciliation jobs and AI investigations | Keeps longer-running work outside the web request lifecycle | Investigation jobs and their status in the application |
| Render Cron Job | Triggers scheduled, date-windowed reconciliation and recovery runs | Re-evaluates transactions as provider evidence becomes available | Scheduled reconciliation configuration and run history |
| Render Managed PostgreSQL | Stores canonical financial records, source evidence, jobs and reconciliation results | Provides persistent storage for the application and its background processing | Persisted reconciliation history and evidence |
| Render Blueprint | Defines the web service, worker, cron job and database in `render.yaml` | Makes the deployment topology version-controlled and reproducible | [`render.yaml`](render.yaml) |
| Environment configuration | Supplies database connection details and external service credentials through Render environment variables | Keeps secrets out of source control and separates deployment configuration from application code | Production deployment configuration |
| Deployment lifecycle | Runs database migrations and ensures demo data before the web service starts serving traffic | Keeps the deployed schema and judge experience ready for use | Successful deployment and populated demo environment |
| Health checks | Exposes application health endpoints for deployment monitoring | Helps Render detect an unhealthy application process | `/health` endpoint |

**What makes this integration meaningful**

Render is not used only to publish a frontend. It runs the distinct application processes needed by ReckonAI:

- The web service handles application requests.
- The worker consumes background jobs, including investigations.
- The scheduled job triggers reconciliation over date windows.
- PostgreSQL stores the financial records and the job queue.

The queue is implemented in PostgreSQL rather than a separate message-broker service. This allows event-deduplication markers and the financial writes they protect to participate in the same database transaction.

**See it:** Open the live deployment and inspect the version-controlled [`render.yaml`](render.yaml) deployment definition.

### Other Technologies

| Technology | Role |
|---|---|
| Python 3.12 | Backend implementation and financial processing |
| FastAPI | Application API |
| PostgreSQL 16 | Persistent relational storage |
| SQLAlchemy and Alembic | Database access and schema migrations |
| React, TypeScript and Vite | Frontend application |
| OpenAI Python SDK | Integration with the deployed GPT-4.1 investigation model |
| pytest, Ruff and mypy | Automated testing, linting and static type checking |


## Try ReckonAI: Judge Testing Guide

The hosted demo is ready for evaluation. You do not need to install anything, configure PayPal credentials, create an AI provider account or run the project locally. Allow about ten minutes.

**What you are testing.** The PayPal side is real: each order is a genuine PayPal Sandbox order, so no real money moves. The marketplace side is simulated: ReckonAI books the matching marketplace records itself, and for some orders it deliberately gets them wrong so there is something to find. ReckonAI then compares the two sides, as it would for a real marketplace.

**Before you start.**

- The queue is shared. Other judges' orders appear next to yours, so always filter by your own name.
- Each name gets its own set of six orders. Choose something distinctive, because the same name always returns the same set.
- Approving or rejecting records your decision and nothing else. It never refunds, adjusts or pays anything.

### Step 1: Open the demo

Open the [ReckonAI live demo](https://reckonai-api.onrender.com). The page opens on the exceptions queue.

### Step 2: Create your test transactions

1. Click **Create test transactions** (top right).
2. Keep the name offered or type your own: letters and digits only, up to 12 characters. The panel remembers it for next time.
3. Click **Create my test transactions**.

If the panel says a limit has been reached, the demo has used its allowance of test sets. Ask the organiser, or use the seeded orders described under "Also try".

### Step 3: Wait for the orders to arrive

The panel places six PayPal Sandbox orders, then waits for each to reach ReckonAI. This takes about a minute: the first call alone is around 40 seconds, because PayPal places the orders one after another.

The table shows each order and its status. PayPal's side arrives by webhook, or is fetched directly if a webhook is slow, and the Status column says which ("fetched directly" or "arrived by webhook"). When all six are in, the panel says **Done. All six are in.**

If you see an error, press **Create my test transactions** again. Your progress is kept and nothing is duplicated.

### Step 4: Run reconciliation

1. Close the panel and click **Run reconciliation** (top right).
2. Wait for "Check complete".
3. In the queue, type `JUDGE-YOURNAME` in the search box (use the name you entered).

You should see three exceptions for your name: the commission mismatch (S2), the refund not booked to the seller (S5) and the same payment booked twice (S6). The other three do not appear in the queue, and that is correct:

- S1 and S3 are clean orders. They are counted under **Checked, no issues**.
- S4 is waiting for PayPal's refund record. Open the **Coverage** tab: it is listed under "On hold: waiting for the other record". This is not an error, and it is not an exception.

If the button says nothing is new, an earlier run already covers your orders. If it says a check has just finished, try again in a few seconds.

### Step 5: Investigate

Running a reconciliation finds and classifies the exceptions. It does not investigate them. Investigating is a separate step:

1. Click one of your exceptions to open its detail panel.
2. Press **Ask the AI to investigate**. It takes a few seconds, and the report appears in the same panel.

To investigate several at once, tick your rows and press **Investigate selected**. Avoid **Investigate all**: it includes other judges' rows and uses shared model calls.

In the report, look for:

- **Probable cause**, with the AI's own stated confidence. This is what the AI says about itself, not a measured accuracy.
- **Records behind this finding** and the cited references. Click a reference to open the underlying PayPal or marketplace record.
- Any reference marked "not found in the records". The AI cited something that does not exist, and it should not be relied on.
- **Other possible causes**, when there is more than one.

Also open **What the system found**: it shows both sides ("PayPal settled" against "Marketplace recorded"), what the finding means and what to do about it.

### Step 6: Make a decision

At the bottom of the panel, type your name, add an optional note and press **Approve** or **Reject**. Approve means you agree with the finding and want it fixed. Reject means you disagree, or no action is needed.

The exception moves from **Needs a decision** to **Decided**. The decision is append-only: deciding again adds a new row and the newest counts, and **Decision log CSV** exports the history. The approver is a typed name, not a sign-in.

### Step 7: Watch a finding clear (late refund)

S4 shows how ReckonAI treats missing evidence. The marketplace has recorded a refund, but PayPal's refund record has not arrived, so ReckonAI waits instead of declaring a loss.

1. After your first reconciliation, open **Create test transactions** again. If you reloaded the page, press **Create my test transactions** once more with the same name: nothing is duplicated and the table returns.
2. Press **Deliver PayPal's refund**.
3. Click **Run reconciliation** again.
4. Open **Coverage**. S4 is no longer on hold.

Deliver the refund only after the first reconciliation. If you deliver it before, the waiting state never appears.

### Scenario reference

| Scenario | What it is | Expected outcome | Where to see it |
|---|---|---|---|
| S1 CLEAN | Correct commission and seller entitlement | No finding | Counted under Checked, no issues |
| S2 COMMISSION | The marketplace books a commission of 15.00 where PayPal's fee is 10.00 | Commission differs from the rule | Queue |
| S3 HELD | Delayed disbursement: PayPal holds the seller's funds | No finding. Held funds are not an unpaid obligation | Counted under Checked, no issues |
| S4 LATE-REFUND | The marketplace records a refund before PayPal's record exists | Waiting for PayPal's record, not an exception | Coverage, "On hold" |
| S5 NO-LEDGER | PayPal refunded the buyer, but the seller ledger was never adjusted | Refund not booked to the seller | Queue |
| S6 DUPLICATE | The same sale is credited twice under different references | Same payment booked twice | Queue |

### Also try

- **Seeded orders** (already in the demo, no setup): ORD-0015 is a real currency-conversion discrepancy; ORD-0008 carries a legitimate one-cent rounding difference and is correctly not flagged; ORD-0018 is a payout shortfall; ORD-0013, ORD-0014 and ORD-0016 are an unbooked refund, a commission mismatch and a duplicate credit; ORD-0017 is another late refund.
- **Ask the queue** in plain English, for example "confirmed FX issues" or "group by seller". It only changes the view; no record or amount is sent to the model.
- **Filter, group, save a view or export** the queue as CSV.
- **Past checks** lists every run. Open one to see the queue as that run left it.
- **Evidence**: from any exception, open a record to see its fields and the raw payload.

### If something looks wrong

| You see | What it means |
|---|---|
| The panel shows an error | PayPal's sandbox was slow or briefly unavailable. Press **Create my test transactions** again; progress is kept |
| A status stays "not arrived yet" | The webhook is slow. After about a minute the panel fetches the record directly |
| "Nothing new has arrived" | Your orders are already covered by an earlier run |
| "A check finished a moment ago" | Wait a few seconds and press **Run reconciliation** again |
| No button for the test transactions | The feature is switched off for this deployment |
| An investigation fails | The AI provider was unavailable. The failed attempt is recorded, and you can ask again |

### What this does not show

The marketplace is simulated, and PayPal is the Sandbox. The approver is a typed name with no sign-in. The AI's root-cause accuracy was scored once on one model and is not a rate: see [Evaluation and Testing](#evaluation-and-testing) before quoting a figure. The full list is under [Current Limitations](#current-limitations).

## AI Investigation

The AI component is designed for evidence-based financial investigation.

It does not calculate seller entitlements, commissions, balances or reconciliation differences. Those are handled by deterministic application logic.

Instead, the agent works with the records retrieved by the application to:

1. Understand the exception classification and financial difference.
2. Examine related marketplace and PayPal records.
3. Reconstruct relevant parts of the transaction lifecycle.
4. Rank plausible explanations.
5. Cite the records supporting its conclusions.
6. Abstain when the evidence is insufficient.

This separation is important in financial workflows: a plausible explanation is not the same as a verified financial fact.

The deployed investigation provider uses OpenAI GPT-4.1. An Anthropic provider is also implemented as a selectable option, but it has not been live-tested in the same evaluation.

## System Architecture

ReckonAI uses a Python backend, PostgreSQL persistence, a background worker and a React frontend.

![System Architecture](<images/ReckonAI Reconciliation System Architecture.png>)

| Component | Responsibility |
|---|---|
| PayPal integration | Retrieve and ingest provider-side financial evidence |
| Ingestion and normalisation | Preserve source payloads and map records into the canonical model |
| Reconciliation engine | Calculate expected amounts and classify discrepancies |
| Investigation agent | Examine retrieved evidence and produce cited findings |
| PostgreSQL | Persist financial records, jobs, exceptions and decisions |
| Background worker | Process queued application jobs |
| FastAPI backend | Expose application and integration APIs |
| React frontend | Present the exception queue, investigation results and review workflow |


## Evaluation and Testing

ReckonAI has separate deterministic and AI-assisted evaluation paths.

### Deterministic Reconciliation Evaluation

The deterministic evaluation uses a seeded dataset containing six known faults and twelve clean orders.

It checks that:

- All six seeded faults are detected.
- The twelve clean orders are not falsely flagged.
- The detected financial impacts match the expected amounts.
- The expected evidence is available for the detected findings.

The deterministic evaluation is a gating check: failures cause a non-zero exit.

Run it with:

```bash
uv run python -m reckonai.eval
```

### AI Investigation Evaluation

The AI evaluation uses seven model calls across five fault cases and two evidence-removed negative cases.

The negative cases test whether the agent can return `insufficient_evidence` rather than invent a cause when supporting evidence has been removed.

The recorded evaluation is an initial, small-sample assessment—not a statistically reliable model accuracy rate. In the human review of the five scored fault cases, three explanations were judged clearly correct. The evidence-removed cases also tested the agent's ability to abstain.

AI evaluation results are reported separately and do not determine the deterministic evaluation's exit status.

### Development Checks

The project has also been checked with:

- Automated tests
- Ruff linting
- Strict mypy type checking
- Frontend production build

See the repository's evaluation and validation specifications for the detailed test scope and recorded results.


## Developer Setup

The hosted demo is the recommended way for judges to evaluate ReckonAI. This section is for developers who want to inspect the codebase or run the application locally.

### Prerequisites

- Python 3.12
- `uv`
- Docker and Docker Compose
- Node.js and npm
- PayPal Sandbox credentials
- An AI provider API key for investigation features
- An AG Grid Enterprise licence key for the licensed frontend features

### 1. Clone the Repository

```bash
git clone https://github.com/Nancy9ice/ReckonAI.git
cd ReckonAI
```

### 2. Start PostgreSQL

```bash
docker compose up -d --wait
```

### 3. Install Python Dependencies

```bash
uv sync --frozen
```

### 4. Configure the Database

```bash
export DATABASE_URL="postgres://reckonai:devpassword@localhost:5432/reckonai"
```

Configure the required PayPal and AI provider settings using the environment variables documented in `.env.example`.

### 5. Run Database Migrations and Seed Demo Data

```bash
uv run alembic upgrade head
uv run python -m reckonai.demo_data --ensure
```

### 6. Build the Frontend

```bash
cd frontend
npm ci
npm run build
```

Configure `VITE_AG_GRID_LICENSE_KEY` before building if you want to use the licensed AG Grid Enterprise features.

### 7. Start the Application

From the project root, start the API:

```bash
uv run uvicorn reckonai.app:app
```

In another terminal, start the background worker:

```bash
uv run python -m reckonai.worker
```

## Current Limitations

ReckonAI is a working prototype, not a production-ready financial control system.

Known limitations include:

- The marketplace-side integration is simulated; it is not connected to a production marketplace ERP or ledger.
- The judge scenarios use PayPal Sandbox transactions.
- Webhook validation and ingestion have been exercised, but only a limited real signed Sandbox delivery has been observed. Refund webhook behaviour still needs further verification.
- Dispute-related reconciliation is not implemented end to end.
- Some delayed-disbursement lifecycle states cannot yet be reliably retrieved from the live provider integration.
- An order first encountered after a refund may not contain enough capture-time evidence to reconstruct the original transaction fully.
- The review workflow records a typed approver name; it is not a complete authenticated role-based approval system.
- The AI evaluation is small and does not establish statistical accuracy or superiority over other models.
- Bank settlement-account reconciliation and providers other than PayPal are outside the current scope.

## Roadmap

The next development priorities are:

1. Expand PayPal lifecycle coverage, including refund, dispute and delayed-disbursement evidence.
2. Validate webhook behaviour across more event types and connected merchant scenarios.
3. Improve investigation evaluation with a larger benchmark and repeated model runs.
4. Introduce authenticated reviewer identities and stronger approval controls.
5. Extend marketplace integrations beyond the simulated marketplace.
6. Expand reconciliation coverage for settlement and multi-currency workflows.

## Project Documentation

Further technical details are available in the repository:

- `product_idea.md` — product scope and problem definition.
- `specs/` — product and engineering specifications.
- `specs/paypal-fields.md` — PayPal field mapping and financial data considerations.
- `specs/roadmap.md` — planned development work.

## License

This project is released under the MIT License. See [LICENSE](LICENSE) for details.

---

**ReckonAI — From financial discrepancies to evidence-backed decisions.**
