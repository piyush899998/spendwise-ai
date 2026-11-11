from fastapi.testclient import TestClient


def test_dashboard_profile_income_trend_and_market_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv('SPENDWISE_DATABASE_URL', f'sqlite:///{tmp_path / "api-market.db"}')
    import backend.main as main
    monkeypatch.setattr(main, 'fetch_market_pulse', lambda force_refresh=False, gold_range='1m': {
        'status': 'unavailable', 'gold': {'value_paise': None}, 'education': [{'title': 'Safe fallback'}]
    })
    app = main.app

    client = TestClient(app)
    dashboard = client.get('/api/dashboard?month=2026-11').json()
    assert dashboard['profile']['display_name'] == 'Demo User'
    assert 'income_received' in dashboard['trend'][0]
    assert 'daily_spending' in dashboard

    market = client.get('/api/market/pulse')
    assert market.status_code == 200
    assert market.json()['status'] == 'unavailable'
    assert market.json()['gold']['value_paise'] is None
    assert market.json()['education']


def test_api_health_dashboard_payment_and_custom_split(tmp_path, monkeypatch):
    monkeypatch.setenv('SPENDWISE_DATABASE_URL', f'sqlite:///{tmp_path / "api.db"}')
    from backend.main import app

    client = TestClient(app)
    assert client.get('/api/health').json()['status'] == 'ok'

    payment = client.post('/api/payments', json={
        'recipient': 'Restaurant', 'amount_paise': 180000,
        'category': 'Food / Dining', 'treatment': 'shared',
        'outcome': 'succeeded',
    })
    assert payment.status_code == 200
    transaction_id = payment.json()['transaction']['id']

    split = client.post('/api/splits', json={
        'transaction_id': transaction_id,
        'participants': [
            {'name': 'Me', 'share_paise': 60000, 'is_self': True},
            {'name': 'Rahul', 'share_paise': 70000, 'is_self': False},
            {'name': 'Priya', 'share_paise': 50000, 'is_self': False},
        ],
    })
    assert split.status_code == 200
    split_payload = split.json()['split']
    assert split_payload['participants'][1]['remaining_paise'] == 70000

    dashboard = client.get('/api/dashboard?month=2026-11')
    assert dashboard.status_code == 200
    assert dashboard.json()['metrics']['personal_expense'] == 60000
    assert dashboard.json()['metrics']['gross_paid'] == 180000


def test_api_reimbursement_review_and_reversal(tmp_path, monkeypatch):
    monkeypatch.setenv('SPENDWISE_DATABASE_URL', f'sqlite:///{tmp_path / "api-reimbursements.db"}')
    from backend.main import app

    client = TestClient(app)
    payment = client.post('/api/payments', json={
        'recipient': 'Restaurant', 'amount_paise': 180000,
        'category': 'Food / Dining', 'treatment': 'shared',
    }).json()
    transaction_id = payment['transaction']['id']
    split = client.post('/api/splits', json={
        'transaction_id': transaction_id,
        'participants': [
            {'name': 'Me', 'share_paise': 60000, 'is_self': True},
            {'name': 'Rahul', 'share_paise': 60000, 'is_self': False},
            {'name': 'Priya', 'share_paise': 60000, 'is_self': False},
        ],
    }).json()['split']
    share_id = split['participants'][1]['id']
    credit_id = client.post('/api/incoming', json={
        'sender': 'Rahul', 'amount_paise': 60000, 'reference': 'API-REPAY-001',
    }).json()['transaction']['id']
    allocation = client.post('/api/reimbursements', json={
        'split_id': split['id'], 'share_id': share_id,
        'credit_id': credit_id, 'amount_paise': 60000,
    })
    assert allocation.status_code == 200
    allocation_id = allocation.json()['allocation']['id']

    assert client.get('/api/dashboard?month=2026-11').json()['metrics']['outstanding'] == 60000
    reversed_result = client.post(f'/api/reimbursements/{allocation_id}/reverse')
    assert reversed_result.status_code == 200
    assert client.get('/api/dashboard?month=2026-11').json()['metrics']['outstanding'] == 120000


def test_api_rejects_invalid_payment_without_posting(tmp_path, monkeypatch):
    monkeypatch.setenv('SPENDWISE_DATABASE_URL', f'sqlite:///{tmp_path / "api-invalid.db"}')
    from backend.main import app

    client = TestClient(app)
    response = client.post('/api/payments', json={
        'recipient': 'Rahul', 'amount_paise': 250000,
        'category': 'Housing / Rent', 'treatment': 'transfer',
        'outcome': 'failed',
    })
    assert response.status_code == 200
    assert response.json()['transaction'] is None
    assert client.get('/api/dashboard?month=2026-11').json()['metrics']['gross_paid'] == 0


