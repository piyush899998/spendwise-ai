"""Deterministic intelligence services for SpendWise.

The ledger and analytics engine remain authoritative.  This module only derives
reviewable projections, explanations, anomalies, chat answers and reports.
External model calls are opt-in and never receive raw statements or account
identifiers.
"""
from __future__ import annotations

import json
import os
import re
from calendar import monthrange
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from statistics import mean, pstdev

from .categories import CATEGORIES, category_from_text, category_matches, normalize_category
from .engine import metrics, normalize_merchant


@dataclass(frozen=True)
class LLMClassification:
    category: str
    merchant: str | None
    intent: str
    confidence: float | None
    explanation: str


@dataclass(frozen=True)
class LLMToolCall:
    tool_name: str
    arguments: dict


TOOL_NAMES = {
    'get_spending_summary', 'get_category_breakdown', 'get_merchant_breakdown',
    'get_account_comparison', 'compare_periods', 'get_largest_transactions',
    'get_recurring_payments', 'get_income_summary', 'get_cash_flow_summary', 'get_transactions',
}

TOOL_ARGUMENT_NAMES = {'month', 'date_from', 'date_to', 'account_name', 'category', 'limit', 'offset', 'merchant', 'direction', 'transaction_type'}


def _json_objects_from_text(text):
    """Find complete JSON objects in plain prose or Markdown without parsing reasoning text."""
    source = str(text or '').strip()
    if not source:
        return []
    candidates = []
    # A balanced scan tolerates fenced blocks and leading/trailing explanation
    # while respecting braces inside JSON strings.
    start = None
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(source):
        if start is None:
            if char == '{':
                start, depth, in_string, escaped = index, 1, False, False
            continue
        if in_string:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(source[start:index + 1])
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
                else:
                    if isinstance(parsed, dict):
                        candidates.append(parsed)
                start = None
    return candidates


def parse_llm_tool_call(payload) -> LLMToolCall:
    """Validate a model-selected tool before any financial data is queried."""
    if isinstance(payload, str):
        candidates = _json_objects_from_text(payload)
        if not candidates:
            raise ValueError('AI tool selection did not contain a JSON object')
        payload = next((candidate for candidate in candidates if candidate.get('tool_name') or candidate.get('name')), candidates[0])
    if isinstance(payload, dict) and isinstance(payload.get('function'), dict):
        function = payload['function']
        payload = {'tool_name': function.get('name'), 'arguments': function.get('arguments')}
    if not isinstance(payload, dict):
        raise ValueError('AI tool selection must be a JSON object')
    name = payload.get('tool_name') or payload.get('name')
    if name not in TOOL_NAMES:
        raise ValueError('AI selected an unsupported financial tool')
    arguments = payload.get('arguments') or {}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError('AI tool arguments must contain valid JSON') from error
    if not isinstance(arguments, dict):
        raise ValueError('AI tool arguments must be a JSON object')
    unknown = set(arguments) - TOOL_ARGUMENT_NAMES
    if unknown:
        raise ValueError('AI tool arguments contain unsupported fields')
    if 'limit' in arguments and (isinstance(arguments['limit'], bool) or not isinstance(arguments['limit'], int) or not 1 <= arguments['limit'] <= 20):
        raise ValueError('AI tool limit must be an integer from 1 to 20')
    if 'offset' in arguments and (isinstance(arguments['offset'], bool) or not isinstance(arguments['offset'], int) or not 0 <= arguments['offset'] <= 100000):
        raise ValueError('AI tool offset must be a non-negative integer')
    for field in ('month', 'date_from', 'date_to', 'account_name', 'category'):
        if field in arguments and not isinstance(arguments[field], str):
            raise ValueError(f'AI tool {field} must be text')
    if 'merchant' in arguments and (not isinstance(arguments['merchant'], str) or len(arguments['merchant']) > 120):
        raise ValueError('AI tool merchant must be text up to 120 characters')
    if 'direction' in arguments and arguments['direction'] not in {'debit', 'credit'}:
        raise ValueError('AI tool direction must be debit or credit')
    if 'transaction_type' in arguments and arguments['transaction_type'] not in {'expense', 'income', 'transfer', 'refund', 'adjustment', 'reimbursement', 'loan'}:
        raise ValueError('AI tool transaction type is unsupported')
    return LLMToolCall(name, arguments)


