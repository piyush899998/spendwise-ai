from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.db import create_database, session_scope
from src.engine import (
    add_recurring_schedule,
    add_split,
    allocate,
    confirm_recurring_schedule,
    contribute_to_goal,
    create_account,
    create_expense,
    create_goal,
    create_income,
    create_intent,
    import_csv,
    incoming,
    metrics,
    new_ledger,
    settle_intent,
    equal_split,
    upsert_budget,
)
from src.migrations import migration_status
from src.models import (
    ClassificationDecision,
    ClassificationRule,
    Account,
    ImportBatch,
    Participant,
    PaymentIntent,
    Reimbursement,
    RecurringSchedule,
    SavingsGoal,
    SplitExpense,
    Transaction,
    User,
)
from src.repositories import LedgerRepository


def ids():
    return str(uuid4())


def test_initial_migration_is_idempotent_and_creates_all_tables(tmp_path):
    database_path = tmp_path / 'migrations.db'
    engine = create_database(f'sqlite:///{database_path}')
    first = migration_status(engine)
    create_database(f'sqlite:///{database_path}')
    second = migration_status(engine)

    assert first == {'current_revision': 9, 'applied_revisions': [1, 2, 3, 4, 5, 6, 7, 8, 9]}
    assert second == first
    assert {
        'users', 'payment_intents', 'transactions', 'classification_decisions',
        'split_expenses', 'participants', 'reimbursements', 'import_batches',
        'accounts', 'recurring_schedules', 'recurring_postings', 'budgets',
        'savings_goals', 'goal_contributions',
        'chat_action_previews',
        'person_contacts',
    }.issubset(set(engine.dialect.get_table_names(engine.connect())))


def test_persists_domain_records_with_integer_paise(tmp_path):
    engine = create_database(f"sqlite:///{tmp_path / 'ledger.db'}")
    user_id = ids()
    intent_id = ids()
    transaction_id = ids()
    split_id = ids()
    participant_id = ids()
    incoming_id = ids()

    with session_scope(engine) as session:
        user = User(id=user_id, display_name='Demo User')
        session.add(user)
        session.add(PaymentIntent(
            id=intent_id, user_id=user_id, recipient='Rahul', amount_paise=250000,
            category='Housing / Rent', treatment='expense', note=None,
            remember_rule=False, status='succeeded',
        ))
        transaction = Transaction(
            id=transaction_id, user_id=user_id, intent_id=intent_id,
            external_ref='DEMO-001', recipient='Rahul', amount_paise=250000,
            direction='debit', status='succeeded', source='simulation',
            category='Housing / Rent', treatment='expense', confirmed=True,
            needs_review=False, occurred_at=datetime.now(timezone.utc),
        )
        session.add(transaction)
        session.add(ClassificationDecision(
            id=ids(), transaction_id=transaction_id, category='Housing / Rent',
            source='user', confidence=1.0, confirmed=True,
        ))
        split = SplitExpense(
            id=split_id, user_id=user_id, source_transaction_id=transaction_id,
            category='Housing / Rent', total_paise=250000, status='active',
        )
        session.add(split)
        session.add(Participant(
            id=participant_id, split_expense_id=split_id, name='Rahul',
            share_paise=125000, is_self=False, status='outstanding',
        ))
        session.add(Transaction(
            id=incoming_id, user_id=user_id, external_ref='IN-001', recipient='Rahul',
            amount_paise=125000, direction='credit', status='succeeded', source='manual',
            category='Uncategorized', treatment='unclassified', confirmed=False,
            needs_review=True, occurred_at=datetime.now(timezone.utc),
        ))
        session.add(Reimbursement(
            id=ids(), participant_id=participant_id, incoming_transaction_id=incoming_id,
            amount_paise=125000, reversed=False,
        ))
        session.add(ImportBatch(
            id=ids(), user_id=user_id, filename='sample.csv', row_count=1,
            added_count=1, skipped_count=0, error_count=0, errors_json='[]',
        ))

    with session_scope(engine) as session:
        assert session.get(Transaction, transaction_id).amount_paise == 250000
        assert session.scalar(select(ClassificationDecision).where(
            ClassificationDecision.transaction_id == transaction_id
        )).confirmed is True
        assert session.scalar(select(Participant).where(
            Participant.id == participant_id
        )).share_paise == 125000
        assert session.scalar(select(Reimbursement).where(
            Reimbursement.incoming_transaction_id == incoming_id
        )).amount_paise == 125000