def test_api_phase_four_accounts_income_transfers_recurring_budgets_and_goals(tmp_path, monkeypatch):
    monkeypatch.setenv('SPENDWISE_DATABASE_URL', f'sqlite:///{tmp_path / "api-phase-four.db"}')
    from backend.main import app

    client = TestClient(app)
    first = client.post('/api/accounts', json={
        'name': 'Everyday', 'bank': 'Demo Bank', 'account_type': 'savings',
        'masked_account_number': '•••• 1001', 'opening_balance_paise': 100000,
    }).json()['account']
    second = client.post('/api/accounts', json={
        'name': 'Reserve', 'bank': 'Demo Bank', 'account_type': 'savings',
        'masked_account_number': '•••• 1002', 'opening_balance_paise': 0,
    }).json()['account']
    income = client.post('/api/transactions', json={
        'account_id': first['id'], 'amount_paise': 200000, 'transaction_type': 'income',
        'category': 'Salary', 'recipient': 'Employer', 'reference': 'API-SAL-001',
    })
    assert income.status_code == 200
    expense = client.post('/api/transactions', json={
        'account_id': first['id'], 'amount_paise': 30000, 'transaction_type': 'expense',
        'category': 'Food / Dining', 'recipient': 'Cafe', 'reference': 'API-EXP-001',
    })
    assert expense.status_code == 200
    transfer = client.post('/api/transfers', json={
        'from_account_id': first['id'], 'to_account_id': second['id'],
        'amount_paise': 50000, 'reference': 'API-MOVE-001',
    })
    assert transfer.status_code == 200
    balances = {item['name']: item['current_balance_paise'] for item in client.get('/api/accounts').json()['accounts']}
    assert balances['Everyday'] == 220000
    assert balances['Reserve'] == 50000

    schedule = client.post('/api/recurring', json={
        'account_id': first['id'], 'name': 'Salary schedule', 'schedule_type': 'salary',
        'amount_paise': 200000, 'category': 'Salary', 'next_due_date': '2026-12-01',
    }).json()['schedule']
    first_post = client.post(f"/api/recurring/{schedule['id']}/confirm?due_date=2026-12-01").json()['transaction']['id']
    second_post = client.post(f"/api/recurring/{schedule['id']}/confirm?due_date=2026-12-01").json()['transaction']['id']
    assert first_post == second_post
    assert len(client.get('/api/transactions?transaction_type=income').json()['transactions']) == 2

    budget = client.post('/api/budgets', json={
        'month': '2026-11', 'category': 'Food / Dining', 'limit_paise': 25000,
    })
    assert budget.status_code == 200
    assert client.get('/api/budgets?month=2026-11').json()['budgets'][0]['overspent'] is True
    goal = client.post('/api/goals', json={'name': 'Travel', 'target_paise': 1000000}).json()['goal']
    contribution = client.post(f"/api/goals/{goal['id']}/contributions", json={'amount_paise': 50000}).json()['goal']
    assert contribution['saved_paise'] == 50000

    reset = client.post('/api/demo/reset', json={'seed': False})
    assert reset.status_code == 200
    assert client.get('/api/accounts').json()['accounts'] == []


def test_api_phase_41_filters_edits_recurring_and_payment_account(tmp_path, monkeypatch):
    monkeypatch.setenv('SPENDWISE_DATABASE_URL', f'sqlite:///{tmp_path / "api-phase-41.db"}')
    from backend.main import app

    client = TestClient(app)
    seeded = client.post('/api/demo/reset', json={'seed': True}).json()
    account_id = seeded['accounts'][0]['id']
    dashboard = client.get('/api/dashboard?month=2026-11').json()
    assert 'savings_rate_percent' in dashboard['metrics']
    assert 'budget_utilization_percent' in dashboard
    filtered = client.get('/api/transactions?month=2026-11&category=Groceries').json()['transactions']
    assert all(row['category'] == 'Groceries' for row in filtered)
    transaction_id = dashboard['transactions'][0]['id']
    edited = client.patch(f'/api/transactions/{transaction_id}', json={'note': 'reviewed'}).json()['transaction']
    assert edited['note'] == 'reviewed'
    schedule = client.get('/api/recurring').json()['schedules'][0]
    updated = client.patch(f"/api/recurring/{schedule['id']}", json={'is_active': False}).json()['schedule']
    assert updated['is_active'] is False
    payment = client.post('/api/payments', json={
        'recipient': 'Funded payment', 'amount_paise': 100, 'account_id': account_id,
        'outcome': 'pending', 'treatment': 'expense',
    })
    assert payment.status_code == 200
    assert payment.json()['transaction'] is None


def test_api_csv_import_preserves_audit_counts_and_duplicates(tmp_path, monkeypatch):
    monkeypatch.setenv('SPENDWISE_DATABASE_URL', f'sqlite:///{tmp_path / "api-import.db"}')
    from backend.main import app

    client = TestClient(app)
    csv_text = 'date,recipient,amount,direction,reference,note\n2026-11-10,Store,12.50,debit,API-CSV-1,groceries\n2026-11-11,,bad,debit,API-CSV-2,invalid\n'
    first = client.post('/api/imports', json={'filename': 'statement.csv', 'text': csv_text})
    assert first.status_code == 200
    assert first.json()['added'] == 1
    assert first.json()['error_count'] == 1
    second = client.post('/api/imports', json={'filename': 'statement.csv', 'text': csv_text})
    assert second.status_code == 200
    assert second.json()['skipped'] == 1
    assert second.json()['error_count'] == 1