def parse_llm_classification(payload) -> LLMClassification:
    """Validate untrusted model output strictly and conservatively."""
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError('AI response must be a JSON object')
    category = normalize_category(payload.get('suggested_category') or payload.get('category'))
    if category is None:
        raise ValueError('AI response contains an unsupported category')
    intent = str(payload.get('transaction_intent') or payload.get('intent') or '').strip()
    if intent not in {'expense', 'income', 'transfer', 'refund', 'reimbursement', 'loan', 'savings', 'investment', 'unknown'}:
        raise ValueError('AI response contains an unsupported transaction intent')
    confidence = payload.get('confidence')
    if confidence is not None:
        try:
            confidence = float(confidence)
        except (TypeError, ValueError) as error:
            raise ValueError('AI confidence must be numeric') from error
        if not 0 <= confidence <= 1:
            raise ValueError('AI confidence must be between 0 and 1')
    return LLMClassification(category, str(payload.get('merchant') or '').strip()[:200] or None,
                              intent, confidence, str(payload.get('explanation') or '').strip()[:500])


def redacted_transaction(transaction):
    """Minimal model context; never include references, account IDs or notes."""
    return {
        'direction': transaction.get('direction'),
        'amount_paise': transaction.get('amount_paise'),
        'date': str(transaction.get('occurred_at', ''))[:10],
        'description': str(transaction.get('recipient') or '')[:120],
        'merchant': transaction.get('merchant'),
        'category': transaction.get('category'),
    }


def merchant_projection(transaction):
    return transaction.get('merchant') or normalize_merchant(transaction.get('recipient'), transaction.get('recipient'))


def _date(transaction):
    return date.fromisoformat(str(transaction['occurred_at'])[:10])


def _period_transactions(ledger, start: date, end: date, account_id=None):
    return [t for t in ledger.get('transactions', [])
            if t.get('status') == 'succeeded' and start <= _date(t) <= end
            and (account_id is None or t.get('account_id') == account_id)]


def _expense_amount(transaction):
    if transaction.get('direction') != 'debit':
        return 0
    if transaction.get('transaction_type') in {'transfer', 'refund', 'reimbursement', 'adjustment'}:
        return 0
    classification = transaction.get('classification') or {}
    # A debit is only confirmed personal spending after an explicit or
    # deterministic classification has been confirmed.  Imported rows that
    # remain Uncategorized/needs_review still contribute to gross cash
    # outflow, but must not be presented as personal spending.
    if classification:
        # Treat either persisted flag as a veto.  This matters for legacy
        # rows whose top-level flags and embedded decision were written by
        # different versions of the importer.
        confirmed = transaction.get('confirmed') is True and classification.get('confirmed') is True
        needs_review = transaction.get('needs_review') is True or classification.get('needs_review') is True
    else:
        confirmed = transaction.get('confirmed') is True
        needs_review = transaction.get('needs_review') is True
    if not confirmed or needs_review or transaction.get('category') == 'Uncategorized':
        return 0
    return transaction.get('amount_paise', 0) if transaction.get('treatment') == 'expense' else 0


def _personal_share_amount(ledger, transaction):
    split = next((item for item in ledger.get('splits', []) if item.get('transaction_id') == transaction.get('id')), None)
    if split and transaction.get('transaction_type') != 'transfer':
        return sum(participant.get('share_paise', 0) for participant in split.get('participants', []) if participant.get('is_self'))
    return _expense_amount(transaction)


def _previous_period(start, end):
    if start <= date.min + timedelta(days=1):
        return date.min, date.min
    days = end - start + timedelta(days=1)
    previous_start = start - days if start - days >= date.min else date.min
    previous_end = start - timedelta(days=1) if start > date.min else date.min
    return previous_start, previous_end


