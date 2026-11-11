from datetime import date

from fastapi.testclient import TestClient

from src.ai_intelligence import execute_financial_tool
from src.engine import create_account, create_expense, new_ledger


def test_transaction_tool_is_read_only_scoped_paginated_and_reports_distinct_totals():
    ledger = new_ledger()
    first = create_account(ledger, 'Daily', 'Demo', 'savings', '•••• 1001', 0)
    other = create_account(ledger, 'Reserve', 'Demo', 'savings', '•••• 1002', 0)
    a = create_expense(ledger, first['id'], 101, 'Food / Dining', 'Cafe', reference='CHAT-A')
    a['occurred_at'] = '2026-09-01'
    b = create_expense(ledger, first['id'], 250, 'Food / Dining', 'Cafe', reference='CHAT-B')
    b['occurred_at'] = '2026-09-02'
    c = create_expense(ledger, other['id'], 999, 'Food / Dining', 'Cafe', reference='CHAT-C')
    c['occurred_at'] = '2026-09-03'
    before_count = len(ledger['transactions'])
    result = execute_financial_tool(ledger, 'get_transactions', {
        'category': 'Food / Dining', 'direction': 'debit', 'transaction_type': 'expense', 'limit': 1,
    }, date(2026, 9, 1), date(2026, 9, 30), first['id'])
    assert result['data']['total_count'] == 2
    assert len(result['data']['transactions']) == 1
    assert result['data']['has_more'] is True
    assert result['data']['gross_debit_paise'] == 351
    assert result['data']['confirmed_personal_expense_paise'] == 351
    assert len(ledger['transactions']) == before_count


def test_chat_followup_selects_exact_previous_category_and_confirmed_split_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv('SPENDWISE_DATABASE_URL', f'sqlite:///{tmp_path / "chat-actions.db"}')
    from backend.main import app, load_ledger, save_ledger

    client = TestClient(app)
    account = client.post('/api/accounts', json={
        'name': 'Jio', 'bank': 'Demo Bank', 'account_type': 'savings',
        'masked_account_number': '•••• 5001', 'opening_balance_paise': 100000,
    }).json()['account']
    second = client.post('/api/accounts', json={
        'name': 'Other', 'bank': 'Demo Bank', 'account_type': 'savings',
        'masked_account_number': '•••• 5002', 'opening_balance_paise': 0,
    }).json()['account']
    ledger = load_ledger()
    first_tx = create_expense(ledger, account['id'], 101, 'Food / Dining', 'Cafe dinner', reference='CHAT-FOOD-1')
    first_tx['occurred_at'] = '2026-09-10'
    second_tx = create_expense(ledger, account['id'], 200, 'Food / Dining', 'Cafe lunch', reference='CHAT-FOOD-2')
    second_tx['occurred_at'] = '2026-09-11'
    other_tx = create_expense(ledger, second['id'], 500, 'Food / Dining', 'Other cafe', reference='CHAT-FOOD-3')
    other_tx['occurred_at'] = '2026-09-12'
    august = create_expense(ledger, account['id'], 700, 'Food / Dining', 'Cafe August', reference='CHAT-FOOD-4')
    august['occurred_at'] = '2026-08-12'
    save_ledger(ledger)

    answer = client.post(f"/api/ai/chat?period=month&month=2026-09&account_id={account['id']}", json={
        'question': 'How much did I spend on food in September?',
    })
    assert answer.status_code == 200, answer.text
    session_id = answer.json()['session_id']
    assert answer.json()['context']['date_from'] == '2026-09-01'
    drilldown = client.post(f"/api/ai/chat?period=month&month=2026-09&account_id={account['id']}", json={
        'question': 'Show me all those transactions', 'session_id': session_id,
    })
    assert drilldown.status_code == 200, drilldown.text
    body = drilldown.json()
    assert {row['id'] for row in body['transactions']} == {first_tx['id'], second_tx['id']}
    assert body['data']['gross_debit_paise'] == 301
    assert body['data']['confirmed_personal_expense_paise'] == 301

    selection = client.post('/api/ai/chat/selection', json={
        'session_id': session_id, 'transaction_ids': [first_tx['id'], second_tx['id']],
    })
    assert selection.status_code == 200 and selection.json()['count'] == 2
    blocked_cross_scope = client.post('/api/ai/chat/selection', json={
        'session_id': session_id, 'transaction_ids': [other_tx['id']],
    })
    assert blocked_cross_scope.status_code == 400

    planned = client.post(f"/api/ai/chat?period=month&month=2026-09&account_id={account['id']}", json={
        'question': 'Split these transactions equally between me and Rahul.', 'session_id': session_id,
    })
    assert planned.status_code == 200, planned.text
    action = planned.json()['action_preview']
    assert action['preview']['total_paise'] == 301
    assert {item['name']: item['share_paise'] for item in action['preview']['participants']} == {'You': 150, 'Rahul': 151}
    assert client.get('/api/splits').json()['splits'] == []
    transactions_before = len(load_ledger()['transactions'])

    committed = client.post('/api/ai/actions/confirm', json={'preview_id': action['id']})
    assert committed.status_code == 200 and committed.json()['status'] == 'committed', committed.text
    replay = client.post('/api/ai/actions/confirm', json={'preview_id': action['id']})
    assert replay.status_code == 200 and replay.json()['idempotent_replay'] is True
    splits = client.get('/api/splits').json()['splits']
    assert len(splits) == 2
    assert len(load_ledger()['transactions']) == transactions_before == 4
    dashboard = client.get(f"/api/dashboard?period=month&month=2026-09&account_id={account['id']}").json()
    assert dashboard['metrics']['gross_paid'] == 301
    assert dashboard['metrics']['personal_expense'] == 150