def test_api_full_307_import_is_visible_in_history_and_analytics(tmp_path, monkeypatch):
    monkeypatch.setenv('SPENDWISE_DATABASE_URL', f'sqlite:///{tmp_path / "api-full-307.db"}')
    from backend.main import app

    client = TestClient(app)
    rows = ['date,recipient,amount,direction,reference,note']
    for index in range(307):
        credit = index < 19
        rows.append(f"2026-01-{(index % 28) + 1:02d},{'Payroll' if credit else 'Merchant'},{500 if credit else 100 + index},{'credit' if credit else 'debit'},FULL-API-{index:03d},synthetic")
    csv_text = '\n'.join(rows) + '\n'
    first = client.post('/api/imports', json={'filename': 'full-307.csv', 'text': csv_text}).json()
    second = client.post('/api/imports', json={'filename': 'full-307.csv', 'text': csv_text}).json()

    assert first['added'] == 307
    assert first['skipped'] == 0
    assert first['error_count'] == 0
    assert second['added'] == 0
    assert second['skipped'] == 307
    history = client.get('/api/transactions').json()['transactions']
    assert len(history) == 307
    january = client.get('/api/dashboard?month=2026-01').json()
    assert len(january['transactions']) == 307
    assert january['metrics']['income_received'] == 19 * 50000
    assert january['metrics']['gross_paid'] == sum(100 + index for index in range(19, 307)) * 100


def test_universal_account_and_date_filters_reconcile_metrics_and_historical_balances(tmp_path, monkeypatch):
    monkeypatch.setenv('SPENDWISE_DATABASE_URL', f'sqlite:///{tmp_path / "universal-filters.db"}')
    from backend import main
    from src.engine import create_income, create_expense, create_transfer

    client = TestClient(main.app)
    first = client.post('/api/accounts', json={
        'name': 'Everyday', 'bank': 'Demo Bank', 'account_type': 'savings',
        'masked_account_number': '•••• 1101', 'opening_balance_paise': 100000,
    }).json()['account']
    second = client.post('/api/accounts', json={
        'name': 'Reserve', 'bank': 'Demo Bank', 'account_type': 'savings',
        'masked_account_number': '•••• 1102', 'opening_balance_paise': 200000,
    }).json()['account']
    ledger = main.load_ledger()
    create_income(ledger, first['id'], 10000, 'Salary', 'Employer', reference='FILTER-SAL-1')
    ledger['transactions'][-1]['occurred_at'] = '2026-01-01'
    create_expense(ledger, first['id'], 3000, 'Food / Dining', 'Cafe', reference='FILTER-EXP-1')
    ledger['transactions'][-1]['occurred_at'] = '2026-01-31'
    create_expense(ledger, first['id'], 4000, 'Transport', 'Taxi', reference='FILTER-EXP-2')
    ledger['transactions'][-1]['occurred_at'] = '2026-02-01'
    create_income(ledger, second['id'], 20000, 'Salary', 'Employer', reference='FILTER-SAL-2')
    ledger['transactions'][-1]['occurred_at'] = '2026-01-15'
    create_transfer(ledger, first['id'], second['id'], 5000, 'FILTER-MOVE-1')
    ledger['transactions'][-2]['occurred_at'] = '2026-02-10'
    ledger['transactions'][-1]['occurred_at'] = '2026-02-10'
    main.save_ledger(ledger)

    january = client.get('/api/dashboard?period=month&month=2026-01').json()
    assert january['metrics']['income_received'] == 30000
    assert january['metrics']['personal_expense'] == 3000
    assert january['metrics']['gross_cash_outflow'] == 3000
    assert len(january['transactions']) == 3
    assert {account['name']: account['balance_as_of_end_paise'] for account in january['accounts']} == {'Everyday': 107000, 'Reserve': 220000}

    individual = client.get(f"/api/dashboard?period=custom&account_id={first['id']}&date_from=2026-02-01&date_to=2026-02-10").json()
    assert individual['metrics']['gross_cash_outflow'] == 9000
    assert individual['metrics']['personal_expense'] == 4000
    assert individual['metrics']['transfer_out'] == 5000
    assert len(individual['transactions']) == 2

    inclusive = client.get('/api/transactions?period=custom&date_from=2026-01-31&date_to=2026-01-31').json()['transactions']
    assert len(inclusive) == 1 and inclusive[0]['amount_paise'] == 3000
    cross_year = client.get('/api/transactions?period=custom&date_from=2025-12-31&date_to=2026-01-01').json()['transactions']
    assert len(cross_year) == 1 and cross_year[0]['amount_paise'] == 10000
    assert client.get('/api/dashboard?period=custom&date_from=2025-05-01&date_to=2025-05-31').json()['metrics']['personal_expense'] == 0
    assert client.get('/api/dashboard?period=custom&date_from=2026-02-10&date_to=2026-02-01').status_code == 400
    detail = client.get(f"/api/accounts/{first['id']}/dashboard?period=month&month=2026-01")
    assert detail.status_code == 200 and detail.json()['metrics']['personal_expense'] == 3000
