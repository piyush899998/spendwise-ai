from datetime import date
import io
import json
from types import SimpleNamespace
import urllib.error

import pytest
from fastapi.testclient import TestClient

from src.ai_intelligence import (
    LLMToolCall, execute_financial_tool, insight_analysis, parse_llm_classification,
    parse_llm_tool_call, restricted_chat_answer,
)
from src.engine import categorize_existing_transactions, classify, create_account, create_expense, create_income, import_csv, new_ledger
from src.db import create_database
from src.repositories import LedgerRepository


def test_llm_schema_is_strict_and_local_fallback_needs_no_key():
    with pytest.raises(ValueError):
        parse_llm_classification({'category': 'Not a category', 'intent': 'expense'})
    with pytest.raises(ValueError):
        parse_llm_classification({'category': 'Food / Dining', 'intent': 'expense', 'confidence': 2})
    parsed = parse_llm_classification({'category': 'Food / Dining', 'intent': 'expense', 'confidence': 0.7, 'explanation': 'Merchant matched'})
    assert parsed.category == 'Food / Dining'
    assert parse_llm_tool_call({'tool_name': 'get_category_breakdown', 'arguments': {'category': 'Food / Dining'}}).tool_name == 'get_category_breakdown'
    with pytest.raises(ValueError):
        parse_llm_tool_call({'tool_name': 'execute_sql', 'arguments': {}})


def test_insights_are_deterministic_and_do_not_flag_without_history():
    ledger = new_ledger()
    account = create_account(ledger, 'Main', 'Demo', 'savings', '•••• 1001', 0)
    expense = create_expense(ledger, account['id'], 100000, 'Food / Dining', 'Cafe', reference='AI-1')
    expense['occurred_at'] = '2026-09-10'
    income = create_income(ledger, account['id'], 200000, 'Salary', 'Employer', reference='AI-2')
    income['occurred_at'] = '2026-09-01'
    result = insight_analysis(ledger, date(2026, 9, 1), date(2026, 9, 30), account['id'])
    assert result['metrics']['personal_expense'] == 100000
    assert result['category_breakdown'][0] == {'category': 'Food / Dining', 'amount_paise': 100000}
    assert not any(item['kind'] == 'anomaly' for item in result['insights'])


def test_restricted_tools_are_scoped_and_follow_up_month_is_inclusive():
    ledger = new_ledger()
    first = create_account(ledger, 'Everyday', 'Demo Bank', 'savings', '•••• 4101', 0)
    second = create_account(ledger, 'Reserve', 'Demo Bank', 'savings', '•••• 4102', 0)
    september = create_expense(ledger, first['id'], 12500, 'Food / Dining', 'Cafe', reference='TOOL-SEP')
    september['occurred_at'] = '2026-09-30'
    august = create_expense(ledger, first['id'], 5000, 'Food / Dining', 'Cafe', reference='TOOL-AUG')
    august['occurred_at'] = '2026-08-31'
    other = create_expense(ledger, second['id'], 9000, 'Shopping', 'Store', reference='TOOL-OTHER')
    other['occurred_at'] = '2026-09-01'
    scoped = execute_financial_tool(ledger, 'get_category_breakdown', {'category': 'Food / Dining'}, date(2026, 9, 1), date(2026, 9, 30), first['id'])
    assert scoped['data']['categories'] == [{'category': 'Food / Dining', 'amount_paise': 12500}]
    with pytest.raises(ValueError):
        execute_financial_tool(ledger, 'get_spending_summary', {'date_from': '2026-09-20', 'date_to': '2026-09-01'}, date(2026, 9, 1), date(2026, 9, 30))
    follow_up = restricted_chat_answer(ledger, 'What about August?', date(2026, 9, 1), date(2026, 9, 30), first['id'])
    assert follow_up['period'] == {'date_from': '2026-08-01', 'date_to': '2026-08-31'}
    assert '₹50.00' in follow_up['answer']
    january_follow_up = restricted_chat_answer(ledger, 'What about August 2026?', date(2026, 1, 1), date(2026, 1, 31), first['id'])
    assert january_follow_up['period'] == {'date_from': '2026-08-01', 'date_to': '2026-08-31'}


