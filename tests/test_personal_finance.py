from datetime import date

import pytest

from src.engine import (
    account_balances,
    add_recurring_schedule,
    budget_status,
    confirm_recurring_schedule,
    contribute_to_goal,
    create_account,
    create_expense,
    create_goal,
    create_income,
    create_refund,
    create_transfer,
    new_ledger,
    upsert_budget,
)


def test_account_balances_are_derived_from_opening_balance_and_posted_entries():
    ledger = new_ledger()
    account = create_account(ledger, 'Daily', 'Demo Bank', 'savings', '•••• 1234', 1000000)
    create_income(ledger, account['id'], 250000, 'Salary', 'Employer', reference='SAL-001')
    create_expense(ledger, account['id'], 50000, 'Food / Dining', 'Cafe', reference='EXP-001')
    failed_like = create_expense(ledger, account['id'], 10000, 'Other', 'Not posted', reference='EXP-002')
    failed_like['status'] = 'failed'
    assert account_balances(ledger)[account['id']] == 1200000


def test_transfers_create_paired_entries_without_personal_expense():
    ledger = new_ledger()
    source = create_account(ledger, 'Checking', 'Demo Bank', 'checking', '•••• 1111', 500000)
    target = create_account(ledger, 'Savings', 'Demo Bank', 'savings', '•••• 2222', 100000)
    transfer = create_transfer(ledger, source['id'], target['id'], 200000, reference='MOVE-001')
    assert transfer['debit']['transaction_type'] == 'transfer'
    assert transfer['credit']['transaction_type'] == 'transfer'
    assert transfer['debit']['transfer_group_id'] == transfer['credit']['transfer_group_id']
    assert account_balances(ledger)[source['id']] == 300000
    assert account_balances(ledger)[target['id']] == 300000


def test_income_refund_and_reimbursement_types_are_distinct():
    ledger = new_ledger()
    account = create_account(ledger, 'Main', 'Demo Bank', 'savings', '•••• 3333', 0)
    salary = create_income(ledger, account['id'], 100000, reference='SAL-002')
    refund = create_refund(ledger, account['id'], 10000, recipient='Store', reference='REF-001')
    assert salary['transaction_type'] == 'income'
    assert refund['transaction_type'] == 'refund'


def test_recurring_schedule_requires_manual_confirmation_and_is_idempotent():
    ledger = new_ledger()
    account = create_account(ledger, 'Main', 'Demo Bank', 'savings', '•••• 4444', 0)
    schedule = add_recurring_schedule(
        ledger, account['id'], 'Monthly salary', 'salary', 250000,
        'Salary', '2026-11-01', note='Manual confirmation required',
    )
    assert ledger['transactions'] == []
    first = confirm_recurring_schedule(ledger, schedule['id'])
    second = confirm_recurring_schedule(ledger, schedule['id'], '2026-11-01')
    assert first['id'] == second['id']
    assert len(ledger['transactions']) == 1
    assert len(ledger['recurring_postings']) == 1
    assert schedule['next_due_date'] == '2026-12-01'


def test_budgets_and_goals_track_progress_in_paise():
    ledger = new_ledger()
    account = create_account(ledger, 'Main', 'Demo Bank', 'savings', '•••• 5555', 0)
    expense = create_expense(ledger, account['id'], 75000, 'Food / Dining', 'Cafe', reference='BUD-001')
    expense['occurred_at'] = '2026-11-10T12:00:00+00:00'
    budget = upsert_budget(ledger, '2026-11', 'Food / Dining', 50000)
    status = budget_status(ledger, '2026-11')[0]
    assert budget['limit_paise'] == 50000
    assert status['spent_paise'] == 75000
    assert status['overspent'] is True

    goal = create_goal(ledger, 'Emergency fund', 1000000, '2027-06-01')
    contribution = contribute_to_goal(ledger, goal['id'], 250000, 'First transfer')
    assert contribution['amount_paise'] == 250000
    assert goal['target_paise'] == 1000000


def test_transfer_requires_distinct_accounts():
    ledger = new_ledger()
    account = create_account(ledger, 'Main', 'Demo Bank', 'savings', '•••• 6666', 0)
    with pytest.raises(ValueError, match='different accounts'):
        create_transfer(ledger, account['id'], account['id'], 1000)
