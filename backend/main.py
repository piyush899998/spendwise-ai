"""FastAPI API for the Phase 3 frontend.

The API is intentionally a thin transport layer. Financial invariants remain
implemented by ``src.engine`` and persistence remains implemented by
``src.repositories``.
"""
from __future__ import annotations

import copy
import json
import os
import re
import secrets
from calendar import monthrange
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load the backend-only project-root configuration before reading CORS or
# provider settings. override=False preserves values explicitly set by the
# process environment (including secrets supplied by a deployment).
load_dotenv(Path(__file__).resolve().parents[1] / '.env', override=False)

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from src.db import create_database
from src.categories import CATEGORIES, category_matches, normalize_category
from src.ai_intelligence import (
    build_report, execute_financial_tool, financial_tool_specs, insight_analysis, recurring_candidates,
    restricted_chat_answer, redacted_transaction, tool_context, _personal_share_amount,
)
from src.ai_repository import AIRepository
from src.ai_provider import AIProviderError, OpenRouterProvider
from src.engine import (
    account_balances,
    add_recurring_schedule,
    add_split,
    allocate,
    allocated_to_share,
    confirm_recurring_schedule,
    contribute_to_goal,
    create_account,
    create_adjustment,
    create_expense,
    create_goal,
    create_income,
    create_intent,
    create_refund,
    create_transaction,
    create_transfer,
    classify,
    categorize_existing_transactions,
    equal_split,
    import_csv,
    incoming,
    metrics,
    new_ledger,
    paise,
    report_text,
    review_transaction,
    reverse_allocation,
    settle_intent,
    update_account,
    update_recurring_schedule,
    update_transaction,
    uid,
    upsert_budget,
)
from src.repositories import LedgerRepository
from src.statement_imports import MAX_UPLOAD_BYTES, StatementParseError, StatementPreview, parse_statement, preview_to_csv
from src.market_data import market_pulse as fetch_market_pulse


USER_ID = 'demo-user'
USER_NAME = 'Demo User'
DEMO_MODE = os.getenv('SPENDWISE_DEMO_MODE', '').lower() == 'true'


class PaymentRequest(BaseModel):
    recipient: str = Field(min_length=1, max_length=200)
    amount_paise: int = Field(gt=0)
    category: str = 'Uncategorized'
    treatment: str = 'expense'
    note: str = ''
    remember: bool = False
    outcome: str = 'succeeded'
    account_id: Optional[str] = None


class ParticipantRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    share_paise: int = Field(gt=0)
    is_self: bool = False


class SplitRequest(BaseModel):
    transaction_id: str
    participants: list[ParticipantRequest]


class ReimbursementRequest(BaseModel):
    split_id: str
    share_id: str
    credit_id: str
    amount_paise: int = Field(gt=0)


class IncomingRequest(BaseModel):
    sender: str = Field(min_length=1, max_length=200)
    amount_paise: int = Field(gt=0)
    reference: Optional[str] = None
    category: str = 'Uncategorized'


class ReviewRequest(BaseModel):
    category: str
    treatment: str


class TransactionUpdateRequest(BaseModel):
    category: Optional[str] = None
    note: Optional[str] = None


class ScheduleUpdateRequest(BaseModel):
    account_id: Optional[str] = None
    name: Optional[str] = Field(default=None, min_length=1, max_length=160)
    schedule_type: Optional[str] = None
    amount_paise: Optional[int] = Field(default=None, gt=0)
    category: Optional[str] = None
    next_due_date: Optional[str] = None
    frequency: Optional[str] = None
    note: Optional[str] = None
    is_active: Optional[bool] = None


class ImportRequest(BaseModel):
    filename: str = 'uploaded.csv'
    text: str


class StatementConfirmRequest(BaseModel):
    account_id: Optional[str] = None
    category_corrections: dict[int, str] = {}


class AccountRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    bank: str = Field(min_length=1, max_length=100)
    account_type: str = 'savings'
    masked_account_number: str = Field(min_length=1, max_length=32)
    opening_balance_paise: int = Field(ge=0)


class AccountUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    bank: Optional[str] = Field(default=None, min_length=1, max_length=100)
    account_type: Optional[str] = None
    masked_account_number: Optional[str] = Field(default=None, min_length=1, max_length=32)
    is_active: Optional[bool] = None


class LedgerEntryRequest(BaseModel):
    account_id: str
    amount_paise: int = Field(gt=0)
    transaction_type: str
    direction: Optional[str] = None
    category: str = 'Other'
    recipient: str = 'Manual entry'
    note: str = ''
    reference: Optional[str] = None


class TransferRequest(BaseModel):
    from_account_id: str
    to_account_id: str
    amount_paise: int = Field(gt=0)
    note: str = ''
    reference: Optional[str] = None


class ScheduleRequest(BaseModel):
    account_id: str
    name: str = Field(min_length=1, max_length=160)
    schedule_type: str
    amount_paise: int = Field(gt=0)
    category: str
    next_due_date: str
    frequency: str = 'monthly'
    note: str = ''


class BudgetRequest(BaseModel):
    month: str
    category: str
    limit_paise: int = Field(gt=0)


class GoalRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    target_paise: int = Field(gt=0)
    target_date: Optional[str] = None
    color: str = 'pine'


class GoalContributionRequest(BaseModel):
    amount_paise: int = Field(gt=0)
    note: str = ''


class DemoResetRequest(BaseModel):
    seed: bool = True


class CategorizationRequest(BaseModel):
    category: str
    treatment: str
    apply_to_similar: bool = False


class RuleRequest(BaseModel):
    recipient: str = Field(min_length=1, max_length=200)
    category: str
    treatment: str


class InsightFeedbackRequest(BaseModel):
    feedback: str


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    session_id: Optional[str] = None


class ChatSelectionRequest(BaseModel):
    session_id: str
    transaction_ids: list[str] = Field(default_factory=list, max_length=1000)
    select_all_matching: bool = False


class ActionParticipantRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    is_self: bool = False
    share_paise: Optional[int] = Field(default=None, ge=0)
    basis_points: Optional[int] = Field(default=None, ge=0, le=10000)


class SplitActionPreviewRequest(BaseModel):
    session_id: str
    idempotency_key: str = Field(min_length=8, max_length=100)
    split_method: str = 'equal'
    participants: list[ActionParticipantRequest] = Field(min_length=2, max_length=20)


class ConfirmChatActionRequest(BaseModel):
    preview_id: str


class ReportRequest(BaseModel):
    report_type: str = 'monthly'


# Preview tokens contain normalized rows only. Raw bytes and passwords never
# enter this store and are discarded before the endpoint returns.
_statement_previews: dict[str, StatementPreview] = {}


@asynccontextmanager
async def lifespan(_app):
    initialize_demo_database()
    yield


app = FastAPI(title='SpendWise AI API', version='3.0.0', lifespan=lifespan)
_cors_origins = [
    origin.strip() for origin in os.getenv(
        'SPENDWISE_CORS_ORIGINS', 'http://localhost:3000,http://127.0.0.1:3000'
    ).split(',') if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=r'https?://(localhost|127\.0\.0\.1)(:\d+)?',
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


def initialize_demo_database():
    """Seed only an empty, explicitly isolated public-demo database."""
    if not DEMO_MODE:
        return
    ledger = load_ledger()
    if ledger.get('accounts') or ledger.get('transactions'):
        return
    reset_demo(DemoResetRequest(seed=True))


@app.get('/api/categories')
def categories():
    """Expose the same canonical registry used by validation and AI tools."""
    return {'categories': list(CATEGORIES)}


def repository():
    return LedgerRepository(create_database())


def load_ledger():
    return repository().load(USER_ID)


def save_ledger(ledger, consume_action_id=None):
    repository().save(ledger, user_id=USER_ID, display_name=USER_NAME, consume_action_id=consume_action_id)


def ai_repository():
    return AIRepository(create_database())


def _month(value: Optional[str]) -> str:
    return value or datetime.now(timezone.utc).strftime('%Y-%m')


def _month_bounds(month: str):
    try:
        year, month_number = (int(part) for part in month.split('-'))
        start = date(year, month_number, 1)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail='month must use YYYY-MM')
    end = date(year, month_number, monthrange(year, month_number)[1])
    return start, end


def _occurred_date(value):
    return date.fromisoformat(str(value)[:10])


