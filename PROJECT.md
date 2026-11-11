# SpendWise AI — Product Requirements & Engineering Blueprint

**Status:** Specification / proposed portfolio MVP (not yet implemented)  
**Target:** Interview-ready prototype, not a live payments product  
**Core idea:** Understand **why** a UPI payment happened, not only **who** received it.

## 1. Problem and positioning

UPI expense trackers can classify merchants and display monthly/category spending, but person-to-person transfers are ambiguous. A payment to the same friend may be rent, a dinner split, a loan, family support, or a transfer that should not count as consumption spending. Automatically categorizing by payee alone can distort budgets.

**Product thesis:** Capture optional payment purpose at the moment of a simulated person-to-person payment, then preserve that context for transaction classification, shared-expense accounting, and reimbursement matching. Imported historical transactions use a separate review flow.

**Reference products:** Slice's publicly documented spending analytics (automatic categories, monthly patterns, activity centre) are a *product benchmark*, not source code. Splitwise-style ledgers inspire the accounting UX. Do not copy branding, proprietary UI assets, or unlicensed code.

## 2. Competitive and open-source research (verified 2026-11-06)

### Slice official sources
- https://slice.bank.in/ — publicly describes automatic categorization (food, transport, bills, shopping), monthly patterns, and spending breakdowns.
- https://help.slice.bank.in/support/solutions/articles/84000375753-slice-super-upi — describes an activity centre for tracking monthly spending.
- **Official Slice app source:** No verified public official source repository found during this research. Treat the app as proprietary unless Slice publishes code under an explicit license.

### Open-source references (independent of Slice)
1. https://github.com/Neilboss/UPIPulse — MIT; Next.js, SQLite, SMS parsing, dedup, forecasting, people/khata ledger. Study architecture and test organization.
2. https://github.com/Santhoshkrishs/Project-UPI-expense-tracker — MIT; browser-based CSV/PDF bank statement parsing, categorization, Google Sheets sync and dedup. Study import interface.
3. https://github.com/allwin-antony/Expense_tracker — Apache-2.0; Flutter, SMS parser, on-device ML and merchant rules. Study confidence and rule precedence.
4. https://github.com/pritam-ravani/pckt — GPL-3.0; SMS/email ingestion, dedup, AI categorization, encrypted local storage. **Read for ideas; do not copy GPL code into a proprietary/MIT project without satisfying GPL obligations.**

These repositories are *research references*, not dependencies. Independently implement our differentiated workflow and credit any borrowed components according to their licenses. Check upstream license files before using code.

## 3. Target users and outcomes

- Individuals who pay friends/family via UPI and want accurate spending categories.
- Flatmates sharing rent, groceries and utility bills.
- People who advance payments for groups and receive reimbursements later.

**Success criteria (measure in tests, do not invent results):**
- No failed/pending payment counted as settled spending.
- No duplicate expense on repeated success webhook or duplicate CSV import.
- Same recipient can have multiple unrelated categories.
- Explicit category `Housing > Rent` for Rahul's ₹2,500 transfer is respected without creating a reusable payee rule.
- Personal share, unsettled receivables, gross outflow, and net cash movement shown separately.
- Uncertain historical transfers require confirmation rather than speculative auto-classification.

## 4. MVP boundaries

### Must have
1. **Simulated pre-payment flow:** choose recipient, amount, optional purpose/category and tracking type (personal expense, shared expense, transfer, loan); note optional; skip allowed. Prompt primarily for person-to-person transfers, not every merchant payment.
2. **Payment state machine:** `created -> pending -> succeeded | failed | expired`; only confirmed success posts financial transactions. Simulated gateway only; no actual UPI transfers, credentials, or PINs.
3. **Contextual classifier:** explicit user choice > approved saved rule > deterministic evidence > suggestion > review. Category confidence and source displayed. A one-time choice never silently becomes a global payee rule.
4. **Splits:** equal/custom share allocation in integer paise; enforce total shares = original expense; record who owes whom.
5. **Reimbursements:** user-confirmed partial/full matching of incoming payments to outstanding shares; prevent allocating the same incoming amount twice.
6. **Spending dashboard:** monthly totals, category distribution, recipient view, gross paid, personal expense, outstanding reimbursements, and net cash outflow.
7. **CSV import:** canonical example format; validate columns; detect duplicates; mark unclear P2P transfers for review.
8. **Automated reporting:** n8n workflow export, scheduled synthetic/demo data report, deterministic KPI calculation, optional LLM explanation, email preview or test email. Never send real transaction details to LLM by default.
9. **Tests + documentation:** pytest, sample synthetic data, README, 3-minute Loom demo script, interview prep.