def test_unresolved_debits_are_gross_outflow_but_not_confirmed_personal_spending():
    ledger = new_ledger()
    account = create_account(ledger, 'Imported', 'Demo Bank', 'savings', '•••• 4201', 0)
    transaction = create_expense(ledger, account['id'], 5895499, 'Uncategorized', 'UPI/DR/123/Unknown Person/Payment', reference='UNRESOLVED-1')
    transaction['occurred_at'] = '2026-09-15'
    transaction['classification'] = {'category': 'Uncategorized', 'source': 'needs_review', 'confidence': None, 'confirmed': False, 'needs_review': True}
    transaction['confirmed'] = False
    transaction['needs_review'] = True
    result = execute_financial_tool(ledger, 'get_spending_summary', {}, date(2026, 9, 1), date(2026, 9, 30), account['id'])
    assert result['data']['gross_cash_outflow'] == 5895499
    assert result['data']['personal_expense'] == 0
    assert result['data']['unclassified_debits_paise'] == 5895499
    assert 'excluded from confirmed personal spending' in restricted_chat_answer(ledger, 'How much did I spend?', date(2026, 9, 1), date(2026, 9, 30), account['id'])['answer']


def test_account_comparison_expands_scope_explicitly_and_labels_gross_outflow():
    ledger = new_ledger()
    account = create_account(ledger, 'alok', 'Jio Payments Bank', 'savings', '•••• 4202', 0)
    other = create_account(ledger, 'Reserve', 'Demo Bank', 'savings', '•••• 4203', 0)
    transaction = create_expense(ledger, account['id'], 5895499, 'Uncategorized', 'UPI/DR/123/Unknown Person/Payment', reference='COMPARE-1')
    transaction['occurred_at'] = '2026-09-15'
    transaction['classification'] = {'category': 'Uncategorized', 'source': 'needs_review', 'confidence': None, 'confirmed': False, 'needs_review': True}
    transaction['confirmed'] = False
    transaction['needs_review'] = True
    comparison = execute_financial_tool(ledger, 'get_account_comparison', {}, date(2026, 9, 1), date(2026, 9, 30), account['id'])
    assert comparison['account'] == 'All Accounts'
    assert comparison['account_id'] is None
    assert comparison['scope_expanded_from'] == 'alok'
    alok = next(row for row in comparison['data']['accounts'] if row['name'] == 'alok')
    assert alok['personal_expense'] == 0
    assert alok['gross_cash_outflow'] == 5895499
    answer = restricted_chat_answer(ledger, 'Which account contributed the most?', date(2026, 9, 1), date(2026, 9, 30), account['id'])['answer']
    assert 'All Accounts' in answer and 'expanded because account comparison' in answer


def test_indian_merchant_registry_handles_upi_aliases_but_not_bare_person_names():
    ledger = new_ledger()
    assert classify(ledger, 'UPI/DR/987654321/Zomato Media Private Limited/HDFC/zomato1pay/Payment').category == 'Food / Dining'
    assert classify(ledger, 'UPI/DR/987654321/Blinkit Commerce Private Limited/YESB/Payment').category == 'Groceries'
    assert classify(ledger, 'Amazon').category == 'Uncategorized'
    assert classify(ledger, 'UPI/DR/987654321/Amazon Seller Services/Payment').category == 'Shopping'
    assert classify(ledger, 'UPI/DR/987654321/Rahul Sharma/Payment').category == 'Uncategorized'


def test_ai_suggestions_are_unconfirmed_and_provider_failures_do_not_change_ledger(monkeypatch):
    ledger = new_ledger()
    account = create_account(ledger, 'AI', 'Demo', 'savings', '•••• 4151', 0)
    transaction = create_expense(ledger, account['id'], 3000, 'Uncategorized', 'A person', reference='AI-SUGGESTION')
    transaction['classification'] = {'category': 'Uncategorized', 'source': 'needs_review', 'confidence': None, 'confirmed': False, 'needs_review': True}; transaction['confirmed'] = False; transaction['needs_review'] = True
    calls = []
    summary = categorize_existing_transactions(ledger, ai_classifier=lambda row: calls.append(row['id']) or SimpleNamespace(category='Shopping', confidence=0.99))
    assert summary['ai_assisted_suggestions'] == 1 and summary['suggested_for_review'] == 1
    assert transaction['category'] == 'Shopping' and transaction['classification']['source'] == 'ai_suggestion'
    assert transaction['classification']['confidence'] == 0.85 and transaction['needs_review'] is True
    before = transaction.copy()
    second = categorize_existing_transactions(ledger, ai_classifier=lambda row: (_ for _ in ()).throw(AssertionError('must not reprocess suggestion')))
    assert second['automatically_categorized'] == 0 and transaction['amount_paise'] == before['amount_paise']