def _filtered_ledger(ledger, start, end, account_id=None):
    period = copy.deepcopy(ledger)
    period['transactions'] = [
        transaction for transaction in ledger['transactions']
        if start <= _occurred_date(transaction['occurred_at']) <= end
        and (account_id is None or transaction.get('account_id') == account_id)
    ]
    transaction_ids = {transaction['id'] for transaction in period['transactions']}
    period['splits'] = [split for split in ledger['splits'] if split['transaction_id'] in transaction_ids]
    share_ids = {participant['id'] for split in period['splits'] for participant in split['participants']}
    credit_ids = {
        allocation['credit_id'] for allocation in ledger['allocations']
        if allocation['share_id'] in share_ids
    }
    period['allocations'] = [
        allocation for allocation in ledger['allocations']
        if allocation['credit_id'] in transaction_ids or allocation['credit_id'] in credit_ids
    ]
    period['intents'] = [intent for intent in ledger['intents'] if intent['id'] in {
        transaction.get('intent_id') for transaction in period['transactions']
    }]
    return period


def _period_ledger(ledger, month, account_id=None):
    start, end = _month_bounds(month)
    return _filtered_ledger(ledger, start, end, account_id)


def _resolve_date_range(period=None, month=None, date_from=None, date_to=None, year=None):
    """Resolve all UI date choices to inclusive UTC calendar dates."""
    today = datetime.now(timezone.utc).date()
    if date_from or date_to:
        if not date_from or not date_to:
            raise HTTPException(status_code=400, detail='date_from and date_to must be provided together')
        try:
            start, end = date.fromisoformat(date_from), date.fromisoformat(date_to)
        except ValueError as error:
            raise HTTPException(status_code=400, detail='Dates must use YYYY-MM-DD') from error
    else:
        choice = (period or ('month' if month else 'this_month')).lower()
        if choice in ('month', 'individual_month'):
            start, end = _month_bounds(month or today.strftime('%Y-%m'))
        elif choice == 'this_month':
            start, end = _month_bounds(today.strftime('%Y-%m'))
        elif choice == 'last_month':
            previous = _previous_month(today.strftime('%Y-%m'))
            start, end = _month_bounds(previous)
        elif choice in ('last_3_months', 'last_6_months'):
            count = 3 if choice == 'last_3_months' else 6
            end = today
            cursor = date(end.year, end.month, 1)
            for _ in range(count - 1):
                cursor = date(cursor.year - (cursor.month == 1), 12 if cursor.month == 1 else cursor.month - 1, 1)
            start = cursor
        elif choice in ('this_year', 'year'):
            selected_year = int(year or (month or str(today.year))[:4])
            start, end = date(selected_year, 1, 1), date(selected_year, 12, 31)
        elif choice == 'all_time':
            start, end = date.min, date.max
        else:
            raise HTTPException(status_code=400, detail='Unknown date period')
    if start > end:
        raise HTTPException(status_code=400, detail='date_from cannot be after date_to')
    return start, end


def _previous_range(start, end):
    length = end - start + timedelta(days=1)
    return start - length, start - timedelta(days=1)


def _months_between(start, end):
    if start == date.min:
        start = end.replace(day=1)
    cursor = date(start.year, start.month, 1)
    last = date(end.year, end.month, 1)
    months = []
    while cursor <= last and len(months) < 36:
        months.append(cursor.strftime('%Y-%m'))
        cursor = date(cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1)
    return months


def _balances_as_of(ledger, end, account_id=None):
    balances = {
        account['id']: account['opening_balance_paise']
        for account in ledger.get('accounts', [])
        if account_id is None or account['id'] == account_id
    }
    for transaction in ledger['transactions']:
        if transaction['status'] != 'succeeded' or transaction.get('account_id') not in balances:
            continue
        if _occurred_date(transaction['occurred_at']) > end:
            continue
        delta = transaction['amount_paise'] if transaction['direction'] == 'credit' else -transaction['amount_paise']
        balances[transaction['account_id']] += delta
    return balances


def _previous_month(month):
    year, month_number = (int(part) for part in month.split('-'))
    if month_number == 1:
        return f'{year - 1:04d}-12'
    return f'{year:04d}-{month_number - 1:02d}'


def _trend_months(month, count=6):
    months = []
    current = month
    for _ in range(count):
        months.append(current)
        current = _previous_month(current)
    return list(reversed(months))


def _transaction_view(transaction):
    classification = transaction.get('classification') or {}
    return {
        'id': transaction['id'],
        'account_id': transaction.get('account_id'),
        'recipient': transaction['recipient'],
        'merchant': transaction.get('merchant'),
        'amount_paise': transaction['amount_paise'],
        'direction': transaction['direction'],
        'status': transaction['status'],
        'source': transaction.get('source'),
        'category': transaction['category'],
        'treatment': transaction['treatment'],
        'transaction_type': transaction.get('transaction_type'),
        'transfer_group_id': transaction.get('transfer_group_id'),
        'note': transaction.get('note', ''),
        'occurred_at': transaction['occurred_at'],
        'needs_review': transaction.get('needs_review', False),
        'classification': classification,
    }


def _split_view(ledger, split):
    transaction = next(t for t in ledger['transactions'] if t['id'] == split['transaction_id'])
    participants = []
    for participant in split['participants']:
        allocated = allocated_to_share(ledger, participant['id'])
        participants.append({
            **participant,
            'allocated_paise': allocated,
            'remaining_paise': participant['share_paise'] - allocated,
        })
    return {
        'id': split['id'],
        'transaction_id': split['transaction_id'],
        'recipient': transaction['recipient'],
        'total_paise': transaction['amount_paise'],
        'participants': participants,
        'allocations': [
            {
                'id': allocation['id'],
                'share_id': allocation['share_id'],
                'credit_id': allocation['credit_id'],
                'amount_paise': allocation['amount_paise'],
                'reversed': allocation.get('reversed', False),
            }
            for allocation in ledger['allocations'] if allocation['split_id'] == split['id']
        ],
    }


def _account_views(ledger):
    balances = account_balances(ledger)
    return [{**account, 'current_balance_paise': balances.get(account['id'], account['opening_balance_paise'])}
            for account in ledger.get('accounts', [])]


def _goal_views(ledger):
    return [{
        **goal,
        'saved_paise': sum(item['amount_paise'] for item in ledger['goal_contributions'] if item['goal_id'] == goal['id']),
    } for goal in ledger.get('goals', [])]


@app.get('/api/health')
def health():
    return {'status': 'ok', 'service': 'spendwise-api'}


@app.get('/api/market/pulse')
def market_pulse(refresh: bool = Query(default=False), gold_range: str = Query(default='1m')):
    """Read-only market snapshot; each upstream failure is isolated."""
    return fetch_market_pulse(force_refresh=refresh, gold_range=gold_range)


@app.get('/api/dashboard')
def dashboard(month: Optional[str] = Query(default=None), account_id: Optional[str] = Query(default=None),
              period: Optional[str] = Query(default=None), year: Optional[int] = Query(default=None),
              date_from: Optional[str] = Query(default=None), date_to: Optional[str] = Query(default=None)):
    start, end = _resolve_date_range(period, month, date_from, date_to, year)
    ledger = load_ledger()
    valid_account_ids = {account['id'] for account in ledger.get('accounts', [])}
    if account_id and account_id not in valid_account_ids:
        raise HTTPException(status_code=404, detail='Account not found')
    current = _filtered_ledger(ledger, start, end, account_id)
    previous_start, previous_end = _previous_range(start, end)
    previous = _filtered_ledger(ledger, previous_start, previous_end, account_id)
    include_transfers = account_id is not None
    current_metrics = metrics(current, include_transfers=include_transfers)
    previous_metrics = metrics(previous, include_transfers=include_transfers)
    selected_month = month or start.strftime('%Y-%m')
    selected_months = set(_months_between(start, end))
    month_budgets = [budget for budget in ledger.get('budgets', []) if budget['month'] in selected_months]
    budget_limit = sum(budget['limit_paise'] for budget in month_budgets)
    budget_spent = sum(current_metrics['by_category'].get(budget['category'], 0) for budget in month_budgets)
    transactions = sorted(current['transactions'], key=lambda t: t['occurred_at'], reverse=True)
    merchant_breakdown = {}
    for transaction in transactions:
        if transaction.get('direction') != 'debit' or transaction.get('transaction_type') in ('transfer', 'refund', 'reimbursement', 'adjustment'):
            continue
        merchant = transaction.get('recipient') or 'Unknown merchant'
        merchant_breakdown[merchant] = merchant_breakdown.get(merchant, 0) + transaction['amount_paise']
    confidence = {}
    for transaction in transactions:
        source = (transaction.get('classification') or {}).get('source', 'legacy')
        confidence[source] = confidence.get(source, 0) + 1
    trend = []
    trend_months = _months_between(start, end)
    if len(trend_months) > 12:
        trend_months = trend_months[-12:]
    for trend_month in trend_months:
        trend_metrics = metrics(_period_ledger(ledger, trend_month, account_id), include_transfers=include_transfers)
        trend.append({
            'month': trend_month,
            'income_received': trend_metrics.get('income_received', trend_metrics.get('credits_received', 0)),
            'personal_expense': trend_metrics['personal_expense'],
            'gross_paid': trend_metrics['gross_paid'],
            'outstanding': trend_metrics['outstanding'],
        })
    daily_spending = {}
    for transaction in transactions:
        if transaction.get('direction') != 'debit' or transaction.get('transaction_type') in ('transfer', 'refund', 'reimbursement', 'adjustment'):
            continue
        amount = _personal_share_amount(current, transaction)
        if amount:
            day = transaction['occurred_at'][:10]
            daily_spending[day] = daily_spending.get(day, 0) + amount
    return {
        'month': selected_month,
        'profile': {'display_name': USER_NAME},
        'filters': {'account_id': account_id, 'date_from': start.isoformat(), 'date_to': end.isoformat(), 'period': period or ('month' if month else 'this_month')},
        'metrics': current_metrics,
        'budget_utilization_percent': round(budget_spent / budget_limit * 100, 2) if budget_limit else 0,
        'previous_metrics': previous_metrics,
        'accounts': [
            {**account, 'balance_as_of_end_paise': _balances_as_of(ledger, end, account['id']).get(account['id'], account['opening_balance_paise'])}
            for account in _account_views(ledger)
        ],
        'budgets': [
            {**budget, 'spent_paise': current_metrics['by_category'].get(budget['category'], 0),
             'remaining_paise': budget['limit_paise'] - current_metrics['by_category'].get(budget['category'], 0),
             'overspent': current_metrics['by_category'].get(budget['category'], 0) > budget['limit_paise']}
            for budget in ledger.get('budgets', []) if budget['month'] == selected_month
        ],
        'goals': _goal_views(ledger),
        'recurring_schedules': ledger.get('recurring_schedules', []),
        'category_breakdown': [
            {'category': category, 'amount_paise': amount}
            for category, amount in sorted(current_metrics['by_category'].items(), key=lambda pair: pair[1], reverse=True)
            if amount
        ],
        'merchant_breakdown': [
            {'merchant': merchant, 'amount_paise': amount}
            for merchant, amount in sorted(merchant_breakdown.items(), key=lambda pair: pair[1], reverse=True)
        ],
        'transactions': [_transaction_view(transaction) for transaction in transactions],
        'classification_sources': confidence,
        'review_transactions': [
            _transaction_view(transaction) for transaction in transactions if transaction.get('needs_review')
        ],
        'trend': trend,
        'daily_spending': [
            {'date': day, 'amount_paise': amount}
            for day, amount in sorted(daily_spending.items())
        ],
        'report_text': report_text(current),
    }