def test_action_preview_rejects_duplicate_people_and_custom_share_mismatch(tmp_path, monkeypatch):
    monkeypatch.setenv('SPENDWISE_DATABASE_URL', f'sqlite:///{tmp_path / "chat-actions-invalid.db"}')
    from backend.main import app, load_ledger, save_ledger

    client = TestClient(app)
    account = client.post('/api/accounts', json={'name': 'Main', 'bank': 'Demo', 'account_type': 'savings', 'masked_account_number': '•••• 5101', 'opening_balance_paise': 0}).json()['account']
    ledger = load_ledger()
    tx = create_expense(ledger, account['id'], 1000, 'Food / Dining', 'Cafe', reference='CHAT-CUSTOM-1')
    tx['occurred_at'] = '2026-09-10'
    save_ledger(ledger)
    chat = client.post(f"/api/ai/chat?period=month&month=2026-09&account_id={account['id']}", json={'question': 'Show transactions'})
    assert chat.status_code == 200
    session_id = chat.json()['session_id']
    client.post('/api/ai/chat/selection', json={'session_id': session_id, 'transaction_ids': [tx['id']]})
    duplicate_names = client.post('/api/ai/actions/split/preview', json={
        'session_id': session_id, 'idempotency_key': 'duplicate-name-action', 'split_method': 'equal',
        'participants': [{'name': 'Rahul', 'is_self': True}, {'name': ' rahul '}],
    })
    assert duplicate_names.status_code == 400
    bad_shares = client.post('/api/ai/actions/split/preview', json={
        'session_id': session_id, 'idempotency_key': 'bad-custom-action', 'split_method': 'custom',
        'participants': [{'name': 'Me', 'is_self': True, 'share_paise': 400}, {'name': 'Rahul', 'share_paise': 500}],
    })
    assert bad_shares.status_code == 400
    assert len(load_ledger()['transactions']) == 1 and client.get('/api/splits').json()['splits'] == []