def test_openrouter_timeout_returns_no_suggestion(monkeypatch):
    from src.ai_provider import OpenRouterProvider

    monkeypatch.setenv('SPENDWISE_AI_EXTERNAL_OPT_IN', 'true')
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test-key')
    def timeout(*args, **kwargs):
        raise TimeoutError('synthetic timeout')
    monkeypatch.setattr('urllib.request.urlopen', timeout)
    assert OpenRouterProvider().classify({'description': 'redacted', 'amount_paise': 100}) is None


def test_openrouter_effective_model_defaults_to_free_and_allows_explicit_override(monkeypatch):
    from src.ai_provider import OpenRouterProvider

    monkeypatch.delenv('SPENDWISE_AI_MODEL', raising=False)
    assert OpenRouterProvider().model == 'nvidia/nemotron-3.5-super-vl:free'
    monkeypatch.setenv('SPENDWISE_AI_MODEL', 'nvidia/nemotron-3.5-super-vl:free')
    assert OpenRouterProvider().model == 'nvidia/nemotron-3.5-super-vl:free'


def test_openrouter_http_diagnostics_are_specific_and_sanitized(monkeypatch):
    from src.ai_provider import AIProviderError, OpenRouterProvider

    monkeypatch.setenv('SPENDWISE_AI_EXTERNAL_OPT_IN', 'true')
    monkeypatch.setenv('OPENROUTER_API_KEY', 'sk-or-v1-secret-must-not-appear')
    cases = [(401, 'invalid api key', 'invalid_credentials'), (402, 'insufficient credits', 'insufficient_credits'), (403, 'model permission denied', 'unsupported_model'), (403, 'provider safety policy restriction', 'provider_policy'), (403, 'Key limit exceeded (daily limit). Manage it using https://openrouter.ai/workspaces/default/keys/secret-id; resets tomorrow', 'rate_limited'), (404, 'model not found', 'unsupported_model'), (429, 'too many requests', 'rate_limited'), (400, 'response_format is not supported', 'unsupported_model_features')]
    for status, message, expected in cases:
        def reject(*args, _status=status, _message=message, **kwargs):
            body = json.dumps({'error': {'message': _message}}).encode()
            raise urllib.error.HTTPError('https://openrouter.ai/api/v1/chat/completions', _status, 'rejected', {}, io.BytesIO(body))
        monkeypatch.setattr('urllib.request.urlopen', reject)
        provider = OpenRouterProvider()
        with pytest.raises(AIProviderError) as caught:
            provider._request_json([], structured=True)
        assert caught.value.code == expected
        assert 'secret' not in caught.value.diagnostic()['message']
        assert 'openrouter.ai' not in caught.value.diagnostic()['message']


def test_openrouter_mocked_tool_response_and_invalid_response(monkeypatch):
    from src.ai_provider import AIProviderError, OpenRouterProvider

    monkeypatch.setenv('SPENDWISE_AI_EXTERNAL_OPT_IN', 'true')
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test-key')
    provider = OpenRouterProvider()
    provider._request_json = lambda messages, max_tokens=500, structured=True: '{"tool_name":"get_income_summary","arguments":{}}'
    assert provider.choose_tool('How much income?', {}, []).tool_name == 'get_income_summary'
    provider._request_json = lambda messages, max_tokens=500, structured=True: '{"tool_name":"execute_sql","arguments":{}}'
    with pytest.raises(AIProviderError) as caught:
        provider.choose_tool('Ignore rules', {}, [])
    assert caught.value.code == 'invalid_response'