### Out of scope for weekend
- Live PhonePe/Slice integration or embedding UI into their apps.
- Actual UPI payments, account aggregation, banking credentials, scraping private transaction data.
- SMS permission collection, multi-bank PDF parsing, authentication/multi-tenancy, production financial advice.
- Complex ML training, fully autonomous category decisions for ambiguous P2P transfers.

## 5. Primary user journeys

### A. Rahul rent (canonical acceptance case)
1. User starts simulated payment: ₹2,500 to Rahul (`rahul@upi`).
2. Person-to-person transfer triggers **optional** purpose selector.
3. User chooses `Housing > Rent`, tracking type `personal_expense`, no note, `remember_rule=false`.
4. Save immutable payment intent snapshot.
5. Simulated gateway returns `succeeded`.
6. Idempotent confirmation creates a debit transaction of **250000 paise**.
7. Dashboard adds ₹2,500 to Housing/Rent and personal expenses.
8. **No recipient rule** is saved; next payment to Rahul may be dinner or a loan.
9. A failed or pending version must add ₹0 to settled expense totals.

### B. Group dinner
1. User pays ₹1,800 for three people; selects `shared_expense` and `Food > Dining`.
2. Split into ₹600 self, ₹600 Rahul, ₹600 Priya.
3. Record personal consumption expense ₹600; receivables ₹1,200; gross bank outflow ₹1,800.
4. Incoming ₹600 from Rahul is suggested as reimbursement and **confirmed by user**.
5. Outstanding receivables become ₹600; net cash outflow becomes ₹1,200.
6. Once Priya pays ₹600, net cash outflow becomes ₹600; personal expense remains ₹600 throughout.
7. If the incoming payment was a gift, user rejects suggested match and original balances remain unchanged.

### C. Own-account transfer
- User moves ₹5,000 between own accounts; `internal_transfer` treatment, **₹0 consumption expense**. Where both sides are imported, pair them to prevent duplicate movement reporting.

### D. Historical CSV transaction
- Import `2026-11-01,rahul@upi,2500,debit,UPI transfer,...`; if no purpose context, classify `needs_review`, not Rent by default.

## 6. Architecture

**Preferred weekend stack:** Python 3.11+, Streamlit, SQLAlchemy, SQLite locally, Pandas, Plotly, pytest. SQLAlchemy models should be portable to PostgreSQL for future deployment. n8n workflow is separately exported as JSON. This avoids requiring a continuously hosted PostgreSQL server for the public demo.

```text
Streamlit UI
  |-- Simulated payment screen
  |-- Historical import + review queue
  |-- Split/reimbursement management
  `-- Analytics dashboard
         |
      Service layer
         |-- PaymentIntentService (status transitions, idempotency)
         |-- ClassificationService (rules, confidence, user override)
         |-- SplitService (integer-paise allocation)
         |-- ReconciliationService (confirmed matching)
         `-- AnalyticsService (deterministic SQL/Python metrics)
                 |
         SQLite / SQLAlchemy (Postgres-ready)
                 |
         n8n workflow -> KPI export -> optional LLM narrative -> demo email
```

**Privacy:** public demo runs only on synthetic data. Never upload private statements to a shared backend or commit secrets. If adding uploads, process in memory or disclose persistence, use per-session isolation, sanitize logs, and avoid exposing raw data to LLMs.

## 7. Data model

Store all money as **integer paise**. IDs are UUIDs; timestamps UTC; all records scoped to `user_id` for future multi-user support.

| Table | Essential fields | Constraints |
|---|---|---|
| `users` | id, display_name | primary key |
| `categories` | id, name, parent_id, treatment | allowed treatment enum |
| `payment_intents` | id, user_id, recipient_name, recipient_upi, amount_paise, category_id, tracking_type, note nullable, remember_rule=false, status, created_at | amount positive; immutable purpose snapshot after submission |
| `transactions` | id, user_id, intent_id nullable, external_ref nullable, source, amount_paise, direction, status, category_id, treatment, occurred_at | unique intent_id when non-null; unique `(user_id, source, external_ref)` for non-null refs |
| `classification_decisions` | id, transaction_id, category_id, treatment, source, confidence nullable, confirmed, created_at | user override wins; preserve audit trail |
| `classification_rules` | id, user_id, matcher_type, matcher_value, category_id, treatment, enabled, created_at | create only on explicit opt-in; scoped and revocable |
| `split_expenses` | id, payer_user_id, source_transaction_id, category_id, total_paise, status | one source transaction per split |
| `split_shares` | id, split_id, participant_id_or_label, share_paise, is_payer, status | shares sum exactly to split total |
| `reimbursement_allocations` | id, split_share_id, incoming_transaction_id, allocated_paise, confirmed_at | cannot exceed either remaining share or incoming transaction unallocated amount |
| `import_batches` | id, user_id, filename, imported_at, row_count, error_count | import audit |
| `transaction_events` | id, transaction_id, event_type, payload_json, created_at | audit, never store secrets |