@app.get('/api/splits')
def splits():
    ledger = load_ledger()
    return {'splits': [_split_view(ledger, split) for split in ledger['splits']]}


@app.get('/api/accounts')
def accounts():
    return {'accounts': _account_views(load_ledger())}


@app.get('/api/accounts/{account_id}/dashboard')
def account_dashboard(account_id: str, month: Optional[str] = Query(default=None),
                      period: Optional[str] = Query(default=None), year: Optional[int] = Query(default=None),
                      date_from: Optional[str] = Query(default=None), date_to: Optional[str] = Query(default=None)):
    """Account-detail analytics use the exact same filtered dashboard path."""
    return dashboard(month=month, account_id=account_id, period=period, year=year,
                     date_from=date_from, date_to=date_to)


@app.post('/api/accounts')
def create_account_endpoint(request: AccountRequest):
    ledger = load_ledger()
    try:
        account = create_account(ledger, request.name, request.bank, request.account_type,
                                 request.masked_account_number, request.opening_balance_paise)
        save_ledger(ledger)
        return {'account': next(item for item in _account_views(ledger) if item['id'] == account['id'])}
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.patch('/api/accounts/{account_id}')
def update_account_endpoint(account_id: str, request: AccountUpdateRequest):
    ledger = load_ledger()
    try:
        changes = request.model_dump(exclude_none=True)
        account = update_account(ledger, account_id, **changes)
        save_ledger(ledger)
        return {'account': next(item for item in _account_views(ledger) if item['id'] == account['id'])}
    except (ValueError, StopIteration) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get('/api/transactions')
def transactions(month: Optional[str] = None, account_id: Optional[str] = None,
                 transaction_type: Optional[str] = None, category: Optional[str] = None,
                 q: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None,
                 period: Optional[str] = None, year: Optional[int] = None):
    ledger = load_ledger()
    if not any((month, period, date_from, date_to, year)):
        selected = copy.deepcopy(ledger)
        if account_id:
            selected['transactions'] = [item for item in selected['transactions'] if item.get('account_id') == account_id]
    else:
        start, end = _resolve_date_range(period, month, date_from, date_to, year)
        selected = _filtered_ledger(ledger, start, end, account_id)
    result = selected['transactions']
    if transaction_type:
        result = [item for item in result if item.get('transaction_type') == transaction_type]
    if category:
        canonical = normalize_category(category)
        if canonical is None:
            raise HTTPException(status_code=400, detail='Unsupported category')
        result = [item for item in result if category_matches(item.get('category'), canonical)]
    if q:
        needle = q.casefold()
        result = [item for item in result if needle in f"{item['recipient']} {item['category']} {item.get('note', '')}".casefold()]
    return {'transactions': [_transaction_view(item) for item in sorted(result, key=lambda item: item['occurred_at'], reverse=True)]}


@app.get('/api/transactions/{transaction_id}')
def transaction_detail(transaction_id: str):
    ledger = load_ledger()
    try:
        transaction = next(item for item in ledger['transactions'] if item['id'] == transaction_id)
    except StopIteration as error:
        raise HTTPException(status_code=404, detail='Transaction not found') from error
    return {'transaction': _transaction_view(transaction)}


@app.patch('/api/transactions/{transaction_id}')
def edit_transaction(transaction_id: str, request: TransactionUpdateRequest):
    ledger = load_ledger()
    try:
        transaction = update_transaction(ledger, transaction_id, **request.model_dump(exclude_unset=True))
        save_ledger(ledger)
        return {'transaction': _transaction_view(transaction)}
    except (ValueError, StopIteration) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post('/api/transactions')
def create_ledger_entry(request: LedgerEntryRequest):
    ledger = load_ledger()
    try:
        direction = request.direction or ('credit' if request.transaction_type in ('income', 'refund') else 'debit')
        if request.transaction_type == 'income':
            transaction = create_income(ledger, request.account_id, request.amount_paise, request.category, request.recipient, request.note, request.reference)
        elif request.transaction_type == 'expense':
            transaction = create_expense(ledger, request.account_id, request.amount_paise, request.category, request.recipient, request.note, request.reference)
        elif request.transaction_type == 'refund':
            transaction = create_refund(ledger, request.account_id, request.amount_paise, request.category, request.recipient, request.note, request.reference)
        elif request.transaction_type == 'adjustment':
            transaction = create_adjustment(ledger, request.account_id, request.amount_paise, direction, request.note, request.reference)
        else:
            transaction = create_transaction(ledger, request.account_id, request.amount_paise, direction, request.transaction_type, request.category, request.recipient, request.note, request.reference)
        save_ledger(ledger)
        return {'transaction': _transaction_view(transaction)}
    except (ValueError, StopIteration) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post('/api/transfers')
def create_transfer_endpoint(request: TransferRequest):
    ledger = load_ledger()
    try:
        transfer = create_transfer(ledger, request.from_account_id, request.to_account_id,
                                   request.amount_paise, request.note, request.reference)
        save_ledger(ledger)
        return {'transfer': transfer}
    except (ValueError, StopIteration) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get('/api/recurring')
def recurring():
    return {'schedules': load_ledger().get('recurring_schedules', [])}


@app.post('/api/recurring')
def create_recurring(request: ScheduleRequest):
    ledger = load_ledger()
    try:
        schedule = add_recurring_schedule(ledger, request.account_id, request.name, request.schedule_type,
                                          request.amount_paise, request.category, request.next_due_date,
                                          request.frequency, request.note)
        save_ledger(ledger)
        return {'schedule': schedule}
    except (ValueError, StopIteration) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.patch('/api/recurring/{schedule_id}')
def edit_recurring(schedule_id: str, request: ScheduleUpdateRequest):
    ledger = load_ledger()
    try:
        schedule = update_recurring_schedule(ledger, schedule_id, **request.model_dump(exclude_unset=True))
        save_ledger(ledger)
        return {'schedule': schedule}
    except (ValueError, StopIteration) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post('/api/recurring/{schedule_id}/confirm')
def confirm_recurring(schedule_id: str, due_date: Optional[str] = None):
    ledger = load_ledger()
    try:
        transaction = confirm_recurring_schedule(ledger, schedule_id, due_date)
        save_ledger(ledger)
        return {'transaction': _transaction_view(transaction)}
    except (ValueError, StopIteration) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get('/api/budgets')