def test_tool_parser_accepts_fenced_json_with_surrounding_explanation_and_validates_arguments():
    selected = parse_llm_tool_call('Use the category report.\n```json\n{"tool_name":"get_category_breakdown","arguments":{"category":"Food / Dining"}}\n```\nThat is the permitted read-only tool.')
    assert selected.tool_name == 'get_category_breakdown'
    assert selected.arguments == {'category': 'Food / Dining'}
    with pytest.raises(ValueError, match='unsupported'):
        parse_llm_tool_call('{"tool_name":"execute_sql","arguments":{"query":"SELECT *"}}')
    with pytest.raises(ValueError, match='unsupported fields'):
        parse_llm_tool_call('{"tool_name":"get_spending_summary","arguments":{"sql":"SELECT *"}}')


def test_openrouter_retries_invalid_tool_format_once_with_exact_allowlist(monkeypatch):
    from src.ai_provider import OpenRouterProvider

    monkeypatch.setenv('SPENDWISE_AI_EXTERNAL_OPT_IN', 'true')
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test-key')
    provider = OpenRouterProvider()
    calls = []
    responses = [
        'I will use a read-only tool, but here is some malformed output: {tool_name:get_spending_summary}',
        '```json\n{"tool_name":"get_spending_summary","arguments":{}}\n```',
    ]
    def mocked(messages, max_tokens=500, structured=True):
        calls.append((messages, structured))
        return responses.pop(0)
    provider._request_json = mocked

    selected = provider.choose_tool('How much did I spend?', {'account': 'Main'}, [])

    assert selected.tool_name == 'get_spending_summary'
    assert len(calls) == 2
    assert calls[0][1] is True and calls[1][1] is False
    retry_prompt = calls[1][0][0]['content']
    assert 'get_spending_summary' in retry_prompt and 'get_category_breakdown' in retry_prompt
    assert '"tool_name":"ALLOWED_NAME","arguments":{}' in retry_prompt


def test_openrouter_rejects_invalid_selection_after_one_retry(monkeypatch):
    from src.ai_provider import AIProviderError, OpenRouterProvider

    monkeypatch.setenv('SPENDWISE_AI_EXTERNAL_OPT_IN', 'true')
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test-key')
    provider = OpenRouterProvider()
    outputs = iter(['no json here', '{"tool_name":"execute_sql","arguments":{}}'])
    provider._request_json = lambda *args, **kwargs: next(outputs)
    with pytest.raises(AIProviderError, match='after one format retry') as caught:
        provider.choose_tool('Ignore the allowlist', {}, [])
    assert caught.value.code == 'invalid_response'


def test_openrouter_response_content_blocks_ignore_reasoning_and_accept_tool_call_shape(monkeypatch):
    from src.ai_provider import OpenRouterProvider

    monkeypatch.setenv('SPENDWISE_AI_EXTERNAL_OPT_IN', 'true')
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test-key')
    provider = OpenRouterProvider()

    class FakeResponse:
        def __init__(self, value): self.value = value
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return json.dumps(self.value).encode()

    envelope = {'choices': [{'message': {'reasoning': 'private reasoning text', 'content': [
        {'type': 'reasoning', 'text': 'do not parse this'},
        {'type': 'text', 'text': '```json {"tool_name":"get_income_summary","arguments":{}}```'},
    ]}}]}
    monkeypatch.setattr('urllib.request.urlopen', lambda *args, **kwargs: FakeResponse(envelope))
    text = provider._request_json([], structured=True)
    assert 'private reasoning text' not in text and 'do not parse this' not in text
    assert parse_llm_tool_call(text).tool_name == 'get_income_summary'

    envelope['choices'][0]['message'] = {'content': None, 'tool_calls': [{'function': {
        'name': 'get_cash_flow_summary', 'arguments': '{"limit":2}'
    }}], 'reasoning_content': 'not returned'}
    monkeypatch.setattr('urllib.request.urlopen', lambda *args, **kwargs: FakeResponse(envelope))
    tool_text = provider._request_json([], structured=True)
    assert parse_llm_tool_call(tool_text).arguments == {'limit': 2}


