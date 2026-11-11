# SpendWise AI — Interview preparation

## 60-second pitch

"UPI transfers tell us who received money but often not why. A person-to-person payment can be rent, a shared meal, a loan or an internal transfer. I prototyped an optional purpose selector in a simulated payment flow, then built deterministic categorization, split allocation and reimbursement reconciliation. The dashboard distinguishes bank outflow from personal consumption, and the code includes tests for duplicate callbacks and partial settlements."

## Questions you must be able to answer

1. **Why not categorize by payee?** One person can receive unrelated payments. Explicit user choice overrides suggestions; opt-in rules only.
2. **Why integer paise?** Avoid floating-point rounding errors in money; uneven splits allocate the remainder deterministically.
3. **Why separate intent from transaction?** Pending and failed payment attempts must not count as successful expenses.
4. **What is idempotency?** Replaying a success event returns the existing transaction instead of creating a duplicate.
5. **Personal expense vs cash outflow?** If I pay ₹1,800 for three people, my consumption is ₹600 while bank outflow is ₹1,800 until reimbursements arrive.
6. **Why require reimbursement confirmation?** A ₹600 incoming payment from Rahul could be unrelated to the dinner.
7. **How does reconciliation prevent over-allocation?** Validate both remaining share and unused incoming credit before matching.
8. **What is not production-ready?** Session storage, no auth, simulated payment events, demo n8n workflow, limited CSV formats.
9. **How would you scale it?** PostgreSQL with unique external refs, row-level locks/serializable allocation transactions, event audit table, authenticated user scoping.
10. **What would you measure?** Classification accuracy, review rate, spending error vs labeled ground truth, extra seconds added by prompts, reconciliation precision.

## Demo script (3 minutes)

- 0:00–0:30: Problem — person-to-person ambiguity.
- 0:30–1:00: Rahul rent with one-time category; second Rahul dinner.
- 1:00–1:45: ₹1,800 group meal split; ₹600 repayment and remaining balance.
- 1:45–2:15: Dashboard showing personal spend vs cash movement.
- 2:15–2:40: CSV import and uncertain-transfer review.
- 2:40–3:00: Tests, limitations and next steps.

## Honesty

Describe this as an independently developed *prototype* using AI-assisted code generation and hands-on testing, not as an app used by real PhonePe customers. Do not claim measurable financial impact without experiments.