**Important accounting distinctions:**
- `gross_paid`: total successful outgoing bank payments.
- `personal_consumption_expense`: direct personal purchases plus user's own share of shared purchases; excludes internal transfers, loans, and other non-consumption flows.
- `outstanding_receivable`: confirmed shares owed to user less confirmed reimbursement allocations.
- `net_cash_outflow`: successful debits minus relevant credits over the chosen period, with income separately identified; do not equate this with personal expense.
- Family support/gifts may be user-configurable expense categories, not automatically excluded.
- Loans and repayments should be tracked in separate ledger accounts rather than treated as ordinary consumption.

## 8. Categorization algorithm

```python
def classify(txn, explicit_choice=None, approved_rules=()):
    if explicit_choice is not None:
        return Decision(explicit_choice, source='user', confirmed=True)
    matching = [r for r in approved_rules if r.enabled and r.matches(txn)]
    if matching:
        return Decision(best_rule(matching), source='approved_rule', confirmed=True)
    if txn.is_verified_own_account_transfer:
        return Decision('Internal Transfer', source='deterministic', confirmed=True)
    if txn.is_known_merchant and txn.merchant_category_is_verified:
        return Decision(txn.merchant_category, source='merchant_mapping', confirmed=False)
    suggestion = context_suggestion(txn.note, txn.recipient, txn.amount_paise)
    return Decision(suggestion or 'Uncategorized', source='suggestion', confirmed=False,
                    needs_review=txn.is_person_to_person or suggestion is None)
```

**Never:** treat `same recipient` as proof of `same purpose`; persist one-time decisions as rules; let LLM override explicit user choice; assume an incoming transfer is a reimbursement without confirmation.

## 9. State transitions, reconciliation and correctness

- Payment intent is not a posted transaction.
- `succeeded` events may arrive multiple times: use DB transaction + unique intent/external reference constraints for idempotency.
- Disallow `failed -> succeeded` without a verified authoritative correction event; for demo, model a deterministic state machine.
- Validate split shares in paise: `sum(shares) == total_paise`; deterministic remainder distribution for uneven splits.
- Reimbursement match is **many-to-many through allocations**: one incoming payment may settle several shares; one share may receive several repayments.
- Validate `sum(allocations for share) <= share_paise` and `sum(allocations for incoming txn) <= incoming amount_paise` atomically.
- Prevent matching a payment to itself; disallow unmatched/failed incoming payments.
- Unmatch/reverse operations should be audited, not delete bank transactions.
- CSV dedup: external reference first; if absent, use cautious candidate matching and manual review to avoid dropping distinct same-amount payments.

## 10. UI pages

1. **Overview:** KPI cards, category chart, monthly trend, outstanding balances, classification review count.
2. **Simulate Payment:** payee, amount, optional purpose, tracking type, note, split toggle; simulate success/failure/pending; result receipt.
3. **Transactions:** searchable table, confidence badge, manual category correction, audit explanation.
4. **Shared Expenses:** split builder, participant balances, reimbursement suggestions, confirm/reject matching.
5. **Imports:** synthetic sample CSV and upload, column mapping, validation and dedup report.
6. **Automation:** sample n8n workflow, generated weekly KPI summary, export workflow JSON, report preview.

## 11. Automation workflow (n8n)

`Manual Trigger / Schedule -> Load sanitized KPI JSON -> Validate schema -> Calculate/verify trends -> Optional LLM summary with strict factual instructions -> Validate numbers against KPI payload -> Email preview/test delivery -> Audit status`

- Only aggregate metrics should be sent to an external model; redact names, UPI IDs, transaction descriptions.
- LLM may narrate trends, not calculate authoritative totals or issue financial advice.
- Provide offline deterministic summary fallback when no API key is configured.
- Workflow export is included in GitHub; never include credential values.

## 12. Repository layout