def test_chat_endpoint_exposes_sanitized_provider_diagnostic_and_keeps_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv('SPENDWISE_DATABASE_URL', f'sqlite:///{tmp_path / "provider-error.db"}')
    from backend import main
    from src.ai_provider import AIProviderError

    class FailingProvider:
        enabled = True
        model = 'nvidia/nemotron-3.5-super-vl:free'

        def choose_tool(self, *args, **kwargs):
            raise AIProviderError('insufficient_credits', 'Provider account has insufficient credits')

    monkeypatch.setattr(main, 'OpenRouterProvider', FailingProvider)
    client = TestClient(main.app)
    response = client.post('/api/ai/chat?period=all_time', json={'question': 'How much did I spend?'})
    assert response.status_code == 200
    payload = response.json()
    assert payload['provider_status'] == 'unavailable'
    assert payload['provider_model'] == 'nvidia/nemotron-3.5-super-vl:free'
    assert payload['provider_diagnostic']['code'] == 'insufficient_credits'
    assert 'calculated locally' in payload['answer'] or payload['ai_notice'].endswith('calculated locally.')


def test_bulk_categorization_is_idempotent_preserves_manual_rows_and_survives_restart(tmp_path):
    database_url = f'sqlite:///{tmp_path / "backfill.db"}'
    engine = create_database(database_url)
    ledger = new_ledger()
    account = create_account(ledger, 'Imported', 'Demo', 'savings', '•••• 7101', 0)
    unknown = import_csv(ledger, 'date,recipient,amount,direction,reference,note\n2026-09-01,UPI/DR/1/Swiggy/Payment,100.00,debit,BL-1,\n', account_id=account['id'])
    imported = next(row for row in ledger['transactions'] if row['external_ref'] == 'BL-1')
    imported['category'] = 'Uncategorized'; imported['classification'] = {'category': 'Uncategorized', 'source': 'needs_review', 'confidence': None, 'confirmed': False, 'needs_review': True}; imported['confirmed'] = False; imported['needs_review'] = True
    manual = create_expense(ledger, account['id'], 2000, 'Uncategorized', 'A person', reference='BL-2')
    manual['classification'] = {'category': 'Uncategorized', 'source': 'user', 'confidence': 1.0, 'confirmed': True, 'needs_review': False}
    manual['confirmed'] = True; manual['needs_review'] = False
    LedgerRepository(engine).save(ledger)
    loaded = LedgerRepository(engine).load()
    summary = categorize_existing_transactions(loaded)
    assert summary['automatically_categorized'] == 1
    assert summary['skipped_manual'] == 1
    LedgerRepository(engine).save(loaded)
    second = categorize_existing_transactions(LedgerRepository(engine).load())
    assert second['automatically_categorized'] == 0
    persisted = LedgerRepository(engine).load()
    assert next(row for row in persisted['transactions'] if row['external_ref'] == 'BL-1')['category'] == 'Food / Dining'
    assert next(row for row in persisted['transactions'] if row['external_ref'] == 'BL-2')['category'] == 'Uncategorized'
    assert len(persisted['transactions']) == 2


def test_api_bulk_backfill_and_future_csv_import_return_categorization_summary(tmp_path, monkeypatch):
    monkeypatch.setenv('SPENDWISE_DATABASE_URL', f'sqlite:///{tmp_path / "bulk-api.db"}')
    from backend import main

    class UnexpectedProvider:
        def __init__(self):
            raise AssertionError('automatic backfill/import must not make external model calls')

    monkeypatch.setattr(main, 'OpenRouterProvider', UnexpectedProvider)

    client = TestClient(main.app)
    account = client.post('/api/accounts', json={'name': 'Imports', 'bank': 'Demo', 'account_type': 'savings', 'masked_account_number': '•••• 7201', 'opening_balance_paise': 0}).json()['account']
    ledger = main.load_ledger()
    legacy = create_expense(ledger, account['id'], 2500, 'Uncategorized', 'UPI/DR/1/Swiggy/Payment', reference='API-LEGACY')
    legacy['classification'] = {'category': 'Uncategorized', 'source': 'needs_review', 'confidence': None, 'confirmed': False, 'needs_review': True}; legacy['confirmed'] = False; legacy['needs_review'] = True
    main.save_ledger(ledger)
    bulk = client.post('/api/ai/categorization/bulk')
    assert bulk.status_code == 200
    assert bulk.json()['automatically_categorized'] == 1
    assert main.load_ledger()['transactions'][0]['category'] == 'Food / Dining'
    repeated = client.post('/api/ai/categorization/bulk')
    assert repeated.status_code == 200 and repeated.json()['automatically_categorized'] == 0

    csv = 'date,recipient,amount,direction,reference,note\n2026-09-02,UPI/DR/2/Amazon Seller Services/Payment,75.00,debit,API-NEW,\n'
    imported = client.post('/api/imports', json={'filename': 'bank.csv', 'text': csv})
    assert imported.status_code == 200
    assert imported.json()['categorization']['automatically_categorized'] == 1
    assert next(row for row in main.load_ledger()['transactions'] if row['external_ref'] == 'API-NEW')['category'] == 'Shopping'