def budgets(month: Optional[str] = None, account_id: Optional[str] = None,
            period: Optional[str] = None, year: Optional[int] = None,
            date_from: Optional[str] = None, date_to: Optional[str] = None):
    ledger = load_ledger()
    start, end = _resolve_date_range(period, month, date_from, date_to, year)
    selected_months = set(_months_between(start, end))
    current_metrics = metrics(_filtered_ledger(ledger, start, end, account_id), include_transfers=account_id is not None)
    return {'budgets': [
        {**item, 'spent_paise': current_metrics['by_category'].get(item['category'], 0),
         'remaining_paise': item['limit_paise'] - current_metrics['by_category'].get(item['category'], 0),
         'overspent': current_metrics['by_category'].get(item['category'], 0) > item['limit_paise']}
        for item in ledger.get('budgets', []) if item['month'] in selected_months
    ]}


@app.post('/api/budgets')
def create_budget(request: BudgetRequest):
    ledger = load_ledger()
    try:
        budget = upsert_budget(ledger, request.month, request.category, request.limit_paise)
        save_ledger(ledger)
        return {'budget': budget}
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get('/api/goals')
def goals():
    return {'goals': _goal_views(load_ledger())}


@app.post('/api/goals')
def create_goal_endpoint(request: GoalRequest):
    ledger = load_ledger()
    try:
        goal = create_goal(ledger, request.name, request.target_paise, request.target_date, request.color)
        save_ledger(ledger)
        return {'goal': next(item for item in _goal_views(ledger) if item['id'] == goal['id'])}
    except (ValueError, StopIteration) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post('/api/goals/{goal_id}/contributions')
def contribute_goal(goal_id: str, request: GoalContributionRequest):
    ledger = load_ledger()
    try:
        contribution = contribute_to_goal(ledger, goal_id, request.amount_paise, request.note)
        save_ledger(ledger)
        return {'contribution': contribution, 'goal': next(item for item in _goal_views(ledger) if item['id'] == goal_id)}
    except (ValueError, StopIteration) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post('/api/payments')
def create_payment(request: PaymentRequest):
    ledger = load_ledger()
    try:
        intent = create_intent(
            ledger, request.recipient, request.amount_paise, request.category,
            request.treatment, request.note, request.remember,
            request.account_id,
        )
        transaction = settle_intent(ledger, intent['id'], request.outcome)
        save_ledger(ledger)
        return {'intent': intent, 'transaction': transaction}
    except (ValueError, StopIteration) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post('/api/splits')
def create_split(request: SplitRequest):
    ledger = load_ledger()
    try:
        split = add_split(ledger, request.transaction_id, [participant.model_dump() for participant in request.participants])
        save_ledger(ledger)
        return {'split': _split_view(ledger, split)}
    except (ValueError, StopIteration) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post('/api/reimbursements')
def create_reimbursement(request: ReimbursementRequest):
    ledger = load_ledger()
    try:
        allocation = allocate(
            ledger, request.split_id, request.share_id, request.credit_id, request.amount_paise,
        )
        save_ledger(ledger)
        return {'allocation': allocation}
    except (ValueError, StopIteration) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post('/api/incoming')
def create_incoming(request: IncomingRequest):
    ledger = load_ledger()
    try:
        transaction = incoming(
            ledger, request.sender, request.amount_paise,
            request.reference, request.category,
        )
        save_ledger(ledger)
        return {'transaction': _transaction_view(transaction)}
    except (ValueError, StopIteration) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post('/api/reimbursements/{allocation_id}/reverse')
def reverse_reimbursement(allocation_id: str):
    ledger = load_ledger()
    try:
        reverse_allocation(ledger, allocation_id)
        save_ledger(ledger)
        return {'status': 'reversed', 'allocation_id': allocation_id}
    except (ValueError, StopIteration) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post('/api/transactions/{transaction_id}/review')
def review(transaction_id: str, request: ReviewRequest):
    ledger = load_ledger()
    try:
        review_transaction(ledger, transaction_id, request.category, request.treatment)
        save_ledger(ledger)
        transaction = next(item for item in ledger['transactions'] if item['id'] == transaction_id)
        return {'transaction': _transaction_view(transaction)}
    except (ValueError, StopIteration) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


def _ai_scope(period=None, month=None, year=None, date_from=None, date_to=None, account_id=None):
    start, end = _resolve_date_range(period, month, date_from, date_to, year)
    ledger = load_ledger()
    account_ids = {item['id'] for item in ledger.get('accounts', [])}
    if account_id and account_id not in account_ids:
        raise HTTPException(status_code=404, detail='Account not found')
    return ledger, start, end, account_id


def _chat_query_transactions(ledger, query):
    """Resolve stored conversational filters against the current user ledger."""
    try:
        start = date.fromisoformat(query['date_from'])
        end = date.fromisoformat(query['date_to'])
    except (KeyError, TypeError, ValueError):
        return []
    if start > end:
        return []
    rows = [t for t in ledger.get('transactions', []) if t.get('status') == 'succeeded'
            and start <= date.fromisoformat(str(t.get('occurred_at'))[:10]) <= end
            and (query.get('account_id') is None or t.get('account_id') == query.get('account_id'))]
    category = query.get('category')
    if category:
        rows = [t for t in rows if category_matches(t.get('category'), category)]
    if query.get('direction'):
        rows = [t for t in rows if t.get('direction') == query.get('direction')]
    if query.get('transaction_type'):
        rows = [t for t in rows if t.get('transaction_type') == query.get('transaction_type')]
    merchant = str(query.get('merchant') or '').casefold()
    if merchant:
        rows = [t for t in rows if merchant in str(t.get('merchant') or '').casefold() or merchant in str(t.get('recipient') or '').casefold()]
    return rows


def _name_key(value):
    return ' '.join(value.casefold().split())


def _parse_conversational_participants(question):
    """Parse a small, explicit participant list; ambiguous text returns no plan."""
    text = str(question or '').strip()
    match = re.search(
        r'\bparticipants?\s*(?:are|:)\s*(.+?)\s*,?\s+(?:divide|split|share)\b',
        text, re.IGNORECASE,
    )
    if match:
        names_text = match.group(1)
    else:
        match = re.search(r'\b(?:between|among)\s+(.+?)(?:\s+(?:equally|evenly)\b|[.!?]|$)', text, re.IGNORECASE)
        if match:
            names_text = match.group(1)
        else:
            verb = re.search(r'\b(?:divide|split|share)\b', text, re.IGNORECASE)
            names_text = text[:verb.start()].strip(' ,') if verb and verb.start() else ''
    names_text = re.sub(r'\b(?:participants?|equally|evenly|please|the expense|food expense)\b', '', names_text, flags=re.IGNORECASE)
    names = [item.strip(' .!?') for item in re.split(r',|\s+and\s+|\s*&\s*', names_text) if item.strip(' ,.!?')]
    if not names:
        return []
    participants = []
    self_names = {'me', 'myself', 'you', 'i'}
    for name in names:
        if len(name) > 100:
            return []
        is_self = _name_key(name) in self_names
        normalized = 'You' if is_self else ' '.join(part.capitalize() for part in name.split())
        participants.append({'name': normalized, 'is_self': is_self})
    if sum(item['is_self'] for item in participants) == 0:
        participants.insert(0, {'name': 'You', 'is_self': True})
    if sum(item['is_self'] for item in participants) != 1:
        return []
    return participants


