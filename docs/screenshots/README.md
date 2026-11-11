# Screenshot capture checklist

No real bank statement, account identifier, API key or personal transaction screenshot is committed to this repository.

For a portfolio gallery, run the app against an isolated synthetic/demo database and capture:

- [ ] Home dashboard: balance, KPI cards and real backend-derived charts.
- [ ] Transactions: filters, category badges and import preview.
- [ ] Ask SpendWise AI: local-fallback answer with account/date context.
- [ ] Split & Settle: editable three-person preview before confirmation.
- [ ] Accounts: multiple simulated account cards and account-scoped view.
- [ ] Insights/Reports: category breakdown and generated report metadata.
- [ ] Gold Market unavailable state and, only when a permitted provider succeeds, the attributed historical chart.

Before publishing captures, verify that:

1. data is synthetic or redacted;
2. the browser address bar and filesystem paths do not reveal local details;
3. no secret, PDF password, full account number or raw statement text is visible;
4. charts show actual values from the isolated demo data and are labelled when a provider is unavailable.

This checklist intentionally contains no broken image links. Add images only after the corresponding synthetic-data workflow has been verified.