def test_bulk_backfill_classifies_known_imports_without_external_ai_and_keeps_p2p_for_review(tmp_path, monkeypatch):
    monkeypatch.setenv('SPENDWISE_DATABASE_URL', f'sqlite:///{tmp_path / "offline-backfill.db"}')
    monkeypatch.setenv('SPENDWISE_AI_EXTERNAL_OPT_IN', 'false')
    monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
    from backend import main

    client = TestClient(main.app)
    account = client.post('/api/accounts', json={'name': 'Imports', 'bank': 'Demo', 'account_type': 'savings', 'masked_account_number': '•••• 7202', 'opening_balance_paise': 0}).json()['account']
    ledger = main.load_ledger()
    entries = [
        ('UPI/DR/88001/Swiggy/Payment', 'BACKFILL-SWIGGY'),
        ('UPI/DR/88002/Blinkit Commerce Private Limited/Payment', 'BACKFILL-BLINKIT'),
        ('UPI/DR/88003/Rahul Sharma/Payment', 'BACKFILL-PERSON'),
    ]
    for index, (narration, reference) in enumerate(entries):
        row = create_expense(ledger, account['id'], (index + 1) * 1000, 'Uncategorized', narration, reference=reference)
        row['occurred_at'] = '2026-09-10'
        row['category'] = 'Uncategorized'
        row['classification'] = {'category': 'Uncategorized', 'source': 'needs_review', 'confidence': None, 'confirmed': False, 'needs_review': True}
        row['confirmed'] = False
        row['needs_review'] = True
    main.save_ledger(ledger)
    before = [(row['id'], row['amount_paise'], str(row['occurred_at'])[:10], row['external_ref'], row['recipient']) for row in ledger['transactions']]

    result = client.post('/api/ai/categorization/bulk?period=all_time')
    assert result.status_code == 200
    summary = result.json()
    assert summary['automatically_categorized'] == 2
    assert summary['still_uncategorized'] == 1
    persisted = main.load_ledger()
    assert len(persisted['transactions']) == 3
    assert [(row['id'], row['amount_paise'], str(row['occurred_at'])[:10], row['external_ref'], row['recipient']) for row in persisted['transactions']] == before
    assert {row['recipient']: row['category'] for row in persisted['transactions']} == {
        'UPI/DR/88001/Swiggy/Payment': 'Food / Dining',
        'UPI/DR/88002/Blinkit Commerce Private Limited/Payment': 'Groceries',
        'UPI/DR/88003/Rahul Sharma/Payment': 'Uncategorized',
    }
    imported = client.post('/api/imports', json={'filename': 'offline.csv', 'text':
        'date,recipient,amount,direction,reference,note\n'
        '2026-09-11,UPI/DR/88004/Zomato Media Private Limited/Payment,25.00,debit,OFFLINE-NEW,\n'})
    assert imported.status_code == 200
    assert imported.json()['categorization']['automatically_categorized'] == 1
    assert any(row['external_ref'] == 'OFFLINE-NEW' and row['category'] == 'Food / Dining'
               for row in main.load_ledger()['transactions'])


