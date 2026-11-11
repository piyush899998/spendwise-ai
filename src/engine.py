"""SpendWise's deterministic ledger. All amounts are integer paise."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from calendar import monthrange
import copy
import re
from uuid import uuid4
import csv, io
from .categories import CATEGORIES, normalize_category

TREATMENTS = ['expense', 'shared', 'transfer', 'loan']
TRANSACTION_TYPES = ['expense', 'income', 'transfer', 'refund', 'adjustment', 'reimbursement', 'loan']
CLASSIFICATION_SOURCES = {
    'user', 'approved_rule', 'note_suggestion', 'ai_suggestion', 'needs_review',
}
REFERENCE_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$')

# Ordered, extensible merchant registry. Keep aliases specific enough that a
# person's name alone does not become a merchant match.
MERCHANT_RULES = [
    {'key': 'swiggy', 'aliases': ('swiggy', 'swiggyinstamart'), 'category': 'Food / Dining'},
    {'key': 'zomato', 'aliases': ('zomato',), 'category': 'Food / Dining'},
    {'key': 'blinkit', 'aliases': ('blinkit',), 'category': 'Groceries'},
    {'key': 'zepto', 'aliases': ('zepto',), 'category': 'Groceries'},
    {'key': 'bigbasket', 'aliases': ('bigbasket',), 'category': 'Groceries'},
    {'key': 'uber', 'aliases': ('uber',), 'category': 'Transport'},
    {'key': 'ola', 'aliases': ('ola',), 'category': 'Transport'},
    {'key': 'rapido', 'aliases': ('rapido',), 'category': 'Transport'},
    {'key': 'namma-yatri', 'aliases': ('namma yatri', 'nammayatri'), 'category': 'Transport'},
    {'key': 'irctc', 'aliases': ('irctc',), 'category': 'Travel'},
    {'key': 'railway', 'aliases': ('railway', 'rail ticket', 'train ticket'), 'category': 'Travel'},
    {'key': 'amazon', 'aliases': ('amazon',), 'category': 'Shopping'},
    {'key': 'flipkart', 'aliases': ('flipkart',), 'category': 'Shopping'},
    {'key': 'myntra', 'aliases': ('myntra',), 'category': 'Shopping'},
    {'key': 'ajio', 'aliases': ('ajio',), 'category': 'Shopping'},
    {'key': 'bookmyshow', 'aliases': ('bookmyshow', 'book my show'), 'category': 'Entertainment'},
    {'key': 'pvr', 'aliases': ('pvr',), 'category': 'Entertainment'},
    {'key': 'inox', 'aliases': ('inox',), 'category': 'Entertainment'},
    {'key': 'coursera', 'aliases': ('coursera',), 'category': 'Education'},
    {'key': 'udemy', 'aliases': ('udemy',), 'category': 'Education'},
    {'key': 'apollo-pharmacy', 'aliases': ('apollo pharmacy',), 'category': 'Healthcare'},
    {'key': 'pharmeasy', 'aliases': ('pharmeasy', 'pharm easy'), 'category': 'Healthcare'},
    {'key': 'netflix', 'aliases': ('netflix',), 'category': 'Subscriptions'},
    {'key': 'spotify', 'aliases': ('spotify',), 'category': 'Subscriptions'},
    {'key': 'google-play', 'aliases': ('google play', 'googleplay'), 'category': 'Subscriptions'},
    {'key': 'jio', 'aliases': ('jio', 'jiopayments'), 'category': 'Bills'},
    {'key': 'airtel', 'aliases': ('airtel',), 'category': 'Bills'},
    {'key': 'bescom', 'aliases': ('bescom',), 'category': 'Bills'},
]


@dataclass(frozen=True)
class ClassificationDecision:
    """The auditable result of classifying a transaction."""

    category: str
    source: str
    confidence: float | None
    confirmed: bool

    def __post_init__(self):
        if self.source not in CLASSIFICATION_SOURCES:
            raise ValueError(f'Unknown classification source: {self.source}')
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError('Classification confidence must be between 0 and 1')

    @property
    def needs_review(self):
        return not self.confirmed

    def as_dict(self):
        return {**asdict(self), 'needs_review': self.needs_review}

def uid(): return str(uuid4())
def now(): return datetime.now(timezone.utc).isoformat()
def paise(value):
    from decimal import Decimal, ROUND_HALF_UP
    d = Decimal(str(value))
    if not d.is_finite() or d <= 0: raise ValueError('Amount must be positive')
    return int((d * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
def rupees(value): return f'₹{value / 100:,.2f}'
def new_ledger():
    return {
        'intents': [], 'transactions': [], 'splits': [], 'allocations': [], 'contacts': [],
        'rules': [], 'events': [], 'import_keys': [], 'import_batches': [],
        'accounts': [], 'recurring_schedules': [], 'recurring_postings': [],
        'budgets': [], 'goals': [], 'goal_contributions': [],
    }


def normalize_merchant(narration, recipient=None):
    """Return a conservative merchant projection without changing narration."""
    text = str(narration or recipient or '').strip()
    cleaned = re.sub(r'(?i)\b(?:UPI|IMPS|NEFT|RTGS)\s*/\s*(?:DR|CR|MANDATE)\s*/\s*[^/\s]+\s*/?', '', text)
    cleaned = re.sub(r'(?i)\b(?:UPI|IMPS|NEFT|RTGS)\s*/\s*', '', cleaned)
    cleaned = re.sub(r'(?i)\b(?:HDFC|ICIC|YESB|SBI|PUNB|UTIB|UBIN|JIOP|PAYTM|PHONEPE|GPAY|RAZORPAY|PAYU|BILLDESK|CASHFREE)\b', '', cleaned)
    cleaned = re.sub(r'(?i)\b(?:DR|CR|MANDATE|PAYMENT|TRANSFER|REFUND|REVERSAL)\b', '', cleaned)
    cleaned = re.sub(r'[/|]+', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip(' -:')
    return cleaned[:200] if cleaned else None


def _has_token(text, alias):
    pattern = r'(?<![A-Za-z0-9])' + re.escape(alias).replace(r'\ ', r'\s+') + r'(?![A-Za-z0-9])'
    return re.search(pattern, text, re.IGNORECASE) is not None


def _merchant_rule(text, raw_text):
    normalized = (text or '').strip()
    raw = str(raw_text or '').strip()
    payment_context = bool(re.search(r'(?i)(?:upi|imps|neft|rtgs|payment|merchant|store|order|gateway|/)', raw))
    for rule in MERCHANT_RULES:
        for alias in rule['aliases']:
            if _has_token(normalized, alias):
                # A bare name such as "Amazon" can be a person. Require a
                # payment/narration context when the entire normalized value
                # is only the alias; richer descriptions are sufficient.
                if normalized.casefold() == alias.casefold() and not payment_context:
                    continue
                return rule
    return None


def _account_for(db, account_id):
    if account_id is None:
        return None
    account = next((account for account in db['accounts'] if account['id'] == account_id), None)
    if account is None or not account.get('is_active', True):
        raise ValueError('Account not found or inactive')
    return account


def create_account(db, name, bank, account_type, masked_account_number, opening_balance_paise=0):
    if not name.strip() or not bank.strip() or not masked_account_number.strip():
        raise ValueError('Account name, bank, and masked account number are required')
    if opening_balance_paise < 0:
        raise ValueError('Opening balance cannot be negative')
    account = {
        'id': uid(), 'name': name.strip(), 'bank': bank.strip(),
        'account_type': account_type, 'masked_account_number': masked_account_number.strip(),
        'opening_balance_paise': opening_balance_paise, 'is_active': True, 'created_at': now(),
    }
    db['accounts'].append(account)
    return account


def update_account(db, account_id, **changes):
    account = _account_for(db, account_id)
    allowed = {'name', 'bank', 'account_type', 'masked_account_number', 'is_active'}
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(f'Unknown account fields: {sorted(unknown)}')
    for key, value in changes.items():
        if key in {'name', 'bank', 'masked_account_number'} and not str(value).strip():
            raise ValueError(f'{key} is required')
        account[key] = value.strip() if isinstance(value, str) else value
    return account


def create_transaction(db, account_id, amount_paise, direction, transaction_type,
                       category='Uncategorized', recipient='', note='', reference=None,
                       treatment=None, transfer_group_id=None, occurred_at=None, merchant=None):
    _account_for(db, account_id)
    if amount_paise <= 0 or direction not in ('debit', 'credit'):
        raise ValueError('Invalid transaction amount or direction')
    if transaction_type not in TRANSACTION_TYPES:
        raise ValueError('Invalid transaction type')
    normalized_reference = validate_reference(reference)
    if normalized_reference and _transaction_for_reference(db, normalized_reference):
        raise ValueError('Duplicate transaction reference')
    if treatment is None:
        treatment = {
            'expense': 'expense', 'income': 'income', 'transfer': 'transfer',
            'refund': 'refund', 'adjustment': 'adjustment',
            'reimbursement': 'unclassified', 'loan': 'loan',
        }[transaction_type]
    transaction = {
        'id': uid(), 'intent_id': None, 'external_ref': normalized_reference,
        'recipient': recipient.strip() or category, 'merchant': merchant or normalize_merchant(recipient, recipient), 'amount_paise': amount_paise,
        'direction': direction, 'status': 'succeeded', 'category': category,
        'treatment': treatment, 'transaction_type': transaction_type,
        'transfer_group_id': transfer_group_id, 'note': note or '',
        'source': 'manual', 'confirmed': True, 'needs_review': False,
        'classification': ClassificationDecision(category, 'user', 1.0, True).as_dict(),
        'account_id': account_id, 'occurred_at': occurred_at or now(),
    }
    db['transactions'].append(transaction)
    return transaction


def create_income(db, account_id, amount_paise, category='Salary', recipient='Employer',
                  note='', reference=None):
    return create_transaction(db, account_id, amount_paise, 'credit', 'income', category,
                              recipient, note, reference)


def create_expense(db, account_id, amount_paise, category='Other', recipient='Merchant',
                   note='', reference=None):
    return create_transaction(db, account_id, amount_paise, 'debit', 'expense', category,
                              recipient, note, reference)


def create_refund(db, account_id, amount_paise, category='Refund', recipient='Merchant',
                  note='', reference=None):
    return create_transaction(db, account_id, amount_paise, 'credit', 'refund', category,
                              recipient, note, reference)


def create_adjustment(db, account_id, amount_paise, direction, note='', reference=None):
    return create_transaction(db, account_id, amount_paise, direction, 'adjustment',
                              'Adjustment', 'Account adjustment', note, reference)


def create_transfer(db, from_account_id, to_account_id, amount_paise, note='', reference=None):
    if from_account_id == to_account_id:
        raise ValueError('Transfer requires two different accounts')
    _account_for(db, from_account_id)
    _account_for(db, to_account_id)
    group_id = uid()
    base_reference = validate_reference(reference) or f'TRANSFER-{group_id}'
    debit = create_transaction(
        db, from_account_id, amount_paise, 'debit', 'transfer', 'Internal Transfer',
        'Transfer out', note, f'{base_reference}-OUT', 'transfer', group_id,
    )
    credit = create_transaction(
        db, to_account_id, amount_paise, 'credit', 'transfer', 'Internal Transfer',
        'Transfer in', note, f'{base_reference}-IN', 'transfer', group_id,
    )
    return {'group_id': group_id, 'debit': debit, 'credit': credit}


def _classification(decision):
    return decision.as_dict()


def _transaction_for_reference(db, reference):
    if not reference:
        return None
    return next((t for t in db['transactions'] if t.get('external_ref') == reference), None)


def validate_reference(reference):
    """Validate an optional external reference and return its normalized value."""
    if reference is None or not str(reference).strip():
        return None
    normalized = str(reference).strip()
    if not REFERENCE_RE.fullmatch(normalized):
        raise ValueError('Reference must be 1-128 characters: letters, numbers, ., _, :, or - only')
    return normalized


def _parse_csv_date(value):
    normalized = str(value).strip()
    try:
        parsed = date.fromisoformat(normalized)
    except (TypeError, ValueError) as e:
        raise ValueError('Date must use YYYY-MM-DD and be a real calendar date') from e
    if parsed.isoformat() != normalized:
        raise ValueError('Date must use YYYY-MM-DD')
    return parsed.isoformat()


def _parse_csv_amount(value):
    try:
        decimal_value = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError) as e:
        raise ValueError('Amount must be a positive number with at most two decimal places') from e
    if not decimal_value.is_finite() or decimal_value <= 0 or decimal_value.as_tuple().exponent < -2:
        raise ValueError('Amount must be a positive number with at most two decimal places')
    return paise(decimal_value)


def classify(db, recipient, note='', explicit_choice=None):
    """Classify a payment while retaining source, confidence, and confirmation."""
    if explicit_choice is not None:
        canonical = normalize_category(explicit_choice)
        if canonical is None:
            raise ValueError('Invalid category')
        return ClassificationDecision(canonical, 'user', 1.0, True)
    raw_text = str(recipient or '').strip()
    normalized = normalize_merchant(raw_text) or raw_text
    for rule in db['rules']:
        if rule.get('enabled', True) and (_has_token(normalized, rule['recipient']) or rule['recipient'] == raw_text.casefold()):
            return ClassificationDecision(rule['category'], 'approved_rule', 0.95, True)
    lowered_note = (note or '').lower()
    merchant_rule = _merchant_rule(normalized, raw_text)
    if merchant_rule:
        return ClassificationDecision(merchant_rule['category'], 'approved_rule', 0.9, True)
    contextual_rules = [
        ('rent', 'Housing / Rent'), ('grocery', 'Groceries'), ('metro', 'Transport'),
        ('hospital', 'Healthcare'), ('pharmacy', 'Healthcare'), ('school', 'Education'),
        ('college', 'Education'), ('tuition', 'Education'), ('course', 'Education'),
        ('flight', 'Travel'), ('hotel', 'Travel'), ('train', 'Travel'), ('railway', 'Travel'),
        ('taxi', 'Transport'), ('cab', 'Transport'), ('auto', 'Transport'), ('bus', 'Transport'),
        ('salon', 'Personal Care'), ('electricity', 'Bills'), ('internet', 'Bills'),
        ('insurance', 'Bills'), ('electricity', 'Bills'), ('recharge', 'Bills'),
        ('dinner', 'Food / Dining'), ('lunch', 'Food / Dining'),
    ]
    for term, category in contextual_rules:
        if _has_token(normalized, term):
            return ClassificationDecision(category, 'approved_rule', 0.9, True)
        if _has_token(lowered_note, term):
            return ClassificationDecision(category, 'note_suggestion', 0.6, False)
    return ClassificationDecision('Uncategorized', 'needs_review', None, False)
def create_intent(db, recipient, amount_paise, category='Uncategorized', treatment='expense', note='', remember=False, account_id=None):
    if amount_paise <= 0 or treatment not in TREATMENTS: raise ValueError('Invalid payment')
    _account_for(db, account_id)
    i = {'id':uid(),'recipient':recipient.strip(),'amount_paise':amount_paise,'category':category,
         'treatment':treatment,'note':note,'remember':bool(remember),'account_id':account_id,
         'status':'created','created_at':now()}
    if not i['recipient']: raise ValueError('Recipient required')
    db['intents'].append(i); return i

def settle_intent(db, intent_id, outcome='succeeded'):
    intent = next(i for i in db['intents'] if i['id']==intent_id)
    existing = next((t for t in db['transactions'] if t.get('intent_id')==intent_id),None)
    if existing: return existing
    if intent['status'] in ('failed','expired'): raise ValueError('Terminal payment status')
    if outcome not in ('succeeded','failed','pending','expired'): raise ValueError('Unknown status')
    intent['status']=outcome
    if outcome!='succeeded': return None
    decision = ClassificationDecision(intent['category'], 'user', 1.0, True)
    external_ref = f'DEMO-{intent_id}'
    if _transaction_for_reference(db, external_ref):
        raise ValueError('Duplicate transaction reference')
    t={'id':uid(),'intent_id':intent_id,'external_ref':external_ref, 'recipient':intent['recipient'],
       'amount_paise':intent['amount_paise'],'direction':'debit','status':'succeeded','category':intent['category'],
       'treatment':intent['treatment'],'source':'simulation','confirmed':True,'needs_review':False,
       'classification':_classification(decision),'transaction_type':'transfer' if intent['treatment'] == 'transfer' else 'expense',
       'transfer_group_id':None,'note':intent.get('note', ''),'account_id':intent.get('account_id'),
       'occurred_at':now()}
    db['transactions'].append(t)
    if intent['remember']:
        db['rules'].append({'recipient':intent['recipient'].casefold(),'category':intent['category'],'treatment':intent['treatment']})
    db['events'].append({'id':uid(),'type':'payment_settled','transaction_id':t['id'],'at':now()})
    return t

def suggest(db, recipient, note=''):
    decision = classify(db, recipient, note)
    return decision.category, decision.source


def categorize_existing_transactions(db, transaction_ids=None, start=None, end=None, account_id=None, ai_classifier=None):
    """Safely backfill imported rows without replacing user decisions.

    The operation is idempotent: applying it repeatedly produces no new
    ledger rows and does not change amounts, dates, references, or narration.
    """
    selected_ids = set(transaction_ids) if transaction_ids is not None else None
    scanned = automatically_categorized = suggested_for_review = still_uncategorized = skipped_manual = skipped_already_classified = ai_assisted_suggestions = 0
    changed_ids = []
    for transaction in db.get('transactions', []):
        if selected_ids is not None and transaction['id'] not in selected_ids:
            continue
        if start and date.fromisoformat(str(transaction['occurred_at'])[:10]) < start:
            continue
        if end and date.fromisoformat(str(transaction['occurred_at'])[:10]) > end:
            continue
        if account_id and transaction.get('account_id') != account_id:
            continue
        classification = transaction.get('classification') or {}
        source = classification.get('source')
        if selected_ids is not None and source == 'approved_rule' and transaction.get('category') != 'Uncategorized':
            scanned += 1
            automatically_categorized += 1
            continue
        manually_classified = source == 'user' or (classification.get('confirmed', False) and transaction.get('category') != 'Uncategorized')
        eligible = transaction.get('category') == 'Uncategorized' or (transaction.get('needs_review') and source in {'needs_review', 'note_suggestion', None})
        if manually_classified:
            skipped_manual += 1
            continue
        if not eligible:
            skipped_already_classified += 1
            continue
        scanned += 1
        decision = classify(db, transaction.get('recipient', ''), transaction.get('note', ''))
        if decision.category == 'Uncategorized' and ai_classifier is not None:
            try:
                suggestion = ai_classifier(transaction)
            except Exception:
                suggestion = None
            suggested_category = getattr(suggestion, 'category', None)
            if suggested_category in CATEGORIES and suggested_category != 'Uncategorized':
                confidence = getattr(suggestion, 'confidence', None)
                confidence = min(float(confidence), 0.85) if confidence is not None else None
                decision = ClassificationDecision(suggested_category, 'ai_suggestion', confidence, False)
                ai_assisted_suggestions += 1
        before = (transaction.get('category'), transaction.get('classification'), transaction.get('needs_review'), transaction.get('confirmed'))
        transaction.update(category=decision.category, classification=decision.as_dict(),
                           needs_review=decision.needs_review, confirmed=decision.confirmed)
        after = (transaction.get('category'), transaction.get('classification'), transaction.get('needs_review'), transaction.get('confirmed'))
        if before != after:
            changed_ids.append(transaction['id'])
        if decision.confirmed:
            automatically_categorized += 1
        else:
            suggested_for_review += 1
        if decision.category == 'Uncategorized':
            still_uncategorized += 1
    return {
        'scanned': scanned, 'automatically_categorized': automatically_categorized,
        'suggested_for_review': suggested_for_review, 'still_uncategorized': still_uncategorized,
        'skipped_manual': skipped_manual, 'skipped_already_classified': skipped_already_classified,
        'ai_assisted_suggestions': ai_assisted_suggestions, 'changed_transaction_ids': changed_ids,
    }

def add_split(db, txn_id, participants):
    txn=next(t for t in db['transactions'] if t['id']==txn_id)
    if txn['direction']!='debit' or txn['status']!='succeeded': raise ValueError('Split requires settled debit')
    if txn['treatment'] not in ('expense', 'shared'):
        raise ValueError('Only expense transactions can be split')
    if any(s['transaction_id']==txn_id for s in db['splits']): raise ValueError('Already split')
    if not participants or sum(p['share_paise'] for p in participants)!=txn['amount_paise']: raise ValueError('Shares must sum to payment')
    if sum(bool(p['is_self']) for p in participants)!=1: raise ValueError('Exactly one self share required')
    if any(p['share_paise']<0 for p in participants): raise ValueError('Negative share')
    split={'id':uid(),'transaction_id':txn_id,'participants':[{**p,'id':uid()} for p in participants]}
    db['splits'].append(split);txn['treatment']='shared';return split

def equal_split(amount_paise, names):
    if not names or len(names)!=len(set(names)): raise ValueError('Unique participants required')
    base,remainder=divmod(amount_paise,len(names))
    return [{'name':n,'share_paise':base+(1 if ix<remainder else 0),'is_self':ix==0} for ix,n in enumerate(names)]

def incoming(db, sender, amount_paise, reference=None, category='Uncategorized', account_id=None,
             transaction_type='reimbursement', note=''):
    if amount_paise<=0: raise ValueError('Amount must be positive')
    if not sender or not sender.strip(): raise ValueError('Sender required')
    _account_for(db, account_id)
    normalized_reference = validate_reference(reference) or f'IN-{uid()}'
    existing = _transaction_for_reference(db, normalized_reference)
    if existing:
        return existing
    decision = ClassificationDecision(category, 'needs_review', None, False)
    t={'id':uid(),'intent_id':None,'external_ref':normalized_reference, 'recipient':sender.strip(),
       'amount_paise':amount_paise,'direction':'credit','status':'succeeded','category':category,
       'treatment':'unclassified','source':'manual','confirmed':False,'needs_review':True,
       'classification':_classification(decision),'transaction_type':transaction_type,
       'transfer_group_id':None,'note':note,'account_id':account_id,'occurred_at':now()}
    db['transactions'].append(t);return t

def allocated_to_share(db,share_id):return sum(a['amount_paise'] for a in db['allocations'] if a['share_id']==share_id and not a.get('reversed'))
def allocated_from_credit(db,credit_id):return sum(a['amount_paise'] for a in db['allocations'] if a['credit_id']==credit_id and not a.get('reversed'))
def allocate(db,split_id,share_id,credit_id,amount_paise):
    split=next(s for s in db['splits'] if s['id']==split_id)
    share=next(p for p in split['participants'] if p['id']==share_id)
    credit=next(t for t in db['transactions'] if t['id']==credit_id)
    if share['is_self'] or credit['direction']!='credit' or credit['status']!='succeeded':raise ValueError('Invalid reimbursement')
    if amount_paise<=0 or amount_paise>share['share_paise']-allocated_to_share(db,share_id):raise ValueError('Exceeds outstanding share')
    if amount_paise>credit['amount_paise']-allocated_from_credit(db,credit_id):raise ValueError('Exceeds available credit')
    a={'id':uid(),'split_id':split_id,'share_id':share_id,'credit_id':credit_id,'amount_paise':amount_paise,'reversed':False}
    db['allocations'].append(a);db['events'].append({'id':uid(),'type':'reimbursement_confirmed','allocation_id':a['id'],'transaction_id':credit_id,'at':now()});return a

def reverse_allocation(db,allocation_id):
    a=next(a for a in db['allocations'] if a['id']==allocation_id)
    if a['reversed']: return
    a['reversed']=True;db['events'].append({'id':uid(),'type':'reimbursement_reversed','allocation_id':allocation_id,'transaction_id':a['credit_id'],'at':now()})

def metrics(db, include_transfers=True):
    tx=[t for t in db['transactions'] if t['status']=='succeeded']
    if not include_transfers:
        tx = [t for t in tx if t.get('transaction_type') != 'transfer']
    gross=sum(t['amount_paise'] for t in tx if t['direction']=='debit')
    credits=sum(t['amount_paise'] for t in tx if t['direction']=='credit')
    split_by_tx={s['transaction_id']:s for s in db['splits']}
    personal=0;by_category={}
    for t in tx:
        if t['direction']!='debit':continue
        transaction_type = t.get('transaction_type')
        category_key = normalize_category(t.get('category')) or t.get('category')
        if t['id'] in split_by_tx and t['treatment'] in ('expense', 'shared') and transaction_type != 'transfer':
            amount=sum(p['share_paise'] for p in split_by_tx[t['id']]['participants'] if p['is_self'])
        elif t['treatment']=='expense' and transaction_type not in ('transfer', 'income', 'refund', 'reimbursement', 'adjustment'):
            amount=t['amount_paise']
        else:amount=0
        personal+=amount
        by_category[category_key]=by_category.get(category_key,0)+amount
    outstanding=sum(p['share_paise']-allocated_to_share(db,p['id']) for s in db['splits'] for p in s['participants'] if not p['is_self'])
    income_received = sum(t['amount_paise'] for t in tx if t['direction']=='credit' and t.get('transaction_type') == 'income')
    return {'gross_paid':gross,'gross_cash_outflow':gross,'credits_received':credits,
            'income_received':income_received,
            'refunds_received':sum(t['amount_paise'] for t in tx if t['direction']=='credit' and t.get('transaction_type') == 'refund'),
            'reimbursements_received':sum(t['amount_paise'] for t in tx if t['direction']=='credit' and t.get('transaction_type') == 'reimbursement'),
            'transfer_out':sum(t['amount_paise'] for t in tx if t['direction']=='debit' and t.get('transaction_type') == 'transfer'),
            'transfer_in':sum(t['amount_paise'] for t in tx if t['direction']=='credit' and t.get('transaction_type') == 'transfer'),
            'net_cash_outflow':gross-credits,
            'savings_rate_percent': round((income_received - personal) / income_received * 100, 2) if income_received else 0,
            'personal_expense':personal,'outstanding':outstanding,'by_category':by_category,
            'review_count':sum(t['needs_review'] for t in tx)}


def account_balances(db):
    balances = {account['id']: account['opening_balance_paise'] for account in db.get('accounts', [])}
    for transaction in db['transactions']:
        if transaction['status'] != 'succeeded' or not transaction.get('account_id'):
            continue
        if transaction['account_id'] not in balances:
            continue
        delta = transaction['amount_paise'] if transaction['direction'] == 'credit' else -transaction['amount_paise']
        balances[transaction['account_id']] += delta
    return balances


def ledger_for_month(db, month):
    try:
        year, month_number = (int(part) for part in month.split('-'))
        start = date(year, month_number, 1)
        end = date(year, month_number, monthrange(year, month_number)[1])
    except (ValueError, TypeError):
        raise ValueError('Month must use YYYY-MM')
    period = copy.deepcopy(db)
    period['transactions'] = [
        transaction for transaction in db['transactions']
        if start <= date.fromisoformat(str(transaction['occurred_at'])[:10]) <= end
    ]
    transaction_ids = {transaction['id'] for transaction in period['transactions']}
    period['splits'] = [split for split in db['splits'] if split['transaction_id'] in transaction_ids]
    share_ids = {participant['id'] for split in period['splits'] for participant in split['participants']}
    period['allocations'] = [allocation for allocation in db['allocations'] if allocation['share_id'] in share_ids or allocation['credit_id'] in transaction_ids]
    return period


def add_recurring_schedule(db, account_id, name, schedule_type, amount_paise, category,
                           next_due_date, frequency='monthly', note=''):
    _account_for(db, account_id)
    if schedule_type not in ('salary', 'bill', 'subscription') or frequency != 'monthly':
        raise ValueError('Only monthly salary, bill, and subscription schedules are supported')
    if amount_paise <= 0:
        raise ValueError('Schedule amount must be positive')
    due = date.fromisoformat(str(next_due_date))
    schedule = {
        'id': uid(), 'account_id': account_id, 'name': name.strip(),
        'schedule_type': schedule_type, 'amount_paise': amount_paise,
        'category': category, 'frequency': frequency, 'next_due_date': due.isoformat(),
        'note': note, 'is_active': True, 'created_at': now(),
    }
    if not schedule['name']:
        raise ValueError('Schedule name required')
    db['recurring_schedules'].append(schedule)
    return schedule


def confirm_recurring_schedule(db, schedule_id, due_date=None):
    schedule = next(s for s in db['recurring_schedules'] if s['id'] == schedule_id)
    if not schedule.get('is_active', True):
        raise ValueError('Schedule is inactive')
    due = date.fromisoformat(str(due_date or schedule['next_due_date']))
    existing = next((posting for posting in db['recurring_postings'] if posting['schedule_id'] == schedule_id and posting['due_date'] == due.isoformat()), None)
    if existing:
        return next(t for t in db['transactions'] if t['id'] == existing['transaction_id'])
    if schedule['schedule_type'] == 'salary':
        transaction = create_income(db, schedule['account_id'], schedule['amount_paise'], schedule['category'], schedule['name'], schedule.get('note', ''), f'SALARY-{schedule_id}-{due.isoformat()}')
    else:
        transaction = create_expense(db, schedule['account_id'], schedule['amount_paise'], schedule['category'], schedule['name'], schedule.get('note', ''), f'RECUR-{schedule_id}-{due.isoformat()}')
        transaction['transaction_type'] = 'expense'
    transaction['occurred_at'] = due.isoformat()
    posting = {'id': uid(), 'schedule_id': schedule_id, 'due_date': due.isoformat(), 'transaction_id': transaction['id'], 'posted_at': now()}
    db['recurring_postings'].append(posting)
    year, month_number = due.year, due.month
    schedule['next_due_date'] = (date(year + (month_number == 12), 1 if month_number == 12 else month_number + 1, 1)).isoformat()
    return transaction


def update_recurring_schedule(db, schedule_id, **changes):
    schedule = next(s for s in db['recurring_schedules'] if s['id'] == schedule_id)
    allowed = {'name', 'schedule_type', 'amount_paise', 'category', 'next_due_date', 'frequency', 'note', 'is_active', 'account_id'}
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(f'Unknown schedule fields: {sorted(unknown)}')
    if 'account_id' in changes:
        _account_for(db, changes['account_id'])
    if 'schedule_type' in changes and changes['schedule_type'] not in ('salary', 'bill', 'subscription'):
        raise ValueError('Invalid schedule type')
    if 'frequency' in changes and changes['frequency'] != 'monthly':
        raise ValueError('Only monthly schedules are supported')
    if 'amount_paise' in changes and changes['amount_paise'] <= 0:
        raise ValueError('Schedule amount must be positive')
    if 'next_due_date' in changes:
        changes['next_due_date'] = date.fromisoformat(str(changes['next_due_date'])).isoformat()
    if 'name' in changes and not str(changes['name']).strip():
        raise ValueError('Schedule name required')
    for key, value in changes.items():
        schedule[key] = value.strip() if isinstance(value, str) and key in {'name', 'category', 'note'} else value
    return schedule


def update_transaction(db, transaction_id, category=None, note=None):
    transaction = next(t for t in db['transactions'] if t['id'] == transaction_id)
    if category is not None:
        if category not in CATEGORIES:
            raise ValueError('Invalid category')
        transaction['category'] = category
        transaction['classification'] = _classification(ClassificationDecision(category, 'user', 1.0, True))
        transaction['confirmed'] = True
        transaction['needs_review'] = False
    if note is not None:
        transaction['note'] = note
    db['events'].append({'id': uid(), 'type': 'transaction_edited', 'transaction_id': transaction_id,
                         'category': transaction['category'], 'note': transaction.get('note', ''), 'at': now()})
    return transaction


def upsert_budget(db, month, category, limit_paise):
    if limit_paise <= 0:
        raise ValueError('Budget limit must be positive')
    budget = next((item for item in db['budgets'] if item['month'] == month and item['category'] == category), None)
    if budget:
        budget['limit_paise'] = limit_paise
        return budget
    budget = {'id': uid(), 'month': month, 'category': category, 'limit_paise': limit_paise, 'created_at': now()}
    db['budgets'].append(budget)
    return budget


def budget_status(db, month):
    period = metrics(ledger_for_month(db, month))
    return [{**budget, 'spent_paise': period['by_category'].get(budget['category'], 0),
             'remaining_paise': budget['limit_paise'] - period['by_category'].get(budget['category'], 0),
             'overspent': period['by_category'].get(budget['category'], 0) > budget['limit_paise']}
            for budget in db['budgets'] if budget['month'] == month]


def create_goal(db, name, target_paise, target_date=None, color='pine'):
    if not name.strip() or target_paise <= 0:
        raise ValueError('Goal name and positive target are required')
    goal = {'id': uid(), 'name': name.strip(), 'target_paise': target_paise,
            'target_date': target_date, 'color': color, 'is_active': True, 'created_at': now()}
    db['goals'].append(goal)
    return goal


def contribute_to_goal(db, goal_id, amount_paise, note=''):
    if amount_paise <= 0:
        raise ValueError('Contribution must be positive')
    next(g for g in db['goals'] if g['id'] == goal_id)
    contribution = {'id': uid(), 'goal_id': goal_id, 'amount_paise': amount_paise, 'contributed_at': now(), 'note': note}
    db['goal_contributions'].append(contribution)
    return contribution

REQUIRED=['date','recipient','amount','direction','reference','note']
def import_csv(db, text, filename='uploaded.csv', account_id=None, source='csv', category_overrides=None,
               transaction_type_overrides=None):
    _account_for(db, account_id)
    reader=csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or any(k not in reader.fieldnames for k in REQUIRED):raise ValueError('CSV must have: '+', '.join(REQUIRED))
    batch = {
        'id': uid(), 'filename': filename, 'imported_at': now(),
        'row_count': 0, 'added': 0, 'skipped': 0, 'error_count': 0,
        'errors': [], 'skipped_rows': [],
    }
    db['import_batches'].append(batch)
    for row in reader:
        batch['row_count'] += 1
        row_number = reader.line_num
        try:
            recipient = row['recipient'].strip()
            if not recipient: raise ValueError('Recipient is required')
            amount = _parse_csv_amount(row['amount'])
            direction=row['direction'].strip().lower()
            if direction not in ('debit','credit'):raise ValueError('Invalid direction')
            occurred_at = _parse_csv_date(row['date'])
            ref = validate_reference(row['reference'])
            if ref and _transaction_for_reference(db, ref):
                batch['skipped'] += 1
                batch['skipped_rows'].append({'row': row_number, 'reason': 'Duplicate external reference', 'reference': ref})
                continue
            decision = classify(db, recipient, row['note'])
            category = decision.category
            row_index = row_number - 2
            if category_overrides and row_index in category_overrides:
                override = category_overrides[row_index]
                if override not in CATEGORIES:
                    raise ValueError(f'Invalid category override: {override}')
                category = override
                decision = ClassificationDecision(override, 'user', 1.0, True)
            # CSV direction is authoritative for cash-flow treatment. Keep
            # classification confidence/review state separate from whether a
            # posted debit is an expense or a posted credit is income.
            transaction_type = (transaction_type_overrides or {}).get(row_index)
            if transaction_type is None:
                transaction_type = 'income' if direction == 'credit' else 'expense'
            if transaction_type not in TRANSACTION_TYPES:
                raise ValueError(f'Invalid transaction type: {transaction_type}')
            treatment = {'transfer': 'transfer', 'refund': 'refund',
                         'reimbursement': 'reimbursement'}.get(transaction_type,
                         'income' if direction == 'credit' else 'expense')
            txn={'id':uid(),'intent_id':None,'external_ref':ref,'recipient':recipient,
                 'merchant': normalize_merchant(recipient, recipient),
                 'amount_paise':amount,'direction':direction,'status':'succeeded','category':category,
                 'transaction_type': transaction_type, 'treatment': treatment,
                 'source': source, 'account_id': account_id, 'note': row.get('note', '').strip(),
                 'confirmed':decision.confirmed,'needs_review':decision.needs_review,
                 'classification':_classification(decision),'occurred_at':occurred_at}
            db['transactions'].append(txn)
            if ref: db['import_keys'].append(f'csv:{ref}')
            batch['added'] += 1
        except (ValueError, TypeError, KeyError, AttributeError) as e:
            batch['error_count'] += 1
            batch['errors'].append({'row': row_number, 'error': str(e)})
    return {
        'batch_id': batch['id'], 'added': batch['added'], 'skipped': batch['skipped'],
        'error_count': batch['error_count'], 'errors': batch['errors'],
        'skipped_rows': batch['skipped_rows'],
    }

def review_transaction(db,txn_id,category,treatment):
    txn=next(t for t in db['transactions'] if t['id']==txn_id)
    if category not in CATEGORIES:raise ValueError('Invalid category')
    if treatment not in ('expense','transfer','loan','reimbursement','income'):raise ValueError('Invalid treatment')
    old=(txn['category'],txn['treatment'])
    decision = ClassificationDecision(category, 'user', 1.0, True)
    txn.update(category=category,treatment=treatment,confirmed=True,needs_review=False,classification=_classification(decision))
    db['events'].append({'id':uid(),'type':'classification_corrected','transaction_id':txn_id,'before':old,'after':(category,treatment),'at':now()})

def weekly_report(db):
    m=metrics(db)
    top=sorted(m['by_category'].items(),key=lambda x:x[1],reverse=True)[:3]
    return {'personal_expense_paise':m['personal_expense'],'gross_paid_paise':m['gross_paid'],
            'outstanding_paise':m['outstanding'],'review_count':m['review_count'],
            'top_categories':[{'name':n,'amount_paise':v} for n,v in top]}
def report_text(db):
    r=weekly_report(db)
    return (f"SpendWise demo report: personal expense {rupees(r['personal_expense_paise'])}; "
            f"gross paid {rupees(r['gross_paid_paise'])}; outstanding reimbursements "
            f"{rupees(r['outstanding_paise'])}; transactions requiring review {r['review_count']}. "
            "Figures are derived from the local ledger, not calculated by AI.")
