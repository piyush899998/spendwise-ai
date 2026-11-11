from fastapi.testclient import TestClient


def test_account_creation_persists_and_returns_derived_balance(tmp_path, monkeypatch):
    monkeypatch.setenv('SPENDWISE_DATABASE_URL', f'sqlite:///{tmp_path / "accounts.db"}')
    from backend.main import app

    client = TestClient(app)
    response = client.post('/api/accounts', json={
        'name': 'Travel account',
        'bank': 'Demo Bank',
        'account_type': 'savings',
        'masked_account_number': '•••• 4321',
        'opening_balance_paise': 125000,
    })
    assert response.status_code == 200
    account = response.json()['account']
    assert account['opening_balance_paise'] == 125000
    assert account['current_balance_paise'] == 125000
    listed = client.get('/api/accounts')
    assert listed.status_code == 200
    assert [item['id'] for item in listed.json()['accounts']] == [account['id']]
    assert client.get('/api/transactions').json()['transactions'] == []


def test_account_creation_rejects_invalid_values_without_persisting(tmp_path, monkeypatch):
    monkeypatch.setenv('SPENDWISE_DATABASE_URL', f'sqlite:///{tmp_path / "invalid-accounts.db"}')
    from backend.main import app

    client = TestClient(app)
    missing_fields = client.post('/api/accounts', json={
        'name': ' ', 'bank': 'Demo Bank', 'account_type': 'savings',
        'masked_account_number': '•••• 1111', 'opening_balance_paise': 0,
    })
    negative_balance = client.post('/api/accounts', json={
        'name': 'Invalid', 'bank': 'Demo Bank', 'account_type': 'savings',
        'masked_account_number': '•••• 2222', 'opening_balance_paise': -1,
    })
    assert missing_fields.status_code in (400, 422)
    assert negative_balance.status_code == 422
    assert client.get('/api/accounts').json()['accounts'] == []