def test_ai_api_is_read_only_for_chat_and_reports_are_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv('SPENDWISE_DATABASE_URL', f'sqlite:///{tmp_path / "ai.db"}')
    # The project-root .env may intentionally enable external AI for local use;
    # this regression test must remain deterministic and offline.
    monkeypatch.setenv('SPENDWISE_AI_EXTERNAL_OPT_IN', 'false')
    from backend import main

    client = TestClient(main.app)
    account = client.post('/api/accounts', json={
        'name': 'Everyday', 'bank': 'Demo', 'account_type': 'savings',
        'masked_account_number': '•••• 2001', 'opening_balance_paise': 100000,
    }).json()['account']
    ledger = main.load_ledger()
    item = create_expense(ledger, account['id'], 12500, 'Food / Dining', 'Cafe', reference='AI-EXP-1')
    item['occurred_at'] = '2026-09-10'
    main.save_ledger(ledger)

    insights = client.get(f"/api/ai/insights?period=month&month=2026-09&account_id={account['id']}")
    assert insights.status_code == 200
    assert insights.json()['category_breakdown'][0]['amount_paise'] == 12500
    feedback = client.post('/api/ai/insights/period-change/feedback', json={'feedback': 'useful'})
    assert feedback.status_code == 200 and feedback.json()['feedback'] == 'useful'
    chat = client.post(f"/api/ai/chat?period=month&month=2026-09&account_id={account['id']}", json={'question': 'How much did I spend on food?'})
    assert chat.status_code == 200
    assert '₹125.00' in chat.json()['answer']
    assert chat.json()['provider_status'] == 'local_fallback'
    assert len(main.load_ledger()['transactions']) == 1
    suggestion = client.post(f"/api/ai/categorization-suggestion/{item['id']}")
    assert suggestion.status_code == 200 and suggestion.json()['suggestion'] is None

    first = client.post('/api/ai/reports?period=month&month=2026-09', json={'report_type': 'monthly'})
    second = client.post('/api/ai/reports?period=month&month=2026-09', json={'report_type': 'monthly'})
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()['report']['id'] == second.json()['report']['id']
    report_id = first.json()['report']['id']
    download = client.get(f'/api/ai/reports/{report_id}/download?format=pdf')
    assert download.status_code == 200 and download.content.startswith(b'%PDF')


def test_opted_in_provider_selects_only_validated_tools_without_mutating_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv('SPENDWISE_DATABASE_URL', f'sqlite:///{tmp_path / "provider.db"}')
    monkeypatch.setenv('SPENDWISE_AI_EXTERNAL_OPT_IN', 'true')
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test-only-key')
    from backend import main

    class FakeProvider:
        enabled = True

        def choose_tool(self, question, context, tools, history=None):
            assert any(tool['name'] == 'get_category_breakdown' for tool in tools)
            assert 'date_from' in context and 'date_to' in context
            return LLMToolCall('get_category_breakdown', {'category': 'Food / Dining'})

        def summarize(self, question, context, tool_name, result):
            assert tool_name == 'get_category_breakdown'
            assert result['data']['categories'][0]['amount_paise'] == 2500
            return 'Verified food result: ₹25.00.'

    monkeypatch.setattr(main, 'OpenRouterProvider', FakeProvider)
    client = TestClient(main.app)
    account = client.post('/api/accounts', json={'name': 'Provider test', 'bank': 'Demo', 'account_type': 'savings', 'masked_account_number': '•••• 5101', 'opening_balance_paise': 0}).json()['account']
    ledger = main.load_ledger()
    item = create_expense(ledger, account['id'], 2500, 'Food / Dining', 'Cafe', reference='PROVIDER-1')
    item['occurred_at'] = '2026-09-10'
    main.save_ledger(ledger)
    before = main.load_ledger()['transactions'][0].copy()
    response = client.post(f"/api/ai/chat?period=month&month=2026-09&account_id={account['id']}", json={'question': 'How much did I spend on food?'})
    assert response.status_code == 200
    assert response.json()['provider_status'] == 'available'
    assert response.json()['tool'] == 'get_category_breakdown'
    assert response.json()['answer'] == 'Verified food result: ₹25.00.'
    after = main.load_ledger()['transactions'][0]
    assert after['amount_paise'] == before['amount_paise'] and after['external_ref'] == before['external_ref']


