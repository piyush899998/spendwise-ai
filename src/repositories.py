"""Persistence adapter for the existing in-memory ledger shape.

The adapter deliberately owns all ORM mapping. ``src.engine`` remains a pure
business-logic module and can continue to operate without a database.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timezone

from sqlalchemy import select

from .db import session_scope
from .engine import new_ledger, uid
from .models import (
    Account,
    Budget,
    ChatActionPreview,
    ClassificationDecision as ClassificationDecisionRow,
    ClassificationRule,
    GoalContribution,
    ImportBatch,
    PersonContact,
    Participant,
    PaymentIntent,
    Reimbursement,
    RecurringPosting,
    RecurringSchedule,
    SavingsGoal,
    SplitExpense,
    Transaction,
    TransactionEvent,
    User,
)


def _datetime(value):
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, time.min)
    else:
        text = str(value)
        result = datetime.fromisoformat(text.replace('Z', '+00:00'))
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)


def _iso(value):
    return value.isoformat() if value else None


class LedgerRepository:
    def __init__(self, engine):
        self.engine = engine

    @staticmethod
    def _assert_owned(session, model, row_id, user_id):
        existing = session.get(model, row_id)
        if existing is not None and getattr(existing, 'user_id', None) != user_id:
            raise ValueError(f'{model.__tablename__} record belongs to another user')

    def save(self, ledger, user_id='demo-user', display_name='Demo User', consume_action_id=None):
        """Persist a complete ledger in one transaction.

        Existing rows are merged by primary key. External references remain
        protected by the database unique constraint, so a conflicting new row
        fails the transaction instead of silently overwriting another payment.
        """
        with session_scope(self.engine) as session:
            action_preview = None
            if consume_action_id:
                action_preview = session.scalar(select(ChatActionPreview).where(
                    ChatActionPreview.id == consume_action_id, ChatActionPreview.user_id == user_id,
                ))
                if action_preview is None or action_preview.status != 'pending':
                    raise ValueError('Action preview is no longer pending')
                expiry = action_preview.expires_at
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                if expiry <= datetime.now(timezone.utc):
                    action_preview.status = 'expired'
                    raise ValueError('Action preview has expired')
            session.merge(User(id=user_id, display_name=display_name))
            existing_contacts = {
                row.normalized_name: row for row in session.scalars(
                    select(PersonContact).where(PersonContact.user_id == user_id)
                ).all()
            }
            for contact in ledger.get('contacts', []):
                normalized = contact.get('normalized_name') or ' '.join(contact['display_name'].casefold().split())
                existing_contact = existing_contacts.get(normalized)
                if existing_contact:
                    contact['id'] = existing_contact.id
                else:
                    row = PersonContact(
                        id=contact.get('id') or uid(), user_id=user_id,
                        display_name=contact['display_name'], normalized_name=normalized,
                        created_at=_datetime(contact.get('created_at') or datetime.now(timezone.utc)),
                    )
                    session.add(row)
                    existing_contacts[normalized] = row
                    contact['id'] = row.id
            session.flush()
            for item in ledger.get('accounts', []):
                self._assert_owned(session, Account, item['id'], user_id)
                session.merge(Account(
                    id=item['id'], user_id=user_id, name=item['name'], bank=item['bank'],
                    account_type=item['account_type'], masked_account_number=item['masked_account_number'],
                    opening_balance_paise=item['opening_balance_paise'], is_active=item.get('is_active', True),
                    created_at=_datetime(item['created_at']),
                ))
            for item in ledger['intents']:
                self._assert_owned(session, PaymentIntent, item['id'], user_id)
                session.merge(PaymentIntent(
                    id=item['id'], user_id=user_id, account_id=item.get('account_id'), recipient=item['recipient'],
                    amount_paise=item['amount_paise'], category=item['category'],
                    treatment=item['treatment'], note=item.get('note'),
                    remember_rule=item.get('remember', False), status=item['status'],
                    created_at=_datetime(item['created_at']),
                ))
            for item in ledger['transactions']:
                self._assert_owned(session, Transaction, item['id'], user_id)
                session.merge(Transaction(
                    id=item['id'], user_id=user_id, intent_id=item.get('intent_id'),
                    account_id=item.get('account_id'),
                    external_ref=item.get('external_ref'), recipient=item['recipient'], merchant=item.get('merchant'),
                    amount_paise=item['amount_paise'], direction=item['direction'],
                    status=item['status'], source=item['source'], category=item['category'],
                    treatment=item['treatment'], confirmed=item.get('confirmed', False),
                    needs_review=item.get('needs_review', False),
                    occurred_at=_datetime(item['occurred_at']), transaction_type=item.get('transaction_type'),
                    transfer_group_id=item.get('transfer_group_id'), note=item.get('note'),
                ))
            session.flush()
            for item in ledger['transactions']:
                classification = item.get('classification')
                if not classification:
                    continue
                existing = session.scalar(select(ClassificationDecisionRow).where(
                    ClassificationDecisionRow.transaction_id == item['id']
                ).order_by(ClassificationDecisionRow.created_at.desc()))
                is_same_as_latest = existing and (
                    existing.category == classification['category']
                    and existing.source == classification['source']
                    and existing.confidence == classification.get('confidence')
                    and existing.confirmed == classification['confirmed']
                )
                if not is_same_as_latest:
                    session.add(ClassificationDecisionRow(
                        id=uid(), transaction_id=item['id'], category=classification['category'],
                        source=classification['source'], confidence=classification.get('confidence'),
                        confirmed=classification['confirmed'],
                    ))
            for item in ledger['splits']:
                self._assert_owned(session, SplitExpense, item['id'], user_id)
                source = next(t for t in ledger['transactions'] if t['id'] == item['transaction_id'])
                source_row = session.get(Transaction, item['transaction_id'])
                if source_row is None or source_row.user_id != user_id:
                    raise ValueError('Split source transaction belongs to another user or does not exist')
                split = session.merge(SplitExpense(
                    id=item['id'], user_id=user_id, source_transaction_id=item['transaction_id'],
                    category=source['category'], total_paise=source['amount_paise'], status='active',
                ))
                session.flush()
                existing_participants = {
                    p.id: p for p in session.scalars(select(Participant).where(Participant.split_expense_id == split.id))
                }
                incoming_participant_ids = set()
                for participant in item['participants']:
                    incoming_participant_ids.add(participant['id'])
                    existing_participant = session.get(Participant, participant['id'])
                    if existing_participant is not None and existing_participant.split_expense_id != split.id:
                        raise ValueError('Participant belongs to another split')
                    contact_id = participant.get('contact_id')
                    if contact_id:
                        contact_row = session.get(PersonContact, contact_id)
                        if contact_row is None or contact_row.user_id != user_id:
                            raise ValueError('Split participant references another user contact')
                    session.merge(Participant(
                        id=participant['id'], split_expense_id=split.id,
                        contact_id=contact_id,
                        name=participant['name'], share_paise=participant['share_paise'],
                        is_self=participant['is_self'], status=participant.get('status', 'active'),
                    ))
                for participant_id, existing_participant in existing_participants.items():
                    if participant_id not in incoming_participant_ids:
                        existing_participant.status = 'inactive'
            session.flush()
            for item in ledger['allocations']:
                participant = session.get(Participant, item['share_id'])
                credit = session.get(Transaction, item['credit_id'])
                if participant is None or credit is None:
                    raise ValueError('Reimbursement references a missing record')
                split_owner = session.scalar(select(SplitExpense).where(
                    SplitExpense.id == participant.split_expense_id,
                    SplitExpense.user_id == user_id,
                ))
                if split_owner is None or credit.user_id != user_id:
                    raise ValueError('Reimbursement references another user')
                existing_reimbursement = session.get(Reimbursement, item['id'])
                if existing_reimbursement is not None and (
                    existing_reimbursement.participant_id != item['share_id']
                    or existing_reimbursement.incoming_transaction_id != item['credit_id']
                ):
                    raise ValueError('Reimbursement belongs to another record')
                session.merge(Reimbursement(
                    id=item['id'], participant_id=item['share_id'], incoming_transaction_id=item['credit_id'],
                    amount_paise=item['amount_paise'], reversed=item.get('reversed', False),
                ))
            for item in ledger.get('rules', []):
                self._assert_owned(session, ClassificationRule, item.get('id'), user_id) if item.get('id') else None
                rule_id = item.get('id')
                if not rule_id:
                    existing_rule = session.scalar(select(ClassificationRule).where(
                        ClassificationRule.user_id == user_id,
                        ClassificationRule.recipient == item['recipient'],
                        ClassificationRule.category == item['category'],
                        ClassificationRule.treatment == item['treatment'],
                    ))
                    rule_id = existing_rule.id if existing_rule else uid()
                    item['id'] = rule_id
                session.merge(ClassificationRule(
                    id=rule_id, user_id=user_id, recipient=item['recipient'],
                    category=item['category'], treatment=item['treatment'], enabled=item.get('enabled', True),
                ))
            for item in ledger.get('import_batches', []):
                self._assert_owned(session, ImportBatch, item['id'], user_id)
                session.merge(ImportBatch(
                    id=item['id'], user_id=user_id, filename=item.get('filename', 'uploaded.csv'),
                    imported_at=_datetime(item['imported_at']), row_count=item['row_count'],
                    added_count=item.get('added', 0), skipped_count=item.get('skipped', 0),
                    error_count=item.get('error_count', 0), errors_json=json.dumps(item.get('errors', [])),
                ))
            for item in ledger.get('recurring_schedules', []):
                self._assert_owned(session, RecurringSchedule, item['id'], user_id)
                account = session.get(Account, item['account_id'])
                if account is None or account.user_id != user_id:
                    raise ValueError('Recurring schedule references another user account')
                session.merge(RecurringSchedule(
                    id=item['id'], user_id=user_id, account_id=item['account_id'], name=item['name'],
                    schedule_type=item['schedule_type'], amount_paise=item['amount_paise'], category=item['category'],
                    frequency=item['frequency'], next_due_date=date.fromisoformat(str(item['next_due_date'])[:10]),
                    note=item.get('note'), is_active=item.get('is_active', True), created_at=_datetime(item['created_at']),
                ))
            session.flush()
            for item in ledger.get('recurring_postings', []):
                schedule = session.get(RecurringSchedule, item['schedule_id'])
                transaction = session.get(Transaction, item['transaction_id'])
                if schedule is None or schedule.user_id != user_id or transaction is None or transaction.user_id != user_id:
                    raise ValueError('Recurring posting references another user')
                session.merge(RecurringPosting(
                    id=item['id'], schedule_id=item['schedule_id'],
                    due_date=date.fromisoformat(str(item['due_date'])[:10]), transaction_id=item['transaction_id'],
                    posted_at=_datetime(item['posted_at']),
                ))
            for item in ledger.get('budgets', []):
                self._assert_owned(session, Budget, item['id'], user_id)
                session.merge(Budget(
                    id=item['id'], user_id=user_id, month=item['month'], category=item['category'],
                    limit_paise=item['limit_paise'], created_at=_datetime(item['created_at']),
                ))
            for item in ledger.get('goals', []):
                self._assert_owned(session, SavingsGoal, item['id'], user_id)
                session.merge(SavingsGoal(
                    id=item['id'], user_id=user_id, name=item['name'], target_paise=item['target_paise'],
                    target_date=date.fromisoformat(str(item['target_date'])[:10]) if item.get('target_date') else None,
                    color=item.get('color', 'pine'), is_active=item.get('is_active', True), created_at=_datetime(item['created_at']),
                ))
            session.flush()
            for item in ledger.get('goal_contributions', []):
                goal = session.get(SavingsGoal, item['goal_id'])
                if goal is None or goal.user_id != user_id:
                    raise ValueError('Goal contribution references another user')
                session.merge(GoalContribution(
                    id=item['id'], goal_id=item['goal_id'], amount_paise=item['amount_paise'],
                    contributed_at=_datetime(item['contributed_at']), note=item.get('note'),
                ))
            for item in ledger.get('events', []):
                if item.get('id'):
                    if item.get('transaction_id'):
                        event_transaction = session.get(Transaction, item['transaction_id'])
                        if event_transaction is None or event_transaction.user_id != user_id:
                            raise ValueError('Transaction event references another user')
                    existing_event = session.get(TransactionEvent, item['id'])
                    if existing_event is not None and existing_event.transaction_id != item.get('transaction_id'):
                        raise ValueError('Transaction event belongs to another record')
                    session.merge(TransactionEvent(
                        id=item['id'], transaction_id=item.get('transaction_id'),
                        event_type=item['type'], payload_json=json.dumps(item), created_at=_datetime(item['at']),
                    ))
            if action_preview is not None:
                action_preview.status = 'committed'
                action_preview.committed_at = datetime.now(timezone.utc)

    def clear(self, user_id='demo-user'):
        """Delete one demo user's persisted ledger, preserving the schema."""
        with session_scope(self.engine) as session:
            transaction_ids = list(session.scalars(select(Transaction.id).where(Transaction.user_id == user_id)))
            split_ids = list(session.scalars(select(SplitExpense.id).where(SplitExpense.user_id == user_id)))
            participant_ids = list(session.scalars(select(Participant.id).where(Participant.split_expense_id.in_(split_ids)))) if split_ids else []
            if participant_ids:
                for row in session.scalars(select(Reimbursement).where(Reimbursement.participant_id.in_(participant_ids))).all():
                    session.delete(row)
                for row in session.scalars(select(Participant).where(Participant.id.in_(participant_ids))).all():
                    session.delete(row)
            for row in session.scalars(select(SplitExpense).where(SplitExpense.id.in_(split_ids))).all() if split_ids else []:
                session.delete(row)
            if transaction_ids:
                for row in session.scalars(select(ClassificationDecisionRow).where(ClassificationDecisionRow.transaction_id.in_(transaction_ids))).all():
                    session.delete(row)
                for row in session.scalars(select(TransactionEvent).where(TransactionEvent.transaction_id.in_(transaction_ids))).all():
                    session.delete(row)
                for row in session.scalars(select(Transaction).where(Transaction.id.in_(transaction_ids))).all():
                    session.delete(row)
            for row in session.scalars(select(PaymentIntent).where(PaymentIntent.user_id == user_id)).all():
                session.delete(row)
            for row in session.scalars(select(ClassificationRule).where(ClassificationRule.user_id == user_id)).all():
                session.delete(row)
            for row in session.scalars(select(ImportBatch).where(ImportBatch.user_id == user_id)).all():
                session.delete(row)
            schedule_ids = list(session.scalars(select(RecurringSchedule.id).where(RecurringSchedule.user_id == user_id)))
            if schedule_ids:
                for row in session.scalars(select(RecurringPosting).where(RecurringPosting.schedule_id.in_(schedule_ids))).all():
                    session.delete(row)
                for row in session.scalars(select(RecurringSchedule).where(RecurringSchedule.id.in_(schedule_ids))).all():
                    session.delete(row)
            goal_ids = list(session.scalars(select(SavingsGoal.id).where(SavingsGoal.user_id == user_id)))
            if goal_ids:
                for row in session.scalars(select(GoalContribution).where(GoalContribution.goal_id.in_(goal_ids))).all():
                    session.delete(row)
                for row in session.scalars(select(SavingsGoal).where(SavingsGoal.id.in_(goal_ids))).all():
                    session.delete(row)
            for row in session.scalars(select(Budget).where(Budget.user_id == user_id)).all():
                session.delete(row)
            for row in session.scalars(select(Account).where(Account.user_id == user_id)).all():
                session.delete(row)
            for row in session.scalars(select(PersonContact).where(PersonContact.user_id == user_id)).all():
                session.delete(row)

    def load(self, user_id='demo-user'):
        """Rehydrate the existing ledger shape for the Streamlit adapter."""
        ledger = new_ledger()
        with session_scope(self.engine) as session:
            ledger['contacts'] = [{
                'id': row.id, 'display_name': row.display_name,
                'normalized_name': row.normalized_name, 'created_at': _iso(row.created_at),
            } for row in session.scalars(select(PersonContact).where(PersonContact.user_id == user_id)).all()]
            accounts = session.scalars(select(Account).where(Account.user_id == user_id)).all()
            ledger['accounts'] = [{
                'id': item.id, 'name': item.name, 'bank': item.bank, 'account_type': item.account_type,
                'masked_account_number': item.masked_account_number, 'opening_balance_paise': item.opening_balance_paise,
                'is_active': item.is_active, 'created_at': _iso(item.created_at),
            } for item in accounts]
            intents = session.scalars(select(PaymentIntent).where(PaymentIntent.user_id == user_id)).all()
            transactions = session.scalars(select(Transaction).where(Transaction.user_id == user_id)).all()
            for item in intents:
                ledger['intents'].append({
                    'id': item.id, 'recipient': item.recipient, 'amount_paise': item.amount_paise,
                    'account_id': item.account_id,
                    'category': item.category, 'treatment': item.treatment, 'note': item.note or '',
                    'remember': item.remember_rule, 'status': item.status, 'created_at': _iso(item.created_at),
                })
            for item in transactions:
                latest = session.scalar(select(ClassificationDecisionRow).where(
                    ClassificationDecisionRow.transaction_id == item.id
                ).order_by(
                    ClassificationDecisionRow.created_at.desc(),
                    ClassificationDecisionRow.id.desc(),
                ))
                classification = None if not latest else {
                    'category': latest.category, 'source': latest.source, 'confidence': latest.confidence,
                    'confirmed': latest.confirmed, 'needs_review': not latest.confirmed,
                }
                ledger['transactions'].append({
                    'id': item.id, 'intent_id': item.intent_id, 'external_ref': item.external_ref,
                    'account_id': item.account_id,
                    'recipient': item.recipient, 'merchant': item.merchant, 'amount_paise': item.amount_paise, 'direction': item.direction,
                    'status': item.status, 'category': item.category, 'treatment': item.treatment,
                    'source': item.source, 'confirmed': item.confirmed, 'needs_review': item.needs_review,
                    'classification': classification, 'transaction_type': item.transaction_type,
                    'transfer_group_id': item.transfer_group_id, 'note': item.note or '',
                    'occurred_at': _iso(item.occurred_at),
                })
            split_rows = session.scalars(select(SplitExpense).where(SplitExpense.user_id == user_id)).all()
            for split in split_rows:
                ledger['splits'].append({
                    'id': split.id, 'transaction_id': split.source_transaction_id,
                    'participants': [{
                        'id': p.id, 'contact_id': p.contact_id, 'name': p.name, 'share_paise': p.share_paise,
                        'is_self': p.is_self, 'status': p.status,
                    } for p in split.participants if p.status != 'inactive'],
                })
            transaction_ids = {t['id'] for t in ledger['transactions']}
            reimbursements = session.scalars(
                select(Reimbursement)
                .join(Participant, Reimbursement.participant_id == Participant.id)
                .join(SplitExpense, Participant.split_expense_id == SplitExpense.id)
                .join(Transaction, Reimbursement.incoming_transaction_id == Transaction.id)
                .where(
                    SplitExpense.user_id == user_id,
                    Transaction.user_id == user_id,
                )
            ).all()
            ledger['allocations'] = [{
                'id': r.id, 'share_id': r.participant_id, 'credit_id': r.incoming_transaction_id,
                'amount_paise': r.amount_paise, 'reversed': r.reversed,
                'split_id': next((s['id'] for s in ledger['splits'] if any(
                    p['id'] == r.participant_id for p in s['participants']
                )), None),
            } for r in reimbursements if r.incoming_transaction_id in transaction_ids]
            rules = session.scalars(select(ClassificationRule).where(ClassificationRule.user_id == user_id)).all()
            ledger['rules'] = [{
                'id': r.id, 'recipient': r.recipient, 'category': r.category,
                'treatment': r.treatment, 'enabled': r.enabled,
            } for r in rules]
            batches = session.scalars(select(ImportBatch).where(ImportBatch.user_id == user_id)).all()
            ledger['import_batches'] = [{
                'id': b.id, 'filename': b.filename, 'imported_at': _iso(b.imported_at),
                'row_count': b.row_count, 'added': b.added_count, 'skipped': b.skipped_count,
                'error_count': b.error_count, 'errors': json.loads(b.errors_json or '[]'),
            } for b in batches]
            schedules = session.scalars(select(RecurringSchedule).where(RecurringSchedule.user_id == user_id)).all()
            ledger['recurring_schedules'] = [{
                'id': item.id, 'account_id': item.account_id, 'name': item.name,
                'schedule_type': item.schedule_type, 'amount_paise': item.amount_paise,
                'category': item.category, 'frequency': item.frequency,
                'next_due_date': item.next_due_date.isoformat(), 'note': item.note or '',
                'is_active': item.is_active, 'created_at': _iso(item.created_at),
            } for item in schedules]
            schedule_ids = {item['id'] for item in ledger['recurring_schedules']}
            postings = session.scalars(select(RecurringPosting).where(RecurringPosting.schedule_id.in_(schedule_ids))).all() if schedule_ids else []
            ledger['recurring_postings'] = [{
                'id': item.id, 'schedule_id': item.schedule_id, 'due_date': item.due_date.isoformat(),
                'transaction_id': item.transaction_id, 'posted_at': _iso(item.posted_at),
            } for item in postings]
            budgets = session.scalars(select(Budget).where(Budget.user_id == user_id)).all()
            ledger['budgets'] = [{
                'id': item.id, 'month': item.month, 'category': item.category,
                'limit_paise': item.limit_paise, 'created_at': _iso(item.created_at),
            } for item in budgets]
            goals = session.scalars(select(SavingsGoal).where(SavingsGoal.user_id == user_id)).all()
            ledger['goals'] = [{
                'id': item.id, 'name': item.name, 'target_paise': item.target_paise,
                'target_date': item.target_date.isoformat() if item.target_date else None,
                'color': item.color, 'is_active': item.is_active, 'created_at': _iso(item.created_at),
            } for item in goals]
            goal_ids = {item['id'] for item in ledger['goals']}
            contributions = session.scalars(select(GoalContribution).where(GoalContribution.goal_id.in_(goal_ids))).all() if goal_ids else []
            ledger['goal_contributions'] = [{
                'id': item.id, 'goal_id': item.goal_id, 'amount_paise': item.amount_paise,
                'contributed_at': _iso(item.contributed_at), 'note': item.note or '',
            } for item in contributions]
            events = session.scalars(select(TransactionEvent).where(
                TransactionEvent.transaction_id.in_(transaction_ids)
            )).all() if transaction_ids else []
            ledger['events'] = [json.loads(event.payload_json) for event in events]
        return ledger