def _split_shares_for_transactions(transactions, participants, method):
    """Create integer-paise shares and ensure both row and participant totals reconcile."""
    total = sum(t['amount_paise'] for t in transactions)
    if method == 'equal':
        count = len(participants)
        base, remainder = divmod(total, count)
        targets = [base] * count
        # Give indivisible paise to non-self participants first. This avoids
        # biasing the payer upward and is stable across the participant order.
        order = [index for index, item in enumerate(participants) if not item['is_self']]
        order += [index for index, item in enumerate(participants) if item['is_self']]
        for index in order[:remainder]:
            targets[index] += 1
        return _allocate_targets_by_transaction(transactions, participants, targets)
    targets = []
    if method == 'custom':
        targets = [p.get('share_paise') for p in participants]
        if any(value is None for value in targets) or sum(targets) != total:
            raise ValueError('Custom participant shares must sum exactly to the selected total')
    elif method == 'percentage':
        points = [p.get('basis_points') for p in participants]
        if any(value is None for value in points) or sum(points) != 10000:
            raise ValueError('Percentage shares must total exactly 100%')
        bases = [(total * value) // 10000 for value in points]
        remainder = total - sum(bases)
        ranked = sorted(range(len(points)), key=lambda index: (-(total * points[index] % 10000), index))
        for index in ranked[:remainder]:
            bases[index] += 1
        targets = bases
    else:
        raise ValueError('Split method must be equal, custom, or percentage')
    if any(value < 0 for value in targets):
        raise ValueError('Participant shares cannot be negative')
    # Intersect cumulative integer-paise ranges: transaction rows remain exact,
    # while aggregate participant amounts exactly match their requested shares.
    return _allocate_targets_by_transaction(transactions, participants, targets)


def _allocate_targets_by_transaction(transactions, participants, targets):
    result = {}
    tx_start = 0
    for transaction in transactions:
        tx_end = tx_start + transaction['amount_paise']
        participant_start = 0
        shares = []
        for index, target in enumerate(targets):
            participant_end = participant_start + target
            amount = max(0, min(tx_end, participant_end) - max(tx_start, participant_start))
            shares.append({'name': participants[index]['name'], 'share_paise': amount, 'is_self': participants[index]['is_self']})
            participant_start = participant_end
        if sum(item['share_paise'] for item in shares) != transaction['amount_paise']:
            raise ValueError('Split allocation did not reconcile')
        result[transaction['id']] = shares
        tx_start = tx_end
    return result


@app.get('/api/ai/categorization/review')
def categorization_review(account_id: Optional[str] = None, period: Optional[str] = None,
                          month: Optional[str] = None, year: Optional[int] = None,
                          date_from: Optional[str] = None, date_to: Optional[str] = None):
    ledger, start, end, account_id = _ai_scope(period, month, year, date_from, date_to, account_id)
    rows = [item for item in _filtered_ledger(ledger, start, end, account_id)['transactions'] if item.get('needs_review') or not item.get('classification', {}).get('confirmed', False)]
    return {'transactions': [_transaction_view(item) for item in rows], 'count': len(rows), 'period': {'date_from': start.isoformat(), 'date_to': end.isoformat(), 'account_id': account_id}}


@app.post('/api/ai/categorization/bulk')
def categorize_existing(account_id: Optional[str] = None, period: Optional[str] = None,
                        month: Optional[str] = None, year: Optional[int] = None,
                        date_from: Optional[str] = None, date_to: Optional[str] = None):
    """Backfill the complete selected scope, never only the visible page."""
    ledger, start, end, account_id = _ai_scope(period or 'all_time', month, year, date_from, date_to, account_id)
    # Bulk backfill is deterministic and local. External model suggestions are
    # available only through the explicit per-transaction suggestion action.
    summary = categorize_existing_transactions(ledger, start=start, end=end, account_id=account_id)
    save_ledger(ledger)
    return {**summary, 'period': {'date_from': start.isoformat(), 'date_to': end.isoformat(), 'account_id': account_id}}


@app.post('/api/ai/categorization-suggestion/{transaction_id}')
def categorization_suggestion(transaction_id: str):
    ledger = load_ledger()
    try:
        transaction = next(item for item in ledger['transactions'] if item['id'] == transaction_id)
    except StopIteration as error:
        raise HTTPException(status_code=404, detail='Transaction not found') from error
    provider = OpenRouterProvider()
    model = provider.classify(redacted_transaction(transaction))
    return {'suggestion': None if model is None else {'category': model.category, 'merchant': model.merchant, 'intent': model.intent, 'confidence': model.confidence, 'explanation': model.explanation}, 'external_ai_used': model is not None, 'provider_diagnostic': provider.last_error.diagnostic() if provider.last_error else None}


@app.post('/api/ai/categorization/{transaction_id}')
def categorize_transaction(transaction_id: str, request: CategorizationRequest):
    ledger = load_ledger()
    try:
        transaction = next(item for item in ledger['transactions'] if item['id'] == transaction_id)
        category = normalize_category(request.category)
        if category is None:
            raise ValueError('Invalid category')
        review_transaction(ledger, transaction_id, category, request.treatment)
        if request.apply_to_similar:
            merchant = (transaction.get('merchant') or transaction.get('recipient', '')).casefold()
            if merchant:
                ledger['rules'].append({'id': uid(), 'recipient': merchant, 'category': category, 'treatment': request.treatment, 'enabled': True})
        save_ledger(ledger)
        return {'transaction': _transaction_view(next(item for item in ledger['transactions'] if item['id'] == transaction_id)), 'applied_to_similar': request.apply_to_similar}
    except (ValueError, StopIteration) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get('/api/ai/rules')
def classification_rules():
    return {'rules': load_ledger().get('rules', [])}


@app.post('/api/ai/rules')
def create_classification_rule(request: RuleRequest):
    ledger = load_ledger()
    try:
        category = normalize_category(request.category)
        if category is None:
            raise ValueError('Invalid category')
        rule = {'id': uid(), 'recipient': request.recipient.strip().casefold(), 'category': category, 'treatment': request.treatment, 'enabled': True}
        ledger['rules'].append(rule); save_ledger(ledger)
        return {'rule': rule}
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.delete('/api/ai/rules/{rule_id}')
def delete_classification_rule(rule_id: str):
    ledger = load_ledger()
    before = len(ledger.get('rules', [])); ledger['rules'] = [rule for rule in ledger.get('rules', []) if rule.get('id') != rule_id]
    if len(ledger['rules']) == before:
        raise HTTPException(status_code=404, detail='Rule not found')
    save_ledger(ledger)
    return {'deleted': rule_id}


@app.get('/api/ai/insights')
def ai_insights(account_id: Optional[str] = None, period: Optional[str] = None,
                month: Optional[str] = None, year: Optional[int] = None,
                date_from: Optional[str] = None, date_to: Optional[str] = None):
    ledger, start, end, account_id = _ai_scope(period, month, year, date_from, date_to, account_id)
    analysis = insight_analysis(ledger, start, end, account_id)
    candidates = ai_repository().candidates(USER_ID, recurring_candidates(ledger, start, end, account_id))
    return {**analysis, 'recurring_candidates': candidates}


@app.post('/api/ai/insights/{insight_key}/feedback')
def insight_feedback(insight_key: str, request: InsightFeedbackRequest):
    if request.feedback not in {'useful', 'not_useful', 'dismissed'}:
        raise HTTPException(status_code=400, detail='Invalid insight feedback')
    return ai_repository().feedback(USER_ID, insight_key, request.feedback)


@app.get('/api/ai/recurring-candidates')
def recurring_candidate_list(account_id: Optional[str] = None, period: Optional[str] = None,
                             month: Optional[str] = None, year: Optional[int] = None,
                             date_from: Optional[str] = None, date_to: Optional[str] = None):
    ledger, start, end, account_id = _ai_scope(period, month, year, date_from, date_to, account_id)
    return {'candidates': ai_repository().candidates(USER_ID, recurring_candidates(ledger, start, end, account_id))}


@app.post('/api/ai/recurring-candidates/{candidate_id}')
def update_recurring_candidate(candidate_id: str, status: str):
    try:
        return {'candidate': ai_repository().update_candidate(USER_ID, candidate_id, status)}
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post('/api/ai/chat')
def ask_spendwise(request: ChatRequest, account_id: Optional[str] = None, period: Optional[str] = None,
                  month: Optional[str] = None, year: Optional[int] = None,
                  date_from: Optional[str] = None, date_to: Optional[str] = None):
    ledger, start, end, account_id = _ai_scope(period, month, year, date_from, date_to, account_id)
    repository = ai_repository()
    history = []
    prior_context = {}
    if request.session_id:
        try:
            conversation = repository.conversation(USER_ID, request.session_id)
            history = conversation['messages']
            prior_context = conversation['context']
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
    context = tool_context(ledger, start, end, account_id)
    provider = OpenRouterProvider()
    result = None
    provider_status = 'local_fallback'
    ai_notice = 'External AI is unavailable or not enabled; this answer was calculated locally.'
    provider_diagnostic = None
    last_query = prior_context.get('last_query') or {}
    action_preview_result = None
    normalized_question = request.question.casefold()
    is_split_instruction = (
        any(re.search(rf'\b{verb}\b', normalized_question) for verb in ('split', 'divide', 'share'))
        and any(word in normalized_question for word in ('between', 'among', 'equally', 'evenly', 'participants'))
    )
    if is_split_instruction and request.session_id:
        participant_items = _parse_conversational_participants(request.question)
        parsed_names = [item['name'] for item in participant_items]
        if not last_query or not parsed_names:
            result = {'tool': None, 'account': context['account'], 'account_id': account_id,
                'period': {'date_from': start.isoformat(), 'date_to': end.isoformat()},
                'answer': 'Show or select transactions and list the participants clearly before requesting a split.',
                'provider_status': 'local_fallback', 'ai_notice': 'No changes were made.', 'provider_diagnostic': None}
        else:
            matched_rows = _chat_query_transactions(ledger, last_query)
            previous_result_ids = prior_context.get('last_result_transaction_ids') or []
            selected_ids = [item for item in prior_context.get('selected_transaction_ids', []) if any(row['id'] == item for row in matched_rows)]
            if not selected_ids:
                selected_ids = [item for item in previous_result_ids if any(row['id'] == item for row in matched_rows)]
            if not selected_ids:
                selected_ids = [row['id'] for row in matched_rows]
            if sum(item['is_self'] for item in participant_items) != 1:
                result = {'tool': None, 'account': last_query.get('account', context['account']), 'account_id': last_query.get('account_id'),
                    'period': {'date_from': last_query.get('date_from', start.isoformat()), 'date_to': last_query.get('date_to', end.isoformat())},
                    'answer': 'Please include exactly one “me” participant and name each other participant once. No preview was created.',
                    'provider_status': 'local_fallback', 'ai_notice': 'No changes were made.', 'provider_diagnostic': None}
            else:
                try:
                    repository.update_chat_context(USER_ID, request.session_id, {'selected_transaction_ids': selected_ids})
                    preview_result = preview_chat_split(SplitActionPreviewRequest(
                        session_id=request.session_id, idempotency_key=secrets.token_urlsafe(18), split_method='equal',
                        participants=[ActionParticipantRequest(**item) for item in participant_items],
                    ))
                    action_preview_result = preview_result['action']
                    result = {'tool': 'create_shared_expense_preview', 'account': last_query.get('account', context['account']),
                        'account_id': last_query.get('account_id'),
                        'period': {'date_from': last_query['date_from'], 'date_to': last_query['date_to']},
                        'answer': f"I prepared an equal split preview for {action_preview_result['preview']['transaction_count']} transaction(s). Review the amounts below and explicitly confirm to save it.",
                        'transactions': matched_rows[:20],
                        'data': {'transactions': matched_rows[:20], 'total_count': len(matched_rows), 'offset': 0,
                            'limit': 20, 'has_more': len(matched_rows) > 20,
                            'gross_debit_paise': sum(t['amount_paise'] for t in matched_rows if t.get('direction') == 'debit'),
                            'confirmed_personal_expense_paise': sum(_personal_share_amount(ledger, t) for t in matched_rows)},
                        'provider_status': 'local_fallback', 'ai_notice': 'This preview has not changed your ledger. Explicit confirmation is required.', 'provider_diagnostic': None}
                except HTTPException as error:
                    result = {'tool': None, 'account': last_query.get('account', context['account']), 'account_id': last_query.get('account_id'),
                        'period': {'date_from': last_query.get('date_from', start.isoformat()), 'date_to': last_query.get('date_to', end.isoformat())},
                        'answer': f"I could not prepare the split preview: {error.detail}", 'provider_status': 'local_fallback',
                        'ai_notice': 'No changes were made.', 'provider_diagnostic': None}
    show_followup = any(phrase in request.question.casefold() for phrase in (
        'show me all those', 'show those transactions', 'show them', 'transactions behind that',
        'show me the transactions', 'list those transactions', 'show all those transactions',
    ))
    if result is None and show_followup and last_query:
        # Contextual drill-down is resolved locally from the stored structured
        # query. It does not ask the model to infer or broaden the result set.
        retrieval_ledger = ledger
        matching = _chat_query_transactions(retrieval_ledger, last_query)
        matching.sort(key=lambda item: (str(item.get('occurred_at', '')), item.get('id', '')), reverse=True)
        page_size = 20
        rows = matching[:page_size]
        result = {
            'tool': 'get_transactions', 'account': last_query.get('account', 'All Accounts'),
            'account_id': last_query.get('account_id'),
            'period': {'date_from': last_query['date_from'], 'date_to': last_query['date_to']},
            'data': {'transactions': rows, 'total_count': len(matching), 'offset': 0, 'limit': page_size,
                     'has_more': len(matching) > page_size,
                     'gross_debit_paise': sum(t['amount_paise'] for t in matching if t.get('direction') == 'debit'),
                     'confirmed_personal_expense_paise': sum(_personal_share_amount(ledger, t) for t in matching),
                     'sum_semantics': 'Gross debits and confirmed personal expense are separate totals.'},
            'filters': {key: last_query.get(key) for key in ('category', 'merchant', 'direction', 'transaction_type')},
            'transactions': rows,
            'answer': f"Showing {len(rows)} of {len(matching)} transactions matching the previous query. Gross debits and confirmed personal expense are reported separately.",
            'provider_status': 'local_fallback', 'ai_notice': 'Retrieved from the exact filters saved with the previous answer.',
            'provider_diagnostic': None,
        }
        provider_status = 'local_fallback'
        ai_notice = result['ai_notice']
    month_names = ('january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december')
    mentioned_month = next((word for word in month_names if re.search(rf'\b{word}\b', normalized_question)), None)
    year_match = re.search(r'\b(20\d{2})\b', request.question)
    if result is None and mentioned_month and last_query:
        year_number = int(year_match.group(1)) if year_match else date.fromisoformat(last_query['date_from']).year
        month_number = month_names.index(mentioned_month) + 1
        follow_start = date(year_number, month_number, 1)
        follow_end = date(year_number, month_number, monthrange(year_number, month_number)[1])
        previous_category = last_query.get('category')
        if previous_category:
            result = execute_financial_tool(ledger, 'get_category_breakdown', {'category': previous_category}, follow_start, follow_end, last_query.get('account_id'))
            amount = sum(row['amount_paise'] for row in result.get('data', {}).get('categories', []))
            result['answer'] = f"Confirmed personal spending for {previous_category} from {follow_start.isoformat()} to {follow_end.isoformat()} was ₹{amount / 100:,.2f}."
        else:
            result = restricted_chat_answer(ledger, 'How much did I spend?', follow_start, follow_end, last_query.get('account_id'))
        result.update({'provider_status': 'local_fallback', 'ai_notice': 'The follow-up retained the previous account scope and used the year from that result.', 'provider_diagnostic': None})
        provider_status = 'local_fallback'
        ai_notice = result['ai_notice']
    if result is None and provider.enabled:
        try:
            selection = provider.choose_tool(request.question, context, financial_tool_specs(ledger), history)
            verified = execute_financial_tool(ledger, selection.tool_name, selection.arguments, start, end, account_id)
            summary_result = verified
            if selection.tool_name == 'get_transactions':
                summary_result = {**verified, 'transactions': None,
                    'data': {key: value for key, value in verified.get('data', {}).items() if key != 'transactions'}}
            result = {**verified, 'answer': provider.summarize(request.question, context, selection.tool_name, summary_result), 'provider_status': 'available', 'ai_notice': 'Answer summarized from verified backend tool results.'}
            result['transactions'] = verified.get('transactions', verified.get('data', {}).get('transactions', []))
            provider_status = 'available'
            ai_notice = result['ai_notice']
        except (AIProviderError, ValueError) as error:
            provider_status = 'unavailable'
            provider_diagnostic = error.diagnostic() if isinstance(error, AIProviderError) else {'code': 'invalid_request', 'message': 'The provider response could not be validated'}
            ai_notice = f'External AI was unavailable ({provider_diagnostic["code"]}); this answer was calculated locally.'
    if result is None:
        result = restricted_chat_answer(ledger, request.question, start, end, account_id)
        result['provider_status'] = provider_status
        result['ai_notice'] = ai_notice
        result['provider_diagnostic'] = provider_diagnostic
    result_context = dict(prior_context)
    result_context.update({'account_id': result.get('account_id', account_id), 'account': result.get('account', context['account']), 'date_from': result.get('period', {}).get('date_from', start.isoformat()), 'date_to': result.get('period', {}).get('date_to', end.isoformat())})
    # Persist semantic filters, not only chat text. This makes "show those" and
    # selection deterministic across restarts and provider availability.
    if result.get('tool') == 'create_shared_expense_preview':
        result_context['last_query'] = last_query
        result_context['last_result_transaction_ids'] = prior_context.get('last_result_transaction_ids', [])
        if action_preview_result:
            result_context['selected_transaction_ids'] = [item['id'] for item in action_preview_result['preview']['transactions']]
    elif result.get('tool') == 'get_transactions':
        current_query = {'account_id': result.get('account_id'), 'account': result.get('account'),
                         'date_from': result['period']['date_from'], 'date_to': result['period']['date_to']}
        current_query.update(result.get('filters') or {
            'category': prior_context.get('last_query', {}).get('category'),
            'merchant': prior_context.get('last_query', {}).get('merchant')})
        result_context['last_query'] = current_query
        matching_result = _chat_query_transactions(ledger, current_query)
        matching_result.sort(key=lambda item: (str(item.get('occurred_at', '')), item.get('id', '')), reverse=True)
        result_context['last_result_transaction_ids'] = [item['id'] for item in matching_result]
    else:
        category = None
        if result.get('tool') == 'get_category_breakdown':
            rows = result.get('data', {}).get('categories', [])
            if len(rows) == 1:
                category = rows[0].get('category')
        result_context['last_query'] = {'account_id': result.get('account_id', account_id),
            'account': result.get('account', context['account']),
            'date_from': result.get('period', {}).get('date_from', start.isoformat()),
            'date_to': result.get('period', {}).get('date_to', end.isoformat()),
            'category': category, 'merchant': None}
    session = repository.chat(USER_ID, request.session_id, result_context, request.question, result['answer'], result.get('tool'))
    response = {**result, 'provider_model': getattr(provider, 'model', None), 'session_id': session['session_id'], 'context': result_context, 'messages': session['messages']}
    if action_preview_result:
        response['action_preview'] = action_preview_result
    return response


@app.get('/api/ai/chat/transactions')
def chat_transaction_page(session_id: str, offset: int = Query(default=0, ge=0, le=100000)):
    try:
        conversation = ai_repository().conversation(USER_ID, session_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    query = conversation.get('context', {}).get('last_query') or {}
    ledger = load_ledger()
    matching = _chat_query_transactions(ledger, query)
    matching.sort(key=lambda item: (str(item.get('occurred_at', '')), item.get('id', '')), reverse=True)
    size = 20
    rows = [dict(item, personal_expense_paise=_personal_share_amount(ledger, item)) for item in matching[offset:offset + size]]
    return {'transactions': rows, 'total_count': len(matching), 'offset': offset,
            'has_more': offset + len(rows) < len(matching),
            'gross_debit_paise': sum(item['amount_paise'] for item in matching if item.get('direction') == 'debit'),
            'confirmed_personal_expense_paise': sum(_personal_share_amount(ledger, item) for item in matching)}


@app.post('/api/ai/chat/selection')
def update_chat_selection(request: ChatSelectionRequest):
    try:
        conversation = ai_repository().conversation(USER_ID, request.session_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    query = conversation.get('context', {}).get('last_query') or {}
    context = conversation.get('context', {})
    matching = _chat_query_transactions(load_ledger(), query)
    allowed_ids = {item['id'] for item in matching}
    result_ids = set(context.get('last_result_transaction_ids') or allowed_ids) & allowed_ids
    selected = result_ids if request.select_all_matching else set(request.transaction_ids)
    if not selected.issubset(result_ids):
        raise HTTPException(status_code=400, detail='Selection includes a transaction outside the latest retrieved result')
    if not selected.issubset(allowed_ids):
        raise HTTPException(status_code=400, detail='Selection includes a transaction outside the previous result scope')
    if len(selected) > 1000:
        raise HTTPException(status_code=400, detail='Select at most 1000 transactions at once')
    stored = ai_repository().update_chat_context(USER_ID, request.session_id, {'selected_transaction_ids': sorted(selected)})
    chosen = [item for item in matching if item['id'] in selected]
    return {'selected_transaction_ids': stored['selected_transaction_ids'], 'count': len(chosen),
            'gross_debit_paise': sum(item['amount_paise'] for item in chosen if item.get('direction') == 'debit')}


@app.post('/api/ai/actions/split/preview')
def preview_chat_split(request: SplitActionPreviewRequest):
    repo = ai_repository()
    try:
        conversation = repo.conversation(USER_ID, request.session_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    context = conversation.get('context', {})
    selected_ids = context.get('selected_transaction_ids') or context.get('last_result_transaction_ids') or []
    query = context.get('last_query') or {}
    matches = _chat_query_transactions(load_ledger(), query)
    by_id = {item['id']: item for item in matches}
    if not selected_ids:
        selected_ids = [item for item in context.get('last_result_transaction_ids', []) if item in by_id]
    if not selected_ids or any(item_id not in by_id for item_id in selected_ids):
        raise HTTPException(status_code=400, detail='Select transactions from the current conversation result first')
    ledger = load_ledger()
    transactions = [by_id[item_id] for item_id in selected_ids]
    if len(transactions) > 200:
        raise HTTPException(status_code=400, detail='Split at most 200 transactions per action')
    if len({item['id'] for item in transactions}) != len(transactions):
        raise HTTPException(status_code=400, detail='Duplicate transaction selection')
    names = [_name_key(item.name) for item in request.participants]
    if len(names) != len(set(names)):
        raise HTTPException(status_code=400, detail='Participant names must be unique; choose one unambiguous name per person')
    if sum(item.is_self for item in request.participants) != 1:
        raise HTTPException(status_code=400, detail='Exactly one participant must be marked as you')
    if any(t.get('direction') != 'debit' or t.get('status') != 'succeeded'
           or t.get('transaction_type') in {'transfer', 'refund', 'reimbursement', 'adjustment'}
           or t.get('treatment') not in {'expense', 'shared'}
           or any(s.get('transaction_id') == t['id'] for s in ledger.get('splits', [])) for t in transactions):
        raise HTTPException(status_code=409, detail='One or more selected transactions cannot be split or were already split')
    participant_data = [item.model_dump() for item in request.participants]
    try:
        per_transaction = _split_shares_for_transactions(transactions, participant_data, request.split_method)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    total = sum(t['amount_paise'] for t in transactions)
    known_contacts = {item.get('normalized_name') or _name_key(item['display_name']): item for item in ledger.get('contacts', [])}
    contact_status = {}
    contacts_to_create = []
    for participant in participant_data:
        if participant['is_self']:
            contact_status[_name_key(participant['name'])] = {'status': 'self', 'contact_id': None}
            continue
        key = _name_key(participant['name'])
        existing_contact = known_contacts.get(key)
        if existing_contact:
            contact_status[key] = {'status': 'existing', 'contact_id': existing_contact['id']}
        else:
            contact_status[key] = {'status': 'create_on_confirm', 'contact_id': None}
            contacts_to_create.append(participant['name'])
    for shares in per_transaction.values():
        for share in shares:
            share['contact_id'] = contact_status[_name_key(share['name'])]['contact_id']
    aggregates = {name: 0 for name in names}
    for shares in per_transaction.values():
        for share in shares:
            aggregates[_name_key(share['name'])] += share['share_paise']
    preview = {'total_paise': total, 'split_method': request.split_method,
        'participants': [{'name': item.name, 'is_self': item.is_self, 'share_paise': aggregates[_name_key(item.name)],
            'contact_status': contact_status[_name_key(item.name)]['status']} for item in request.participants],
        'transactions': [{'id': t['id'], 'date': str(t['occurred_at'])[:10],
            'recipient': t.get('merchant') or t.get('recipient'), 'account_id': t.get('account_id'),
            'amount_paise': t['amount_paise'], 'who_paid': 'You',
            'shares': per_transaction[t['id']]} for t in transactions],
        'transaction_count': len(transactions),
        'note': 'Original bank transactions remain in the ledger; this action records shared-expense allocations only.'}
    payload = {'transaction_ids': [t['id'] for t in transactions], 'per_transaction': per_transaction,
               'split_method': request.split_method, 'contacts_to_create': contacts_to_create,
               'contact_ids': {key: value['contact_id'] for key, value in contact_status.items() if value['contact_id']}}
    try:
        action = repo.create_action_preview(USER_ID, request.session_id, 'create_shared_expense', payload, preview, request.idempotency_key)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {'action': action}


@app.post('/api/ai/actions/confirm')
def confirm_chat_action(request: ConfirmChatActionRequest):
    repo = ai_repository()
    try:
        action = repo.action_preview(USER_ID, request.preview_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if action['status'] == 'committed':
        return {'status': 'committed', 'idempotent_replay': True, 'transaction_count': action['preview']['transaction_count']}
    expires_at = datetime.fromisoformat(action['expires_at'])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if action['status'] != 'pending' or expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=409, detail='Action preview is stale, expired, or already cancelled')
    ledger = load_ledger()
    tx_by_id = {t['id']: t for t in ledger.get('transactions', [])}
    try:
        contacts_by_name = {
            item.get('normalized_name') or _name_key(item['display_name']): item
            for item in ledger.get('contacts', [])
        }
        for display_name in action['payload'].get('contacts_to_create', []):
            key = _name_key(display_name)
            if key not in contacts_by_name:
                contact = {'id': uid(), 'display_name': display_name, 'normalized_name': key,
                           'created_at': datetime.now(timezone.utc).isoformat()}
                ledger.setdefault('contacts', []).append(contact)
                contacts_by_name[key] = contact
        contact_ids = {key: item['id'] for key, item in contacts_by_name.items()}
        for transaction_id in action['payload']['transaction_ids']:
            txn = tx_by_id.get(transaction_id)
            if txn is None or txn.get('status') != 'succeeded' or txn.get('direction') != 'debit':
                raise ValueError('Selected transaction is no longer available')
            if txn.get('transaction_type') in {'transfer', 'refund', 'reimbursement', 'adjustment'}:
                raise ValueError('Selected transaction is no longer eligible for splitting')
            shares = action['payload']['per_transaction'][transaction_id]
            for share in shares:
                share['contact_id'] = None if share['is_self'] else contact_ids.get(_name_key(share['name']))
            add_split(ledger, transaction_id, shares)
        save_ledger(ledger, consume_action_id=request.preview_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {'status': 'committed', 'idempotent_replay': False, 'transaction_count': len(action['payload']['transaction_ids'])}


@app.post('/api/ai/actions/cancel')
def cancel_chat_action(request: ConfirmChatActionRequest):
    try:
        action = ai_repository().cancel_action_preview(USER_ID, request.preview_id)
        return {'status': action['status']}
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


def _report_range(report_type, period, month, year, date_from, date_to):
    if date_from or date_to or period:
        return _resolve_date_range(period, month, date_from, date_to, year)
    today = datetime.now(timezone.utc).date()
    if report_type == 'weekly':
        return today - timedelta(days=6), today
    return _month_bounds(today.strftime('%Y-%m'))


@app.post('/api/ai/reports')
def generate_report(request: ReportRequest, account_id: Optional[str] = None, period: Optional[str] = None,
                    month: Optional[str] = None, year: Optional[int] = None,
                    date_from: Optional[str] = None, date_to: Optional[str] = None):
    if request.report_type not in {'weekly', 'monthly'}:
        raise HTTPException(status_code=400, detail='Report type must be weekly or monthly')
    start, end = _report_range(request.report_type, period, month, year, date_from, date_to)
    ledger = load_ledger()
    if account_id and account_id not in {item['id'] for item in ledger.get('accounts', [])}:
        raise HTTPException(status_code=404, detail='Account not found')
    content = build_report(ledger, request.report_type, start, end, account_id)
    return {'report': ai_repository().report(USER_ID, request.report_type, account_id, start, end, content)}


@app.get('/api/ai/reports')
def report_history(limit: int = 50):
    if not 1 <= limit <= 100:
        raise HTTPException(status_code=400, detail='limit must be between 1 and 100')
    return {'reports': ai_repository().reports(USER_ID, limit)}


def _simple_pdf(text):
    lines = [line[:110] for line in text.splitlines()][:48]
    encoded_lines = []
    for line in lines:
        safe = line.replace('\\', '\\\\').replace('(', '[').replace(')', ']')
        encoded_lines.append(f'({safe}) Tj 0 -14 Td')
    stream = 'BT /F1 10 Tf 40 760 Td ' + ' '.join(encoded_lines) + ' ET'
    objects = ['', '<< /Type /Catalog /Pages 2 0 R >>', '<< /Type /Pages /Kids [3 0 R] /Count 1 >>', '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>', '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>', f'<< /Length {len(stream.encode())} >>\nstream\n{stream}\nendstream']
    body = bytearray(b'%PDF-1.4\n'); offsets = [0]
    for index, obj in enumerate(objects[1:], 1): offsets.append(len(body)); body.extend(f'{index} 0 obj\n{obj}\nendobj\n'.encode())
    xref = len(body); body.extend(f'xref\n0 {len(objects)}\n0000000000 65535 f \n'.encode()); body.extend(''.join(f'{offset:010d} 00000 n \n' for offset in offsets[1:]).encode()); body.extend(f'trailer\n<< /Size {len(objects)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()); return bytes(body)


@app.get('/api/ai/reports/{report_id}/download')
def download_report(report_id: str, format: str = 'pdf'):
    try:
        report = ai_repository().report_by_id(USER_ID, report_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    text = json.dumps(report['content'], indent=2, ensure_ascii=False)
    if format == 'json':
        return Response(content=text, media_type='application/json', headers={'Content-Disposition': f'attachment; filename="spendwise-{report_id}.json"'})
    return Response(content=_simple_pdf(text), media_type='application/pdf', headers={'Content-Disposition': f'attachment; filename="spendwise-{report_id}.pdf"'})


@app.post('/api/imports')
def import_transactions(request: ImportRequest):
    ledger = load_ledger()
    try:
        before_ids = {item['id'] for item in ledger.get('transactions', [])}
        result = import_csv(ledger, request.text, request.filename)
        new_ids = [item['id'] for item in ledger.get('transactions', []) if item['id'] not in before_ids]
        categorization = categorize_existing_transactions(ledger, transaction_ids=new_ids)
        save_ledger(ledger)
        return {**result, 'categorization': categorization}
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post('/api/imports/preview')
async def preview_statement_upload(
    file: UploadFile = File(...),
    password: Optional[str] = Form(default=None),
    account_id: Optional[str] = Form(default=None),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail='A filename is required')
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail='Statement exceeds the 10 MB upload limit')
    try:
        preview = parse_statement(data, file.filename, password=password)
        # Password is intentionally scoped to this call and never placed in
        # the preview token or logs.
        ledger = load_ledger()
        for row in preview.rows:
            decision = classify(ledger, row.description, row.description)
            row.category = decision.category
            row.confidence = decision.confidence
            row.warnings.extend([] if decision.confirmed else ['Classification needs review before confirmation'])
        token = secrets.token_urlsafe(24)
        if len(_statement_previews) >= 20:
            _statement_previews.pop(next(iter(_statement_previews)))
        _statement_previews[token] = preview
        return preview.as_response(token)
    except StatementParseError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        data = b''
        password = None


@app.post('/api/imports/{token}/confirm')
def confirm_statement_import(token: str, request: StatementConfirmRequest):
    preview = _statement_previews.pop(token, None)
    if preview is None:
        raise HTTPException(status_code=404, detail='Import preview expired or was already confirmed')
    if preview.errors or preview.confirmation_blocked:
        raise HTTPException(status_code=400, detail='Statement has unresolved extraction or reconciliation discrepancies; confirmation is blocked')
    ledger = load_ledger()
    try:
        before_ids = {item['id'] for item in ledger.get('transactions', [])}
        result = import_csv(
            ledger,
            preview_to_csv(preview, request.category_corrections),
            filename=preview.filename,
            account_id=request.account_id,
            source=preview.format,
            category_overrides=request.category_corrections,
            transaction_type_overrides={index: row.transaction_type for index, row in enumerate(preview.rows)},
        )
        new_ids = [item['id'] for item in ledger.get('transactions', []) if item['id'] not in before_ids]
        categorization = categorize_existing_transactions(ledger, transaction_ids=new_ids)
        save_ledger(ledger)
        return {**result, 'categorization': categorization, 'confirmed': True, 'format': preview.format}
    except (ValueError, StopIteration) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post('/api/demo/reset')
def reset_demo(request: DemoResetRequest):
    repo = repository()
    repo.clear(USER_ID)
    if not request.seed:
        return {'demo': True, 'seeded': False, 'accounts': [], 'transactions': []}
    ledger = new_ledger()
    primary = create_account(ledger, 'Everyday', 'Mint Bank', 'savings', '•••• 2048', 850000)
    reserve = create_account(ledger, 'Rainy day', 'Mint Bank', 'savings', '•••• 7712', 1250000)
    salary = create_income(ledger, primary['id'], 165000, 'Salary', 'Acme Studio', 'November salary', 'DEMO-SAL-2026-11')
    salary['occurred_at'] = '2026-11-01T09:00:00+00:00'
    rent = create_expense(ledger, primary['id'], 32000, 'Housing / Rent', 'Home rent', 'November rent', 'DEMO-RENT-2026-11')
    rent['occurred_at'] = '2026-11-02T09:00:00+00:00'
    groceries = create_expense(ledger, primary['id'], 8400, 'Groceries', 'Green Market', 'Weekly groceries', 'DEMO-GROC-2026-11')
    groceries['occurred_at'] = '2026-11-04T18:30:00+00:00'
    refund = create_refund(ledger, primary['id'], 1200, 'Refund', 'Bookstore', 'Returned item', 'DEMO-REFUND-2026-11')
    refund['occurred_at'] = '2026-11-06T12:00:00+00:00'
    transfer = create_transfer(ledger, primary['id'], reserve['id'], 15000, 'Monthly reserve', 'DEMO-MOVE-2026-11')
    transfer['debit']['occurred_at'] = '2026-11-07T10:00:00+00:00'
    transfer['credit']['occurred_at'] = '2026-11-07T10:00:00+00:00'
    add_recurring_schedule(ledger, primary['id'], 'Salary', 'salary', 165000, 'Salary', '2026-12-01', note='Confirm when received')
    add_recurring_schedule(ledger, primary['id'], 'Internet', 'bill', 1800, 'Bills', '2026-11-20', note='Confirm after payment')
    upsert_budget(ledger, '2026-11', 'Food / Dining', 18000)
    upsert_budget(ledger, '2026-11', 'Groceries', 12000)
    goal = create_goal(ledger, 'Emergency cushion', 2500000, '2027-09-01', 'coral')
    contribute_to_goal(ledger, goal['id'], 450000, 'Existing reserve')
    repo.save(ledger, user_id=USER_ID, display_name=USER_NAME)
    return {'demo': True, 'seeded': True, 'accounts': _account_views(ledger), 'transactions': len(ledger['transactions'])}