def _month_bounds(month):
    try:
        year, month_number = (int(part) for part in month.split('-'))
        return date(year, month_number, 1), date(year, month_number, monthrange(year, month_number)[1])
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError('Tool month must use YYYY-MM format') from error


def _resolve_tool_scope(ledger, arguments, base_start, base_end, base_account_id):
    if not isinstance(arguments, dict):
        raise ValueError('Tool arguments must be an object')
    allowed = {'month', 'date_from', 'date_to', 'account_name', 'category', 'limit', 'offset', 'merchant', 'direction', 'transaction_type'}
    unknown = set(arguments) - allowed
    if unknown:
        raise ValueError(f'Unsupported tool arguments: {sorted(unknown)}')
    start, end = base_start, base_end
    if arguments.get('month'):
        start, end = _month_bounds(arguments['month'])
    elif arguments.get('date_from') or arguments.get('date_to'):
        if not arguments.get('date_from') or not arguments.get('date_to'):
            raise ValueError('Both date_from and date_to are required')
        try:
            start, end = date.fromisoformat(arguments['date_from']), date.fromisoformat(arguments['date_to'])
        except (TypeError, ValueError) as error:
            raise ValueError('Tool dates must use YYYY-MM-DD format') from error
    if start > end:
        raise ValueError('Tool date range is reversed')

    account_id = base_account_id
    account_name = arguments.get('account_name')
    if account_name:
        needle = str(account_name).strip().casefold()
        matches = [account for account in ledger.get('accounts', [])
                   if needle in f"{account.get('name', '')} {account.get('bank', '')}".casefold()]
        if len(matches) != 1:
            raise ValueError('Tool account scope did not resolve to exactly one account')
        account_id = matches[0]['id']
    limit = arguments.get('limit', 5)
    max_limit = 100 if arguments.get('_internal_page') else 100
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= max_limit:
        raise ValueError('Tool limit must be an integer between 1 and 100')
    offset = arguments.get('offset', 0)
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 100000:
        raise ValueError('Tool offset must be a non-negative integer')
    category = arguments.get('category')
    if category is not None:
        category = normalize_category(category)
        if category is None:
            raise ValueError('Tool category is not supported')
    merchant = arguments.get('merchant')
    if merchant is not None and (not isinstance(merchant, str) or len(merchant) > 120):
        raise ValueError('Tool merchant must be text up to 120 characters')
    direction = arguments.get('direction')
    if direction is not None and direction not in {'debit', 'credit'}:
        raise ValueError('Tool direction must be debit or credit')
    transaction_type = arguments.get('transaction_type')
    if transaction_type is not None and transaction_type not in {'expense', 'income', 'transfer', 'refund', 'adjustment', 'reimbursement', 'loan'}:
        raise ValueError('Tool transaction type is unsupported')
    return start, end, account_id, limit, category, offset, merchant, direction, transaction_type


def _account_label(ledger, account_id):
    if not account_id:
        return 'All Accounts'
    account = next((item for item in ledger.get('accounts', []) if item.get('id') == account_id), None)
    return account.get('name', 'Selected account') if account else 'Selected account'


def tool_context(ledger, start, end, account_id=None):
    """Context safe to send to a provider: labels and dates, never identifiers."""
    return {
        'account': _account_label(ledger, account_id),
        'date_from': start.isoformat(),
        'date_to': end.isoformat(),
        'accounts': [{'name': item.get('name'), 'bank': item.get('bank')} for item in ledger.get('accounts', [])],
    }