def test_records_survive_application_restart(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'restart.db'}"
    first_engine = create_database(database_url)
    user_id = ids()
    with session_scope(first_engine) as session:
        session.add(User(id=user_id, display_name='Restart User'))
    first_engine.dispose()

    second_engine = create_database(database_url)
    with session_scope(second_engine) as session:
        assert session.get(User, user_id).display_name == 'Restart User'


def test_database_rejects_duplicate_transaction_references(tmp_path):
    engine = create_database(f"sqlite:///{tmp_path / 'unique.db'}")
    first_user = ids()
    second_user = ids()
    common = dict(
        user_id=first_user, external_ref='DUP-001', recipient='Store', amount_paise=100,
        direction='debit', status='succeeded', source='csv', category='Other',
        treatment='expense', confirmed=True, needs_review=False,
        occurred_at=datetime.now(timezone.utc),
    )
    with session_scope(engine) as session:
        session.add(User(id=first_user, display_name='Duplicate Test A'))
        session.add(User(id=second_user, display_name='Duplicate Test B'))
        session.add(Transaction(id=ids(), **common))

        session.add(Transaction(
            id=ids(), **{**common, 'user_id': second_user},
        ))

    with pytest.raises(IntegrityError):
        with session_scope(engine) as session:
            session.add(Transaction(id=ids(), **common))


def test_repository_round_trip_preserves_ledger_after_restart(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'round-trip.db'}"
    engine = create_database(database_url)
    ledger = new_ledger()
    intent = create_intent(ledger, 'Restaurant', 180000, 'Food / Dining', 'shared', remember=True)
    paid = settle_intent(ledger, intent['id'])
    split = add_split(ledger, paid['id'], equal_split(180000, ['Me', 'Rahul', 'Priya']))
    credit = incoming(ledger, 'Rahul', 60000, reference='REPAY-ROUNDTRIP')
    allocate(ledger, split['id'], split['participants'][1]['id'], credit['id'], 60000)
    import_csv(ledger, 'date,recipient,amount,direction,reference,note\n2026-11-05,store@upi,840,debit,CSV-ROUNDTRIP,groceries\n', filename='roundtrip.csv')
    before = metrics(ledger)

    repository = LedgerRepository(engine)
    repository.save(ledger)
    engine.dispose()

    restarted_engine = create_database(database_url)
    restored = LedgerRepository(restarted_engine).load()
    assert metrics(restored) == before
    assert restored['transactions'][0]['classification']['source'] == 'user'
    assert restored['rules'][0]['recipient'] == 'restaurant'
    assert restored['splits'][0]['participants'][1]['share_paise'] == 60000
    assert restored['allocations'][0]['amount_paise'] == 60000
    assert restored['import_batches'][0]['filename'] == 'roundtrip.csv'
    assert any(event['type'] == 'payment_settled' for event in restored['events'])
    assert any(event['type'] == 'reimbursement_confirmed' for event in restored['events'])

    repository.save(restored)
    with session_scope(restarted_engine) as session:
        assert len(session.scalars(select(ClassificationRule)).all()) == 1
    repository.clear()
    assert LedgerRepository(restarted_engine).load()['transactions'] == []


def test_repository_updates_existing_participants(tmp_path):
    engine = create_database(f"sqlite:///{tmp_path / 'participants.db'}")
    ledger = new_ledger()
    intent = create_intent(ledger, 'Restaurant', 180000, 'Food / Dining', 'shared')
    paid = settle_intent(ledger, intent['id'])
    split = add_split(ledger, paid['id'], equal_split(180000, ['Me', 'Rahul', 'Priya']))
    repository = LedgerRepository(engine)
    repository.save(ledger)

    split['participants'][1].update(name='Rahul updated', share_paise=70000)
    split['participants'][2].update(share_paise=50000)
    repository.save(ledger)
    restored = repository.load()
    participants = {p['name']: p['share_paise'] for p in restored['splits'][0]['participants']}
    assert participants['Rahul updated'] == 70000
    assert participants['Priya'] == 50000