def test_custom_and_percentage_shares_reconcile_to_integer_paise():
    from backend.main import _split_shares_for_transactions

    transactions = [{'id': 'one', 'amount_paise': 101}, {'id': 'two', 'amount_paise': 99}]
    people = [{'name': 'Me', 'is_self': True, 'share_paise': 100},
              {'name': 'Rahul', 'is_self': False, 'share_paise': 50},
              {'name': 'Aman', 'is_self': False, 'share_paise': 50}]
    custom = _split_shares_for_transactions(transactions, people, 'custom')
    assert all(sum(row['share_paise'] for row in shares) == transactions[index]['amount_paise']
               for index, shares in enumerate(custom.values()))
    assert {name: sum(row['share_paise'] for shares in custom.values() for row in shares if row['name'] == name)
            for name in ('Me', 'Rahul', 'Aman')} == {'Me': 100, 'Rahul': 50, 'Aman': 50}
    percentage_people = [{'name': 'Me', 'is_self': True, 'basis_points': 3333},
                         {'name': 'Rahul', 'is_self': False, 'basis_points': 3333},
                         {'name': 'Aman', 'is_self': False, 'basis_points': 3334}]
    percentage = _split_shares_for_transactions(transactions, percentage_people, 'percentage')
    assert sum(row['share_paise'] for shares in percentage.values() for row in shares) == 200
    assert all(sum(row['share_paise'] for row in shares) == transactions[index]['amount_paise']
               for index, shares in enumerate(percentage.values()))


def test_conversational_food_split_three_people_contact_creation_and_replay(tmp_path, monkeypatch):
    """End-to-end action on synthetic data only; the configured database is untouched."""
    monkeypatch.setenv('SPENDWISE_DATABASE_URL', f'sqlite:///{tmp_path / "food-split.db"}')
    from backend.main import app, load_ledger, save_ledger
    from src.models import PersonContact
    from src.db import create_database, session_scope
    from sqlalchemy import select

    client = TestClient(app)
    account = client.post('/api/accounts', json={
        'name': 'Synthetic Jio', 'bank': 'Synthetic Bank', 'account_type': 'savings',
        'masked_account_number': '•••• 9001', 'opening_balance_paise': 2500000,
    }).json()['account']
    ledger = load_ledger()
    transactions = []
    for index in range(15):
        amount = 100000 if index < 14 else 108036
        tx = create_expense(ledger, account['id'], amount, 'Food / Dining',
                            f'Synthetic cafe {index + 1}', reference=f'SYNTH-FOOD-{index + 1}')
        tx['occurred_at'] = f'2026-09-{(index % 28) + 1:02d}'
        transactions.append(tx)
    save_ledger(ledger)
    originals = {tx['id']: (tx['amount_paise'], tx['occurred_at'], tx['external_ref'], tx['recipient'])
                 for tx in load_ledger()['transactions']}

    scope = f"period=month&month=2026-09&account_id={account['id']}"
    summary = client.post(f'/api/ai/chat?{scope}', json={'question': 'How much did I spend on Food / Dining in September?'})
    assert summary.status_code == 200, summary.text
    session_id = summary.json()['session_id']
    detail = client.post(f'/api/ai/chat?{scope}', json={
        'question': 'Show me all those transactions', 'session_id': session_id,
    })
    assert detail.status_code == 200, detail.text
    retrieved_ids = {row['id'] for row in detail.json()['transactions']}
    assert retrieved_ids == {tx['id'] for tx in transactions}
    assert detail.json()['context']['account_id'] == account['id']
    assert detail.json()['context']['date_from'] == '2026-09-01'
    assert detail.json()['context']['date_to'] == '2026-09-30'

    planned = client.post(f'/api/ai/chat?{scope}', json={
        'question': 'participants are harsh and deku, divide the food expense equally',
        'session_id': session_id,
    })
    assert planned.status_code == 200, planned.text
    plan = planned.json()
    action = plan['action_preview']
    assert plan['tool'] == 'create_shared_expense_preview'
    assert len(action['preview']['transactions']) == 15
    assert {row['id'] for row in action['preview']['transactions']} == retrieved_ids
    assert {item['name']: item['share_paise'] for item in action['preview']['participants']} == {
        'You': 502678, 'Harsh': 502679, 'Deku': 502679,
    }
    assert {item['name']: item['contact_status'] for item in action['preview']['participants']} == {
        'You': 'self', 'Harsh': 'create_on_confirm', 'Deku': 'create_on_confirm',
    }
    assert client.get('/api/splits').json()['splits'] == []
    assert load_ledger()['contacts'] == []
    assert {tx['id']: (tx['amount_paise'], tx['occurred_at'], tx['external_ref'], tx['recipient'])
            for tx in load_ledger()['transactions']} == originals

    confirmed = client.post('/api/ai/actions/confirm', json={'preview_id': action['id']})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()['transaction_count'] == 15
    replay = client.post('/api/ai/actions/confirm', json={'preview_id': action['id']})
    assert replay.status_code == 200 and replay.json()['idempotent_replay'] is True
    saved = load_ledger()
    assert len(saved['splits']) == 15
    assert len(saved['contacts']) == 2
    assert {tx['id']: (tx['amount_paise'], tx['occurred_at'], tx['external_ref'], tx['recipient'])
            for tx in saved['transactions']} == originals
    assert sum(person['share_paise'] for split in saved['splits'] for person in split['participants'] if person['is_self']) == 502678
    assert sum(person['share_paise'] for split in saved['splits'] for person in split['participants'] if not person['is_self']) == 1005358
    with session_scope(create_database()) as session:
        contacts = session.scalars(select(PersonContact)).all()
        assert {contact.normalized_name for contact in contacts} == {'harsh', 'deku'}
    dashboard = client.get(f'/api/dashboard?{scope}').json()
    assert dashboard['metrics']['personal_expense'] == 502678
    assert dashboard['metrics']['by_category']['Food / Dining'] == 502678
    owed = client.get('/api/splits').json()['splits']
    assert len(owed) == 15