def financial_tool_specs(ledger):
    """The only tools a provider may select; handlers remain server-side."""
    common = {
        'type': 'object',
        'properties': {
            'month': {'type': 'string', 'description': 'Optional YYYY-MM override'},
            'date_from': {'type': 'string', 'description': 'Optional inclusive YYYY-MM-DD start'},
            'date_to': {'type': 'string', 'description': 'Optional inclusive YYYY-MM-DD end'},
            'account_name': {'type': 'string', 'description': 'Optional exact or distinctive account/bank name'},
            'category': {'type': 'string', 'description': 'Optional category such as Food / Dining'},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20},
            'offset': {'type': 'integer', 'minimum': 0, 'maximum': 100000},
            'merchant': {'type': 'string', 'description': 'Optional merchant substring filter'},
            'direction': {'type': 'string', 'enum': ['debit', 'credit']},
            'transaction_type': {'type': 'string', 'enum': ['expense', 'income', 'transfer', 'refund', 'adjustment', 'reimbursement', 'loan']},
        },
        'additionalProperties': False,
    }
    descriptions = {
        'get_spending_summary': 'Posted personal expense, gross outflow, refunds, reimbursements and net cash flow.',
        'get_category_breakdown': 'Personal spending grouped by category.',
        'get_merchant_breakdown': 'Personal spending grouped by normalized merchant.',
        'get_account_comparison': 'Compare account-level income, spending and cash flow.',
        'compare_periods': 'Compare the active period with the immediately preceding comparable period.',
        'get_largest_transactions': 'Return the largest posted personal expenses with supporting transaction rows.',
        'get_recurring_payments': 'Return recurring-payment candidates supported by repeated dates and amounts.',
        'get_income_summary': 'Posted income and other credits for the selected scope.',
        'get_cash_flow_summary': 'Gross outflow, personal expense, income and net cash flow for the selected scope.',
        'get_transactions': 'Read-only transaction drill-down for the selected account, inclusive date range and optional category or merchant. Use pagination.',
    }
    return [{'name': name, 'description': descriptions[name], 'parameters': common} for name in sorted(TOOL_NAMES)]


def execute_financial_tool(ledger, tool_name, arguments, base_start, base_end, base_account_id=None):
    """Execute a validated, read-only analytics tool against the ledger."""
    if tool_name not in TOOL_NAMES:
        raise ValueError('Unsupported financial tool')
    start, end, account_id, limit, category, offset, merchant, direction, transaction_type = _resolve_tool_scope(ledger, arguments, base_start, base_end, base_account_id)
    analysis = insight_analysis(ledger, start, end, account_id)
    result = {'tool': tool_name, 'period': {'date_from': start.isoformat(), 'date_to': end.isoformat()},
              'account': _account_label(ledger, account_id), 'account_id': account_id}
    result['filters'] = {'category': category, 'merchant': merchant, 'direction': direction, 'transaction_type': transaction_type}
    if tool_name in {'get_spending_summary', 'get_cash_flow_summary'}:
        result['data'] = analysis['metrics']
    elif tool_name == 'get_category_breakdown':
        rows = analysis['category_breakdown']
        if category:
            rows = [row for row in rows if row['category'] == category]
        result['data'] = {'categories': rows[:limit], 'category': category,
                          'unclassified_debits_paise': analysis['metrics'].get('unclassified_debits_paise', 0)}
    elif tool_name == 'get_merchant_breakdown':
        result['data'] = {'merchants': analysis['merchant_breakdown'][:limit], 'unclassified_debits_paise': analysis['metrics'].get('unclassified_debits_paise', 0)}
    elif tool_name == 'get_account_comparison':
        # Comparing accounts is inherently an all-account operation.  Make
        # the expansion explicit in the verified result so both local and
        # provider-generated answers cannot misrepresent a selected account.
        selected_account = _account_label(ledger, account_id) if account_id else None
        result['account'] = 'All Accounts'
        result['account_id'] = None
        result['scope_expanded'] = bool(selected_account)
        if selected_account:
            result['scope_expanded_from'] = selected_account
        account_rows = []
        for item in ledger.get('accounts', []):
            if not item.get('is_active', True):
                continue
            account_analysis = insight_analysis(ledger, start, end, item['id'])
            account_metrics = account_analysis['metrics']
            account_rows.append({'name': item.get('name'), 'bank': item.get('bank'), 'personal_expense': account_metrics['personal_expense'],
                                 'gross_cash_outflow': account_metrics.get('gross_cash_outflow', 0),
                                 'unclassified_debits_paise': account_metrics.get('unclassified_debits_paise', 0),
                                 'income_received': account_metrics.get('income_received', 0),
                                 'net_cash_flow': account_metrics.get('credits_received', 0) - account_metrics.get('gross_cash_outflow', 0)})
        result['data'] = {'accounts': account_rows[:limit]}
    elif tool_name == 'compare_periods':
        result['data'] = {'current': analysis['metrics'], 'previous': analysis['previous_metrics']}
    elif tool_name == 'get_largest_transactions':
        result['data'] = {'transactions': analysis['largest_transactions'][:limit]}
        result['transactions'] = analysis['largest_transactions'][:limit]
    elif tool_name == 'get_recurring_payments':
        result['data'] = {'candidates': recurring_candidates(ledger, start, end, account_id)[:limit]}
    elif tool_name == 'get_income_summary':
        result['data'] = {'income_received': analysis['metrics'].get('income_received', 0), 'credits_received': analysis['metrics'].get('credits_received', 0), 'refunds_received': analysis['metrics'].get('refunds_received', 0)}
    elif tool_name == 'get_transactions':
        selected = _period_transactions(ledger, start, end, account_id)
        if category:
            selected = [item for item in selected if category_matches(item.get('category'), category)]
        if direction:
            selected = [item for item in selected if item.get('direction') == direction]
        if transaction_type:
            selected = [item for item in selected if item.get('transaction_type') == transaction_type]
        if merchant:
            needle = merchant.casefold()
            selected = [item for item in selected if needle in str(merchant_projection(item) or '').casefold() or needle in str(item.get('recipient', '')).casefold()]
        selected.sort(key=lambda item: (str(item.get('occurred_at', '')), item.get('id', '')), reverse=True)
        rows = selected[offset:offset + limit]
        rows = [dict(row, personal_expense_paise=_personal_share_amount(ledger, row)) for row in rows]
        gross_paise = sum(row.get('amount_paise', 0) for row in selected if row.get('direction') == 'debit')
        personal_paise = sum(_personal_share_amount(ledger, row) for row in selected)
        result['data'] = {'transactions': rows, 'total_count': len(selected), 'offset': offset,
                          'limit': limit, 'has_more': offset + len(rows) < len(selected),
                          'gross_debit_paise': gross_paise, 'confirmed_personal_expense_paise': personal_paise,
                          'category': category,
                          'unclassified_debits_paise': analysis['metrics'].get('unclassified_debits_paise', 0),
                          'sum_semantics': 'Gross debit total and confirmed personal expense are separate; unresolved debits are excluded from personal expense.'}
        result['transactions'] = rows
    return result