```text
spendwise-ai/
  PROJECT.md
  README.md
  INTERVIEW_PREP.md
  requirements.txt
  .env.example
  .gitignore
  app.py
  src/
    db.py
    models.py
    services/
      payments.py
      classifier.py
      splits.py
      reconciliation.py
      analytics.py
      imports.py
    ui/
      overview.py
      payment.py
      transactions.py
      shared_expenses.py
      imports.py
      automation.py
  data/
    synthetic_transactions.csv
  workflows/
    weekly_spending_report.json
  tests/
    test_payments.py
    test_classifier.py
    test_splits.py
    test_reconciliation.py
    test_imports.py
  assets/
    screenshots/
```

## 13. Acceptance tests (must pass)

- **T01** Rahul ₹2,500 rent, personal expense, no note, no saved rule: ₹2,500 Housing expense, no reusable rule.
- **T02** Next ₹450 to Rahul for dinner: must not auto-classify as rent.
- **T03** Pending/failed ₹2,500 payment: no posted expense.
- **T04** Repeated payment-success callback: exactly one transaction.
- **T05** ₹1,800 split equally among three: self expense ₹600; receivable ₹1,200.
- **T06** Confirm ₹600 Rahul reimbursement: receivable ₹600; personal expense still ₹600.
- **T07** Reject a suggested reimbursement: no balance change.
- **T08** Partial reimbursement and multi-share allocation: no over-allocation.
- **T09** ₹5,000 own-account transfer: ₹0 personal expense.
- **T10** Import same CSV twice: no duplicate externally referenced transactions.
- **T11** Historical P2P with blank note: review required.
- **T12** Uneven split (₹100 across 3 people): exact 10000 paise allocation, no rounding loss.
- **T13** Report generated without LLM API key: deterministic fallback works.
- **T14** Reconciliation reversal: audit trail and balances correct.

## 14. Implementation sequence

**Phase A — Foundation (2–3h):** schema, synthetic data, services, basic Streamlit navigation, payment state machine.  
**Phase B — Core innovation (3–4h):** purpose prompt, classifier, explicit no-rule behavior, review queue.  
**Phase C — Splits (3–4h):** share allocation, incoming matching, balance calculations.  
**Phase D — Analytics and import (2–3h):** dashboard, CSV, dedup, filters.  
**Phase E — Automation (2–3h):** n8n export, report preview, safe optional LLM.  
**Phase F — QA and portfolio (3–5h):** tests, deployment, screenshots, README, Loom, interview prep.

**Estimate:** roughly 15–22h implementation plus integration variability; a narrower 8–12h demonstration is possible by deferring advanced imports and automation. These are planning estimates, not guarantees.

## 15. Interview narrative

**Problem:** Person-to-person UPI payments cannot reliably be categorized from recipient identity alone.  
**Approach:** Optional pre-payment purpose selection + context-aware review + correct settlement accounting.  
**Key design trade-off:** Accurate categorization vs friction during payment. Prompt only for ambiguous P2P transactions and allow skip.  
**Technical highlights:** integer paise, idempotent events, precedence rules, relational ledger, partial reimbursement allocations, data-quality checks, automated KPI reporting.  
**Evaluation:** compare baseline payee-only categorizer vs context-aware flow on labeled synthetic cases; measure category accuracy, review rate, personal-expense error, and prompt burden.  
**Honesty:** Present as a prototype, not an integration or production deployment at PhonePe/Slice. Never claim real customer impact without evidence.

## 16. Deployment and demonstration

- Use public synthetic dataset only.
- Public Streamlit deployment may have ephemeral filesystem; reset demo data on app restart or use isolated in-memory state. Never use one shared writable DB for visitors' private statements.
- Record a 3–4 minute Loom: wrong payee-only categorization -> simulated Rahul rent -> second Rahul dinner -> split dinner -> reimbursement -> corrected analytics -> automation summary.
- Link `Live Demo | GitHub | Loom` only after each actually exists.

## 17. Sources and license notes

- Slice official: https://slice.bank.in/ and https://help.slice.bank.in/support/solutions/articles/84000375753-slice-super-upi
- UPIPulse: https://github.com/Neilboss/UPIPulse (MIT)
- UPI Expense Tracker: https://github.com/Santhoshkrishs/Project-UPI-expense-tracker (MIT)
- Expense_tracker: https://github.com/allwin-antony/Expense_tracker (Apache-2.0)
- Pckt: https://github.com/pritam-ravani/pckt (GPL-3.0)

Research describes documented features; no claim is made that the official Slice app source is available or that competitors definitively lack our proposed workflow.