def test_split_language_parser_includes_self_and_recognizes_alternates():
    from backend.main import _parse_conversational_participants

    expected = {'Harsh': False, 'Deku': False, 'You': True}
    for phrase in (
        'participants are harsh and deku, divide the food expense equally',
        'divide the food expense equally between Harsh, Deku and me',
    ):
        parsed = _parse_conversational_participants(phrase)
        assert {person['name']: person['is_self'] for person in parsed} == expected


def test_local_frontend_origin_is_allowed_for_cors(tmp_path, monkeypatch):
    monkeypatch.setenv('SPENDWISE_DATABASE_URL', f'sqlite:///{tmp_path / "cors.db"}')
    response = TestClient(__import__('backend.main', fromlist=['app']).app).options(
        '/api/accounts', headers={
            'Origin': 'http://localhost:3001',
            'Access-Control-Request-Method': 'GET',
        },
    )
    assert response.status_code == 200
    assert response.headers.get('access-control-allow-origin') == 'http://localhost:3001'


def test_conversational_month_followup_keeps_account_and_category(tmp_path, monkeypatch):
    monkeypatch.setenv('SPENDWISE_DATABASE_URL', f'sqlite:///{tmp_path / "chat-followup.db"}')
    from backend.main import app, load_ledger, save_ledger

    client = TestClient(app)
    first = client.post('/api/accounts', json={'name': 'Daily', 'bank': 'Demo', 'account_type': 'savings', 'masked_account_number': '•••• 5201', 'opening_balance_paise': 0}).json()['account']
    second = client.post('/api/accounts', json={'name': 'Other', 'bank': 'Demo', 'account_type': 'savings', 'masked_account_number': '•••• 5202', 'opening_balance_paise': 0}).json()['account']
    ledger = load_ledger()
    sep = create_expense(ledger, first['id'], 12000, 'Food / Dining', 'Cafe', reference='FOLLOW-SEP')
    sep['occurred_at'] = '2026-09-02'
    aug = create_expense(ledger, first['id'], 8000, 'Food / Dining', 'Cafe', reference='FOLLOW-AUG')
    aug['occurred_at'] = '2026-08-02'
    excluded = create_expense(ledger, second['id'], 50000, 'Food / Dining', 'Other', reference='FOLLOW-OTHER')
    excluded['occurred_at'] = '2026-08-03'
    save_ledger(ledger)
    first_answer = client.post(f"/api/ai/chat?period=month&month=2026-09&account_id={first['id']}", json={'question': 'How much did I spend on food in September?'})
    session_id = first_answer.json()['session_id']
    followup = client.post(f"/api/ai/chat?period=month&month=2026-09&account_id={first['id']}", json={'question': 'What about August?', 'session_id': session_id})
    assert followup.status_code == 200
    assert followup.json()['context']['account_id'] == first['id']
    assert followup.json()['context']['date_from'] == '2026-08-01'
    assert '₹80.00' in followup.json()['answer']