def _follow_up_scope(question, start, end):
    text = question.casefold()
    if 'last month' in text or 'previous month' in text:
        previous_end = start - timedelta(days=1)
        return date(previous_end.year, previous_end.month, 1), previous_end
    month_names = {name.casefold(): index for index, name in enumerate(('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'), 1)}
    for name, month_number in month_names.items():
        if re.search(rf'\b{name}\b', text):
            explicit_year = re.search(r'\b(20\d{2})\b', text)
            # ``all_time`` uses date.min/date.max. Those sentinel years are
            # not useful when a user says "in September" without a year.
            reference_year = start.year if 1900 <= start.year <= 2100 else date.today().year
            year = int(explicit_year.group(1)) if explicit_year else reference_year
            return date(year, month_number, 1), date(year, month_number, monthrange(year, month_number)[1])
    return start, end


def _local_tool_for_question(question):
    text = question.casefold()
    if 'account' in text and any(word in text for word in ('which', 'compare', 'highest', 'most', 'contributed')):
        return 'get_account_comparison'
    if 'merchant' in text:
        return 'get_merchant_breakdown'
    category = category_from_text(text)
    asks_for_rows = any(phrase in text for phrase in ('show', 'list', 'select', 'transactions', 'payments', 'expenses'))
    if asks_for_rows and category:
        return 'get_transactions'
    if 'category' in text or category:
        return 'get_category_breakdown'
    if 'largest' in text or 'biggest' in text or 'behind that' in text:
        return 'get_largest_transactions'
    if any(phrase in text for phrase in ('show me all', 'show those', 'show them', 'list those', 'transactions behind', 'show the transactions', 'show transactions')):
        return 'get_transactions'
    if 'subscription' in text or 'recurring' in text:
        return 'get_recurring_payments'
    if 'income' in text or 'salary' in text:
        return 'get_income_summary'
    if 'cash flow' in text or 'cashflow' in text or 'outflow' in text:
        return 'get_cash_flow_summary'
    if 'compare' in text or 'change' in text or 'higher' in text:
        return 'compare_periods'
    return 'get_spending_summary'