def test_repository_preserves_classification_history(tmp_path):
    engine = create_database(f"sqlite:///{tmp_path / 'classification-history.db'}")
    ledger = new_ledger()
    intent = create_intent(ledger, 'Rahul', 250000, 'Housing / Rent', 'expense')
    txn = settle_intent(ledger, intent['id'])
    repository = LedgerRepository(engine)
    repository.save(ledger)

    txn['category'] = 'Food / Dining'
    txn['classification'] = {
        'category': 'Food / Dining', 'source': 'user', 'confidence': 1.0,
        'confirmed': True, 'needs_review': False,
    }
    repository.save(ledger)

    with session_scope(engine) as session:
        decisions = session.scalars(select(ClassificationDecision).where(
            ClassificationDecision.transaction_id == txn['id']
        ).order_by(ClassificationDecision.created_at)).all()
        assert len(decisions) == 2
        assert [decision.category for decision in decisions] == ['Housing / Rent', 'Food / Dining']
    assert repository.load()['transactions'][0]['classification']['category'] == 'Food / Dining'


def test_repository_load_clear_and_save_are_user_scoped(tmp_path):
    engine = create_database(f"sqlite:///{tmp_path / 'users.db'}")
    user_a = ids()
    user_b = ids()
    ledger_a = new_ledger()
    ledger_b = new_ledger()
    a_intent = create_intent(ledger_a, 'A', 10000, 'Other', 'expense')
    b_intent = create_intent(ledger_b, 'B', 20000, 'Other', 'expense')
    a_txn = settle_intent(ledger_a, a_intent['id'])
    settle_intent(ledger_b, b_intent['id'])
    repository = LedgerRepository(engine)
    repository.save(ledger_a, user_id=user_a, display_name='A')
    repository.save(ledger_b, user_id=user_b, display_name='B')

    assert [t['recipient'] for t in repository.load(user_a)['transactions']] == ['A']
    assert [t['recipient'] for t in repository.load(user_b)['transactions']] == ['B']

    foreign_ledger = new_ledger()
    foreign_ledger['transactions'].append({**a_txn})
    with pytest.raises(ValueError, match='belongs to another user'):
        repository.save(foreign_ledger, user_id=user_b, display_name='B')

    repository.clear(user_a)
    assert repository.load(user_a)['transactions'] == []
    assert [t['recipient'] for t in repository.load(user_b)['transactions']] == ['B']


def test_repository_round_trip_persists_phase_four_records(tmp_path):
    engine = create_database(f"sqlite:///{tmp_path / 'phase-four.db'}")
    ledger = new_ledger()
    account = create_account(ledger, 'Main', 'Demo Bank', 'savings', '•••• 7777', 500000)
    create_income(ledger, account['id'], 250000, reference='P4-SAL-001')
    expense = create_expense(ledger, account['id'], 50000, 'Food / Dining', 'Cafe', reference='P4-EXP-001')
    schedule = add_recurring_schedule(ledger, account['id'], 'Rent', 'bill', 100000, 'Housing / Rent', '2026-11-15')
    confirm_recurring_schedule(ledger, schedule['id'])
    upsert_budget(ledger, '2026-11', 'Food / Dining', 100000)
    goal = create_goal(ledger, 'Buffer', 1000000, '2027-12-01')
    contribute_to_goal(ledger, goal['id'], 25000)

    repository = LedgerRepository(engine)
    repository.save(ledger)
    restored = repository.load()
    assert len(restored['accounts']) == 1
    assert restored['transactions'][0]['account_id'] == account['id']
    assert len(restored['recurring_schedules']) == 1
    assert len(restored['recurring_postings']) == 1
    assert len(restored['budgets']) == 1
    assert len(restored['goals']) == 1
    assert restored['goal_contributions'][0]['amount_paise'] == 25000
