from datetime import date

from src.ai_intelligence import execute_financial_tool, restricted_chat_answer
from src.categories import CATEGORIES, category_from_text, normalize_category
from src.engine import classify, create_account, create_expense, new_ledger


def test_canonical_registry_and_natural_language_aliases():
    assert len(CATEGORIES) == len(set(CATEGORIES))
    assert normalize_category('Bills / Utilities') == 'Bills'  # the persisted name is Bills
    assert normalize_category('Housing') == 'Housing / Rent'
    assert normalize_category('Transportation') == 'Transport'
    assert category_from_text('education expenses in September') == 'Education'
    assert category_from_text('show all my taxi payments') == 'Transport'
    assert category_from_text('travel spending') == 'Travel'
    assert normalize_category('Internal Transfer') == 'Transfers'


def test_deterministic_indian_merchant_rules_are_conservative():
    ledger = new_ledger()
    assert classify(ledger, 'UPI/DR/1/Uber India/Payment').category == 'Transport'
    assert classify(ledger, 'UPI/DR/2/Coursera/Payment').category == 'Education'
    assert classify(ledger, 'UPI/DR/3/Apollo Pharmacy/Payment').category == 'Healthcare'
    assert classify(ledger, 'UPI/DR/4/BookMyShow/Payment').category == 'Entertainment'
    assert classify(ledger, 'Harsh').category == 'Uncategorized'


def test_category_queries_use_aliases_and_report_empty_categories_without_inventing_data():
    ledger = new_ledger()
    account = create_account(ledger, 'Main', 'Demo', 'savings', '•••• 9001', 0)
    transport = create_expense(ledger, account['id'], 1200, 'Transport', 'Uber', reference='CAT-1')
    transport['occurred_at'] = '2026-09-10'
    education = create_expense(ledger, account['id'], 2500, 'Education', 'College fee', reference='CAT-2')
    education['occurred_at'] = '2026-09-11'
    result = execute_financial_tool(ledger, 'get_transactions', {'category': 'transportation'}, date(2026, 9, 1), date(2026, 9, 30), account['id'])
    assert [row['id'] for row in result['data']['transactions']] == [transport['id']]
    empty = restricted_chat_answer(ledger, 'How much did I spend on travel?', date(2026, 9, 1), date(2026, 9, 30), account['id'])
    assert 'No confirmed Travel transactions were found' in empty['answer']
    assert 'unclassified' not in empty['answer'].lower() or empty['data']['unclassified_debits_paise'] == 0