def test_ai_filters_corrections_recurring_candidates_and_report_totals(tmp_path, monkeypatch):
    monkeypatch.setenv('SPENDWISE_DATABASE_URL', f'sqlite:///{tmp_path / "integration.db"}')
    from backend import main

    client = TestClient(main.app)
    first = client.post('/api/accounts', json={'name': 'Daily', 'bank': 'Demo', 'account_type': 'savings', 'masked_account_number': '•••• 6101', 'opening_balance_paise': 0}).json()['account']
    second = client.post('/api/accounts', json={'name': 'Reserve', 'bank': 'Demo', 'account_type': 'savings', 'masked_account_number': '•••• 6102', 'opening_balance_paise': 0}).json()['account']
    ledger = main.load_ledger()
    first_food = create_expense(ledger, first['id'], 10000, 'Uncategorized', 'Cafe', reference='INT-SEP-1')
    first_food['occurred_at'] = '2026-09-10'
    first_food['needs_review'] = True
    first_food['classification']['confirmed'] = False
    second_food = create_expense(ledger, first['id'], 12000, 'Food / Dining', 'Cafe', reference='INT-AUG-1')
    second_food['occurred_at'] = '2026-08-10'
    other_expense = create_expense(ledger, second['id'], 5000, 'Shopping', 'Store', reference='INT-SEP-2')
    other_expense['occurred_at'] = '2026-09-11'
    salary = create_income(ledger, first['id'], 50000, 'Income', 'Employer', reference='INT-SEP-INCOME')
    salary['occurred_at'] = '2026-09-01'
    recurring_one = create_expense(ledger, first['id'], 999, 'Subscriptions', 'Streaming Service', reference='INT-REC-1')
    recurring_one['occurred_at'] = '2026-08-01'
    recurring_two = create_expense(ledger, first['id'], 999, 'Subscriptions', 'Streaming Service', reference='INT-REC-2')
    recurring_two['occurred_at'] = '2026-09-01'
    main.save_ledger(ledger)

    combined = client.get('/api/ai/insights?period=month&month=2026-09')
    individual = client.get(f"/api/ai/insights?period=month&month=2026-09&account_id={first['id']}")
    assert combined.status_code == individual.status_code == 200
    assert combined.json()['metrics']['personal_expense'] == 5999
    assert individual.json()['metrics']['personal_expense'] == 999
    assert client.get('/api/ai/recurring-candidates?period=all_time').json()['candidates'][0]['merchant'] == 'Streaming Service'

    corrected = client.post(f"/api/ai/categorization/{first_food['id']}", json={'category': 'Food / Dining', 'treatment': 'expense', 'apply_to_similar': False})
    assert corrected.status_code == 200
    corrected_insights = client.get(f"/api/ai/insights?period=month&month=2026-09&account_id={first['id']}").json()
    assert corrected_insights['category_breakdown'][0] == {'category': 'Food / Dining', 'amount_paise': 10000}
    report = client.post('/api/ai/reports?period=month&month=2026-09', json={'report_type': 'monthly'}).json()['report']
    assert report['content']['metrics']['personal_expense'] == 15999
    assert len(main.load_ledger()['transactions']) == 6


def test_categorization_review_can_apply_user_rule_without_changing_amount(tmp_path, monkeypatch):
    monkeypatch.setenv('SPENDWISE_DATABASE_URL', f'sqlite:///{tmp_path / "review.db"}')
    from backend import main

    client = TestClient(main.app)
    account = client.post('/api/accounts', json={
        'name': 'Main', 'bank': 'Demo', 'account_type': 'savings',
        'masked_account_number': '•••• 3001', 'opening_balance_paise': 0,
    }).json()['account']
    ledger = main.load_ledger()
    item = create_expense(ledger, account['id'], 9999, 'Uncategorized', 'A person', reference='AI-REVIEW-1')
    item['occurred_at'] = '2026-09-12'
    item['needs_review'] = True
    item['classification']['confirmed'] = False
    main.save_ledger(ledger)
    response = client.get(f"/api/ai/categorization/review?period=month&month=2026-09&account_id={account['id']}")
    assert response.status_code == 200 and response.json()['count'] == 1
    updated = client.post(f"/api/ai/categorization/{item['id']}", json={'category': 'Loans', 'treatment': 'loan', 'apply_to_similar': True})
    assert updated.status_code == 200
    restored = main.load_ledger()['transactions'][0]
    assert restored['amount_paise'] == 9999 and restored['category'] == 'Loans'
    assert client.get('/api/ai/rules').json()['rules'][0]['category'] == 'Loans'