def _format_tool_answer(result):
    data = result.get('data', {})
    tool = result['tool']
    period = f"{result['period']['date_from']} to {result['period']['date_to']}"
    if tool == 'get_category_breakdown':
        rows = data.get('categories', [])
        suffix = _unclassified_note(data.get('unclassified_debits_paise', 0))
        if not rows and data.get('category'):
            return f"No confirmed {data['category']} transactions were found for this account and date range." + suffix
        return f"Confirmed personal spending from {period}: " + (', '.join(f"{row['category']} ₹{row['amount_paise'] / 100:,.2f}" for row in rows) or 'no confirmed categorized expenses.') + suffix
    if tool == 'get_merchant_breakdown':
        rows = data.get('merchants', [])
        suffix = _unclassified_note(data.get('unclassified_debits_paise', 0))
        return f"Confirmed personal spending by merchant from {period}: " + (', '.join(f"{row['merchant']} ₹{row['amount_paise'] / 100:,.2f}" for row in rows) or 'no confirmed personal expenses.') + suffix
    if tool == 'get_account_comparison':
        rows = data.get('accounts', [])
        scope_note = 'This comparison uses All Accounts.'
        if result.get('scope_expanded_from'):
            scope_note += f" The selected scope ({result['scope_expanded_from']}) was expanded because account comparison requires all accounts."
        return f"{scope_note} Confirmed personal spending by account for {period}: " + (', '.join(f"{row['name']} ₹{row['personal_expense'] / 100:,.2f} confirmed personal spending (gross outflow ₹{row.get('gross_cash_outflow', 0) / 100:,.2f})" for row in rows) or 'no active accounts.')
    if tool == 'compare_periods':
        current, previous = data.get('current', {}), data.get('previous', {})
        return f"Personal spending was ₹{current.get('personal_expense', 0) / 100:,.2f} in the selected period versus ₹{previous.get('personal_expense', 0) / 100:,.2f} in the comparable previous period."
    if tool == 'get_largest_transactions':
        rows = data.get('transactions', [])
        return f"The largest personal expenses from {period} total ₹{sum(row['amount_paise'] for row in rows) / 100:,.2f}."
    if tool == 'get_recurring_payments':
        return f"I found {len(data.get('candidates', []))} recurring-payment candidate(s) from {period}. They remain suggestions until confirmed."
    if tool == 'get_income_summary':
        return f"Posted income from {period} was ₹{data.get('income_received', 0) / 100:,.2f}."
    if tool == 'get_transactions':
        rows = data.get('transactions', [])
        if not rows and data.get('category'):
            return f"No confirmed {data['category']} transactions were found for this account and date range." + _unclassified_note(data.get('unclassified_debits_paise', 0))
        return (f"Showing {len(rows)} of {data.get('total_count', len(rows))} matching transactions for {period}. "
                f"Gross debits total ₹{data.get('gross_debit_paise', 0) / 100:,.2f}; "
                f"confirmed personal expense totals ₹{data.get('confirmed_personal_expense_paise', 0) / 100:,.2f}. "
                "These totals use different accounting semantics.")
    metrics_data = data
    return f"For {period}, confirmed personal spending was ₹{metrics_data.get('personal_expense', 0) / 100:,.2f}, gross cash outflow was ₹{metrics_data.get('gross_cash_outflow', metrics_data.get('gross_paid', 0)) / 100:,.2f}, and posted income was ₹{metrics_data.get('income_received', 0) / 100:,.2f}." + _unclassified_note(metrics_data.get('unclassified_debits_paise', 0))


def _unclassified_note(amount_paise):
    if not amount_paise:
        return ''
    return f" ₹{amount_paise / 100:,.2f} of debit outflow remains unclassified and is excluded from confirmed personal spending."


