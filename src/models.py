"""SQLAlchemy persistence models for the SpendWise ledger.

These models contain storage concerns only. Business rules such as payment
state transitions, split allocation, and classification precedence remain in
``src.engine`` and repository/service code will translate between the two.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'users'

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    payment_intents: Mapped[list['PaymentIntent']] = relationship(back_populates='user')
    transactions: Mapped[list['Transaction']] = relationship(back_populates='user')
    split_expenses: Mapped[list['SplitExpense']] = relationship(back_populates='user')
    import_batches: Mapped[list['ImportBatch']] = relationship(back_populates='user')
    classification_rules: Mapped[list['ClassificationRule']] = relationship(back_populates='user')
    accounts: Mapped[list['Account']] = relationship(back_populates='user')
    recurring_schedules: Mapped[list['RecurringSchedule']] = relationship(back_populates='user')
    budgets: Mapped[list['Budget']] = relationship(back_populates='user')
    savings_goals: Mapped[list['SavingsGoal']] = relationship(back_populates='user')


class PaymentIntent(Base):
    __tablename__ = 'payment_intents'

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    account_id: Mapped[Optional[str]] = mapped_column(ForeignKey('accounts.id'), index=True)
    recipient: Mapped[str] = mapped_column(String(200), nullable=False)
    amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    treatment: Mapped[str] = mapped_column(String(30), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text)
    remember_rule: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    user: Mapped[User] = relationship(back_populates='payment_intents')
    account: Mapped[Optional['Account']] = relationship(back_populates='payment_intents')
    transaction: Mapped[Optional['Transaction']] = relationship(back_populates='intent', uselist=False)


class Transaction(Base):
    __tablename__ = 'transactions'
    __table_args__ = (
        Index(
            'uq_transactions_user_external_ref', 'user_id', 'external_ref',
            unique=True, sqlite_where=text('external_ref IS NOT NULL'),
            postgresql_where=text('external_ref IS NOT NULL'),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    account_id: Mapped[Optional[str]] = mapped_column(ForeignKey('accounts.id'), index=True)
    intent_id: Mapped[Optional[str]] = mapped_column(ForeignKey('payment_intents.id'), unique=True)
    external_ref: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    recipient: Mapped[str] = mapped_column(String(200), nullable=False)
    merchant: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    treatment: Mapped[str] = mapped_column(String(30), nullable=False)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    transaction_type: Mapped[Optional[str]] = mapped_column(String(30))
    transfer_group_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    note: Mapped[Optional[str]] = mapped_column(Text)

    user: Mapped[User] = relationship(back_populates='transactions')
    intent: Mapped[Optional[PaymentIntent]] = relationship(back_populates='transaction')
    classification_decisions: Mapped[list['ClassificationDecision']] = relationship(back_populates='transaction')
    split_expense: Mapped[Optional['SplitExpense']] = relationship(back_populates='source_transaction', uselist=False)
    events: Mapped[list['TransactionEvent']] = relationship(back_populates='transaction')
    account: Mapped[Optional['Account']] = relationship(back_populates='transactions')


class ClassificationDecision(Base):
    __tablename__ = 'classification_decisions'

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(ForeignKey('transactions.id'), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    transaction: Mapped[Transaction] = relationship(back_populates='classification_decisions')


class SplitExpense(Base):
    __tablename__ = 'split_expenses'

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    source_transaction_id: Mapped[str] = mapped_column(ForeignKey('transactions.id'), unique=True, nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    total_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    user: Mapped[User] = relationship(back_populates='split_expenses')
    source_transaction: Mapped[Transaction] = relationship(back_populates='split_expense')
    participants: Mapped[list['Participant']] = relationship(back_populates='split_expense', cascade='all, delete-orphan')


class PersonContact(Base):
    __tablename__ = 'person_contacts'
    __table_args__ = (UniqueConstraint('user_id', 'normalized_name', name='uq_person_contact_user_name'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class Participant(Base):
    __tablename__ = 'participants'

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    split_expense_id: Mapped[str] = mapped_column(ForeignKey('split_expenses.id'), nullable=False, index=True)
    contact_id: Mapped[Optional[str]] = mapped_column(ForeignKey('person_contacts.id'), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    share_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    is_self: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    split_expense: Mapped[SplitExpense] = relationship(back_populates='participants')
    reimbursements: Mapped[list['Reimbursement']] = relationship(back_populates='participant')


class Reimbursement(Base):
    __tablename__ = 'reimbursements'

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    participant_id: Mapped[str] = mapped_column(ForeignKey('participants.id'), nullable=False, index=True)
    incoming_transaction_id: Mapped[str] = mapped_column(ForeignKey('transactions.id'), nullable=False, index=True)
    amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    reversed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    participant: Mapped[Participant] = relationship(back_populates='reimbursements')


class ImportBatch(Base):
    __tablename__ = 'import_batches'

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    added_count: Mapped[int] = mapped_column(Integer, nullable=False)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False)
    errors_json: Mapped[str] = mapped_column(Text, default='[]', nullable=False)

    user: Mapped[User] = relationship(back_populates='import_batches')


class Account(Base):
    __tablename__ = 'accounts'

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    bank: Mapped[str] = mapped_column(String(100), nullable=False)
    account_type: Mapped[str] = mapped_column(String(30), nullable=False)
    masked_account_number: Mapped[str] = mapped_column(String(32), nullable=False)
    opening_balance_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    user: Mapped[User] = relationship(back_populates='accounts')
    transactions: Mapped[list[Transaction]] = relationship(back_populates='account')
    payment_intents: Mapped[list[PaymentIntent]] = relationship(back_populates='account')
    recurring_schedules: Mapped[list['RecurringSchedule']] = relationship(back_populates='account')


class RecurringSchedule(Base):
    __tablename__ = 'recurring_schedules'

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey('accounts.id'), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    schedule_type: Mapped[str] = mapped_column(String(30), nullable=False)
    amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    frequency: Mapped[str] = mapped_column(String(20), nullable=False)
    next_due_date: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    user: Mapped[User] = relationship(back_populates='recurring_schedules')
    account: Mapped[Account] = relationship(back_populates='recurring_schedules')
    postings: Mapped[list['RecurringPosting']] = relationship(back_populates='schedule')


class RecurringPosting(Base):
    __tablename__ = 'recurring_postings'
    __table_args__ = (UniqueConstraint('schedule_id', 'due_date', name='uq_recurring_posting_due_date'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    schedule_id: Mapped[str] = mapped_column(ForeignKey('recurring_schedules.id'), nullable=False, index=True)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    transaction_id: Mapped[str] = mapped_column(ForeignKey('transactions.id'), nullable=False, unique=True)
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    schedule: Mapped[RecurringSchedule] = relationship(back_populates='postings')


class Budget(Base):
    __tablename__ = 'budgets'
    __table_args__ = (UniqueConstraint('user_id', 'month', 'category', name='uq_budget_user_month_category'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    month: Mapped[str] = mapped_column(String(7), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    limit_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    user: Mapped[User] = relationship(back_populates='budgets')


class SavingsGoal(Base):
    __tablename__ = 'savings_goals'

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    target_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    target_date: Mapped[Optional[date]] = mapped_column(Date)
    color: Mapped[str] = mapped_column(String(20), default='pine', nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    user: Mapped[User] = relationship(back_populates='savings_goals')
    contributions: Mapped[list['GoalContribution']] = relationship(back_populates='goal', cascade='all, delete-orphan')


class GoalContribution(Base):
    __tablename__ = 'goal_contributions'

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    goal_id: Mapped[str] = mapped_column(ForeignKey('savings_goals.id'), nullable=False, index=True)
    amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    contributed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text)

    goal: Mapped[SavingsGoal] = relationship(back_populates='contributions')


class ClassificationRule(Base):
    __tablename__ = 'classification_rules'

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    recipient: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    treatment: Mapped[str] = mapped_column(String(30), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    user: Mapped[User] = relationship(back_populates='classification_rules')


class TransactionEvent(Base):
    __tablename__ = 'transaction_events'

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    transaction_id: Mapped[Optional[str]] = mapped_column(ForeignKey('transactions.id'))
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, default='{}', nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    transaction: Mapped[Optional[Transaction]] = relationship(back_populates='events')


class InsightFeedback(Base):
    __tablename__ = 'insight_feedback'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    insight_key: Mapped[str] = mapped_column(String(160), nullable=False)
    feedback: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ChatSession(Base):
    __tablename__ = 'chat_sessions'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    context_json: Mapped[str] = mapped_column(Text, default='{}', nullable=False)


class ChatMessage(Base):
    __tablename__ = 'chat_messages'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey('chat_sessions.id'), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_name: Mapped[Optional[str]] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ChatActionPreview(Base):
    __tablename__ = 'chat_action_previews'
    __table_args__ = (UniqueConstraint('user_id', 'session_id', 'idempotency_key', name='uq_chat_action_idempotency'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey('chat_sessions.id'), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    action_type: Mapped[str] = mapped_column(String(40), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    preview_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default='pending')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    committed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class FinancialReport(Base):
    __tablename__ = 'financial_reports'
    __table_args__ = (UniqueConstraint('user_id', 'report_type', 'account_id', 'date_from', 'date_to', name='uq_financial_report_job'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    report_type: Mapped[str] = mapped_column(String(20), nullable=False)
    account_id: Mapped[Optional[str]] = mapped_column(ForeignKey('accounts.id'), index=True)
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    content_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class RecurringCandidate(Base):
    __tablename__ = 'recurring_candidates'
    __table_args__ = (UniqueConstraint('user_id', 'candidate_key', name='uq_recurring_candidate_user_key'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    candidate_key: Mapped[str] = mapped_column(String(200), nullable=False)
    merchant: Mapped[str] = mapped_column(String(200), nullable=False)
    amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    interval_days: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