def _metrics_for_ai(transactions, splits, allocations, include_transfers):
    """Use authoritative ledger metrics while excluding unresolved debits from personal spend."""
    sanitized = []
    unclassified_debits = 0
    split_transaction_ids = {item.get('transaction_id') for item in splits}
    for transaction in transactions:
        row = dict(transaction)
        if (row.get('direction') == 'debit' and
                row.get('transaction_type') not in {'transfer', 'refund', 'reimbursement', 'adjustment'} and
                row.get('id') not in split_transaction_ids and
                not _expense_amount(row)):
            unclassified_debits += row.get('amount_paise', 0)
            row['treatment'] = 'unclassified'
        sanitized.append(row)
    result = metrics({'transactions': sanitized, 'splits': splits, 'allocations': allocations}, include_transfers=include_transfers)
    result['unclassified_debits_paise'] = unclassified_debits
    return result


def insight_analysis(ledger, start: date, end: date, account_id=None):
    transactions = _period_transactions(ledger, start, end, account_id)
    transaction_ids = {transaction['id'] for transaction in transactions}
    scoped_splits = [split for split in ledger.get('splits', []) if split.get('transaction_id') in transaction_ids]
    split_ids = {split['id'] for split in scoped_splits}
    scoped_allocations = [allocation for allocation in ledger.get('allocations', []) if allocation.get('split_id') in split_ids]
    previous_start, previous_end = _previous_period(start, end)
    previous = _period_transactions(ledger, previous_start, previous_end, account_id)
    current_by_category = defaultdict(int)
    current_by_merchant = defaultdict(int)
    for transaction in transactions:
        amount = _personal_share_amount(ledger, transaction)
        if amount:
            current_by_category[normalize_category(transaction.get('category')) or transaction.get('category', 'Uncategorized')] += amount
            current_by_merchant[merchant_projection(transaction) or 'Unknown merchant'] += amount
    previous_by_category = defaultdict(int)
    for transaction in previous:
        amount = _personal_share_amount(ledger, transaction)
        if amount:
            previous_by_category[normalize_category(transaction.get('category')) or transaction.get('category', 'Uncategorized')] += amount
    previous_ids = {transaction['id'] for transaction in previous}
    previous_splits = [split for split in ledger.get('splits', []) if split.get('transaction_id') in previous_ids]
    previous_split_ids = {split['id'] for split in previous_splits}
    previous_allocations = [allocation for allocation in ledger.get('allocations', []) if allocation.get('split_id') in previous_split_ids]
    current_metrics = _metrics_for_ai(transactions, scoped_splits, scoped_allocations, include_transfers=account_id is not None)
    previous_metrics = _metrics_for_ai(previous, previous_splits, previous_allocations, include_transfers=account_id is not None)
    insights = []
    if current_by_category:
        category, amount = max(current_by_category.items(), key=lambda pair: pair[1])
        insights.append({'key': f'top-category:{category}', 'kind': 'category', 'title': 'Biggest expense category', 'message': f'{category} is your largest personal expense at ₹{amount / 100:,.2f}.', 'amount_paise': amount, 'confidence': 1.0})
    if previous_metrics['personal_expense'] > 0 and current_metrics['personal_expense'] != previous_metrics['personal_expense']:
        change = current_metrics['personal_expense'] - previous_metrics['personal_expense']
        percent = round(abs(change) / previous_metrics['personal_expense'] * 100, 2)
        direction = 'increased' if change > 0 else 'decreased'
        insights.append({'key': 'period-change', 'kind': 'comparison', 'title': 'Period comparison', 'message': f'Personal spending {direction} by {percent}% versus the comparable previous period.', 'amount_paise': change, 'confidence': 1.0})
    if len(transactions) >= 3:
        expenses = [t['amount_paise'] for t in transactions if _expense_amount(t)]
        if expenses:
            threshold = mean(expenses) + (2 * pstdev(expenses) if len(expenses) > 1 else mean(expenses))
            for transaction in transactions:
                if _expense_amount(transaction) > threshold:
                    insights.append({'key': f'anomaly:{transaction["id"]}', 'kind': 'anomaly', 'title': 'Unusually large transaction', 'message': f'{transaction.get("recipient", "Transaction")} is higher than this period’s normal expense range.', 'transaction_id': transaction['id'], 'amount_paise': transaction['amount_paise'], 'why': 'Compared with the mean and standard deviation of at least three posted expenses.', 'confidence': 1.0})
    return {
        'period': {'date_from': start.isoformat(), 'date_to': end.isoformat(), 'account_id': account_id},
        'metrics': current_metrics,
        'previous_metrics': previous_metrics,
        'category_breakdown': [{'category': k, 'amount_paise': v} for k, v in sorted(current_by_category.items(), key=lambda pair: pair[1], reverse=True)],
        'merchant_breakdown': [{'merchant': k, 'amount_paise': v} for k, v in sorted(current_by_merchant.items(), key=lambda pair: pair[1], reverse=True)[:10]],
        'insights': insights,
        'largest_transactions': [redacted_transaction(t) | {'id': t['id']} for t in sorted(transactions, key=lambda item: item['amount_paise'], reverse=True)[:5]],
    }


def recurring_candidates(ledger, start: date | None = None, end: date | None = None, account_id=None):
    transactions = [t for t in ledger.get('transactions', []) if t.get('status') == 'succeeded' and t.get('direction') == 'debit' and t.get('transaction_type') not in {'transfer', 'refund'} and (account_id is None or t.get('account_id') == account_id)]
    if start and end:
        transactions = [t for t in transactions if start <= _date(t) <= end]
    groups = defaultdict(list)
    for transaction in transactions:
        merchant = merchant_projection(transaction) or transaction.get('recipient', 'Unknown')
        groups[merchant.casefold()].append(transaction)
    candidates = []
    for key, rows in groups.items():
        if len(rows) < 2:
            continue
        dates = sorted(_date(row) for row in rows)
        intervals = [(right - left).days for left, right in zip(dates, dates[1:])]
        amounts = [row['amount_paise'] for row in rows]
        average_interval = round(mean(intervals))
        if not 20 <= average_interval <= 40 or max(amounts) - min(amounts) > max(100, round(mean(amounts) * 0.1)):
            continue
        candidates.append({'candidate_key': key, 'merchant': merchant_projection(rows[-1]) or rows[-1]['recipient'], 'amount_paise': round(mean(amounts)), 'interval_days': average_interval, 'confidence': min(0.99, 0.6 + len(rows) * 0.1), 'status': 'candidate', 'transaction_ids': [row['id'] for row in rows]})
    return candidates


def restricted_chat_answer(ledger, question: str, start: date, end: date, account_id=None):
    """Deterministic fallback using the same restricted registry as the provider path."""
    local_start, local_end = _follow_up_scope(question, start, end)
    tool_name = _local_tool_for_question(question)
    arguments = {}
    if tool_name == 'get_transactions':
        arguments['limit'] = 20
    if tool_name in {'get_category_breakdown', 'get_transactions'}:
        category = category_from_text(question)
        if category:
            arguments['category'] = category
    try:
        result = execute_financial_tool(ledger, tool_name, arguments, local_start, local_end, account_id)
    except ValueError:
        result = execute_financial_tool(ledger, 'get_spending_summary', {}, start, end, account_id)
    result['answer'] = _format_tool_answer(result)
    result['transactions'] = result.get('transactions', result.get('data', {}).get('transactions', []))
    result['provider_status'] = 'local_fallback'
    result['ai_notice'] = 'External AI is unavailable or not enabled; this answer was calculated locally.'
    return result


def build_report(ledger, report_type: str, start: date, end: date, account_id=None):
    analysis = insight_analysis(ledger, start, end, account_id)
    analysis['report_type'] = report_type
    analysis['generated_at'] = date.today().isoformat()
    analysis['scope_label'] = account_id or 'All accounts combined'
    analysis['recurring_candidates'] = recurring_candidates(ledger, start, end, account_id)
    return analysis
