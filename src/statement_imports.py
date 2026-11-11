"""Local, deterministic bank-statement parsing and confirmation staging.

This module never writes to the ledger during parsing. It keeps only normalized
preview data in the caller's process memory; callers must discard it after
confirmation or expiry. Passwords and raw statement bytes are never returned
or persisted.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Callable, Optional

from pypdf import PdfReader


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
SUPPORTED_FORMATS = {'csv', 'pdf'}
BankParser = Callable[[bytes, str, Optional[str]], 'StatementPreview']
REQUIRED_CSV_COLUMNS = ('date', 'recipient', 'amount', 'direction', 'reference', 'note')
DATE_RE = re.compile(r'\b(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\b')
JIO_DATE_RE = re.compile(r'\b\d{1,2}-[A-Za-z]{3}-\d{4}\b')
MONEY_RE = re.compile(r'(?<![A-Za-z])(?:₹|INR\s*)?\(?\d[\d,]*(?:\.\d{1,2})?\)?(?![A-Za-z])')
JIO_AMOUNT_RE = re.compile(r'(?<![A-Za-z0-9])(?:₹|INR\s*)?\(?\d[\d,]*\.\d{2}\)?')
REFERENCE_RE = re.compile(r'(?i)\b(?:ref(?:erence)?|utr|txn(?:\s*id)?|transaction(?:\s*id)?)\s*[:#-]?\s*([A-Za-z0-9][A-Za-z0-9._:/-]{2,127})')
ACCOUNT_RE = re.compile(r'(?i)\b(?:a/?c|account|acct)\s*(?:no\.?\s*)?[:#-]?\s*([Xx*]{2,}\d{2,}|\d{4,})')
TRANSFER_RE = re.compile(r'(?i)\b(?:own\s+account|self\s+transfer|internal\s+transfer|transfer\s+between\s+accounts)\b')
REFUND_RE = re.compile(r'(?i)\b(?:refund|reversal|chargeback)\b')
REIMBURSEMENT_RE = re.compile(r'(?i)\b(?:reimbursement|repaid|repayment\s+from)\b')


@dataclass
class StatementRow:
    date: str
    description: str
    amount_paise: int
    direction: str
    reference: Optional[str]
    running_balance_paise: Optional[int]
    page: Optional[int] = None
    category: str = 'Uncategorized'
    confidence: Optional[float] = None
    transaction_type: str = 'expense'
    warnings: list[str] = field(default_factory=list)

    def as_preview(self, index: int):
        return {
            'index': index,
            'date': self.date,
            'description': self.description,
            'amount_paise': self.amount_paise,
            'direction': self.direction,
            'reference': self.reference,
            'running_balance_paise': self.running_balance_paise,
            'page': self.page,
            'category': self.category,
            'confidence': self.confidence,
            'transaction_type': self.transaction_type,
            'warnings': self.warnings,
        }


@dataclass
class StatementPreview:
    filename: str
    format: str
    rows: list[StatementRow]
    errors: list[dict]
    warnings: list[str]
    account_identifier: Optional[str] = None
    page_count: int = 1
    balance_reconciled: Optional[bool] = None
    opening_balance_paise: Optional[int] = None
    closing_balance_paise: Optional[int] = None
    reconciliation: dict = field(default_factory=dict)
    confirmation_blocked: bool = False
    complete_preview: bool = False

    def as_response(self, token: str):
        return {
            'token': token,
            'filename': self.filename,
            'format': self.format,
            'page_count': self.page_count,
            'row_count': len(self.rows),
            'preview_rows': [row.as_preview(index) for index, row in enumerate(self.rows if self.complete_preview else self.rows[:10])],
            'errors': self.errors,
            'warnings': self.warnings,
            'account_identifier': self.account_identifier,
            'balance_reconciled': self.balance_reconciled,
            'opening_balance_paise': self.opening_balance_paise,
            'closing_balance_paise': self.closing_balance_paise,
            'reconciliation': self.reconciliation,
            'requires_confirmation': not self.errors and not self.confirmation_blocked,
            'confirmation_blocked': self.confirmation_blocked,
            'complete_preview': self.complete_preview,
        }


class StatementParseError(ValueError):
    pass


# Bank-specific parsers can be registered without changing the ledger or API.
# The generic parser remains the safe default when no bank is selected.
BANK_PARSERS: dict[str, BankParser] = {}


def register_bank_parser(name: str, parser: BankParser) -> None:
    if not name.strip():
        raise ValueError('Bank parser name is required')
    BANK_PARSERS[name.strip().lower()] = parser


def _money_to_paise(value: str) -> int:
    cleaned = value.replace('₹', '').replace('INR', '').replace(',', '').replace('(', '-').replace(')', '').strip()
    try:
        amount = Decimal(cleaned)
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f'Invalid amount: {value}') from error
    if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2:
        raise ValueError(f'Invalid amount: {value}')
    return int(amount * 100)


def _date_iso(value: str) -> str:
    raw = value.strip()
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%d-%m-%Y', '%d/%m/%Y', '%d-%m-%y', '%d/%m/%y'):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f'Invalid date: {value}')


def _reference(line: str) -> Optional[str]:
    match = REFERENCE_RE.search(line)
    return match.group(1) if match else None


def _account_identifier(text: str) -> Optional[str]:
    match = ACCOUNT_RE.search(text)
    return match.group(1) if match else None


def _transaction_type(description: str, direction: str) -> str:
    if TRANSFER_RE.search(description):
        return 'transfer'
    if REFUND_RE.search(description) and direction == 'credit':
        return 'refund'
    if REIMBURSEMENT_RE.search(description) and direction == 'credit':
        return 'reimbursement'
    return 'income' if direction == 'credit' else 'expense'


def _balance_reconciliation(rows: list[StatementRow], opening: Optional[int]):
    if not rows:
        return None, None
    closing = rows[-1].running_balance_paise
    if opening is None or closing is None or any(row.running_balance_paise is None for row in rows):
        return None, closing
    expected = opening
    for row in rows:
        expected += row.amount_paise if row.direction == 'credit' else -row.amount_paise
        if expected != row.running_balance_paise:
            return False, closing
    return True, closing


def parse_csv_statement(data: bytes, filename: str) -> StatementPreview:
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError as error:
        raise StatementParseError('CSV must be UTF-8 encoded') from error
    reader = csv.DictReader(io.StringIO(text))
    headers = [header.strip().lower() for header in (reader.fieldnames or [])]
    missing = [column for column in REQUIRED_CSV_COLUMNS if column not in headers]
    if missing:
        return StatementPreview(filename, 'csv', [], [{'row': 1, 'error': f'Missing required columns: {", ".join(missing)}'}], [])
    rows: list[StatementRow] = []
    errors: list[dict] = []
    for row_number, row in enumerate(reader, start=2):
        try:
            direction = (row.get('direction') or '').strip().lower()
            if direction not in ('debit', 'credit'):
                raise ValueError('Direction must be debit or credit')
            description = (row.get('recipient') or '').strip()
            if not description:
                raise ValueError('Recipient/description is required')
            reference = (row.get('reference') or '').strip() or None
            rows.append(StatementRow(
                date=_date_iso(row.get('date', '')),
                description=description,
                amount_paise=_money_to_paise(row.get('amount', '')),
                direction=direction,
                reference=reference,
                running_balance_paise=None,
                transaction_type=_transaction_type(description, direction),
                warnings=['Reference is missing; duplicate matching will be conservative'] if not reference else [],
            ))
        except (ValueError, TypeError) as error:
            errors.append({'row': row_number, 'error': str(error)})
    return StatementPreview(filename, 'csv', rows, errors, [])


def _parse_pdf_line(line: str, page: int) -> StatementRow:
    date_match = DATE_RE.search(line)
    if not date_match:
        raise ValueError('No transaction date found')
    occurred = _date_iso(date_match.group(0))
    remainder = line[date_match.end():].strip(' |\t')
    reference = _reference(remainder)
    remainder_without_ref = REFERENCE_RE.sub('', remainder).strip(' |\t')
    labelled = re.findall(r'(?i)\b(debit|credit|dr|cr)\b\s*[:=-]?\s*(₹?\(?[\d,]+(?:\.\d{1,2})?\)?)', remainder_without_ref)
    balance_match = re.search(r'(?i)\b(?:balance|closing)\s*[:=-]?\s*(₹?\(?[\d,]+(?:\.\d{1,2})?\)?)', remainder_without_ref)
    balance = _money_to_paise(balance_match.group(1)) if balance_match else None
    if labelled:
        direction_word, amount_text = labelled[0]
        direction = 'credit' if direction_word.lower() in ('credit', 'cr') else 'debit'
        amount = _money_to_paise(amount_text)
        description = re.sub(r'(?i)\b(debit|credit|dr|cr)\b\s*[:=-]?\s*(₹?\(?[\d,]+(?:\.\d{1,2})?\)?)', '', remainder_without_ref)
    else:
        numbers = MONEY_RE.findall(remainder_without_ref)
        if not numbers:
            raise ValueError('No debit or credit amount found')
        amount = _money_to_paise(numbers[0])
        direction = 'credit' if re.search(r'(?i)\b(?:credit|cr)\b', remainder_without_ref) else 'debit'
        if len(numbers) >= 2 and balance is None:
            balance = _money_to_paise(numbers[-1])
        description = remainder_without_ref
        for number in numbers:
            description = description.replace(number, '', 1)
    description = re.sub(r'\s+', ' ', description).strip(' |\t-')
    if not description:
        raise ValueError('Description is required')
    warnings = ['Reference not found; duplicate matching will be conservative'] if not reference else []
    return StatementRow(occurred, description, amount, direction, reference, balance, page=page,
                        transaction_type=_transaction_type(description, direction), warnings=warnings)


def parse_pdf_statement(data: bytes, filename: str, password: Optional[str] = None) -> StatementPreview:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            if not password:
                raise StatementParseError('This PDF is password protected')
            if reader.decrypt(password) == 0:
                raise StatementParseError('Incorrect PDF password')
    except StatementParseError:
        raise
    except Exception as error:
        raise StatementParseError('Could not read PDF') from error
    pages_text = []
    account_identifier = None
    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ''
        pages_text.append(text)
        account_identifier = account_identifier or _account_identifier(text)
    rows: list[StatementRow] = []
    errors: list[dict] = []
    opening = None
    full_text = '\n'.join(pages_text)
    opening_match = re.search(r'(?i)\b(?:opening|available)\s+balance\s*[:=-]?\s*(₹?\(?[\d,]+(?:\.\d{1,2})?\)?)', full_text)
    if opening_match:
        opening = _money_to_paise(opening_match.group(1))
    for page_number, text in enumerate(pages_text, start=1):
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not DATE_RE.search(line):
                continue
            try:
                rows.append(_parse_pdf_line(line, page_number))
            except ValueError as error:
                errors.append({'page': page_number, 'row': line_number, 'error': str(error)})
    reconciled, closing = _balance_reconciliation(rows, opening)
    warnings = []
    if reconciled is False:
        warnings.append('Running balance does not reconcile with the extracted debit/credit rows')
    if not rows and not errors:
        errors.append({'error': 'No dated transaction rows found; scanned PDFs are not supported'})
    return StatementPreview(filename, 'pdf', rows, errors, warnings, account_identifier, len(reader.pages), reconciled, opening, closing)


@dataclass
class _JioFragment:
    text: str
    x: float
    y: float
    page: int
    width: float


def _jio_visual_lines(reader: PdfReader) -> list[list[_JioFragment]]:
    lines: list[list[_JioFragment]] = []
    for page_number, page in enumerate(reader.pages, start=1):
        width = float(page.mediabox.width)
        fragments: list[_JioFragment] = []

        def visitor(text, _cm, tm, _font, font_size):
            if not text or not text.strip():
                return
            for offset, part in enumerate(text.splitlines()):
                if part.strip():
                    fragments.append(_JioFragment(part.strip(), float(tm[4]), float(tm[5]) - offset * float(font_size or 10), page_number, width))

        page.extract_text(visitor_text=visitor)
        fragments.sort(key=lambda item: (-item.y, item.x))
        page_lines: list[list[_JioFragment]] = []
        for fragment in fragments:
            if page_lines and abs(page_lines[-1][0].y - fragment.y) <= 3.5:
                page_lines[-1].append(fragment)
            else:
                page_lines.append([fragment])
        lines.extend(page_lines)
    for line in lines:
        line.sort(key=lambda item: item.x)
    return lines


def _jio_money_cell(fragments: list[_JioFragment]) -> Optional[int]:
    values = []
    for fragment in fragments:
        for value in MONEY_RE.findall(fragment.text):
            cleaned = value.replace('₹', '').replace('INR', '').replace(',', '').replace('(', '-').replace(')', '').strip()
            try:
                amount = Decimal(cleaned)
            except InvalidOperation:
                continue
            if amount.is_finite() and amount >= 0 and amount.as_tuple().exponent >= -2:
                values.append(int(amount * 100))
    return values[-1] if values else None


def _jio_money_text(value: str) -> int:
    cleaned = value.replace('₹', '').replace('INR', '').replace(',', '').replace('(', '-').replace(')', '').strip()
    amount = Decimal(cleaned)
    if not amount.is_finite() or amount < 0 or amount.as_tuple().exponent < -2:
        raise ValueError(f'Invalid Jio amount: {value}')
    return int(amount * 100)


def _jio_flattened_row(fragments: list[_JioFragment]):
    """Fallback for PDF producers that flatten a whole table row into one text chunk."""
    combined = re.sub(r'\s+', ' ', ' '.join(fragment.text for fragment in fragments)).strip()
    dates = list(JIO_DATE_RE.finditer(combined))
    if len(dates) < 2:
        raise ValueError('Jio row is missing transaction and value dates')
    values = list(JIO_AMOUNT_RE.finditer(combined[dates[1].end():]))
    if len(values) < 3:
        raise ValueError('Jio row is missing withdrawal, deposit, or closing balance')
    cells = values[-3:]
    start = dates[1].end() + cells[0].start()
    narration = combined[dates[1].end():start].strip(' -')
    withdrawal, deposit, closing = (_jio_money_text(match.group(0)) for match in cells)
    return narration, withdrawal, deposit, closing


def _jio_date(text: str) -> Optional[str]:
    match = JIO_DATE_RE.search(text)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(0), '%d-%b-%Y').date().isoformat()
    except ValueError:
        return None


def _jio_reference(narration: str) -> Optional[str]:
    match = re.search(r'(?i)\bUPI\s*/\s*(?:DR|CR|MANDATE)\s*/\s*([A-Za-z0-9]+)', narration)
    return f'JIO-UPI-{match.group(1)}' if match else _reference(narration)


def _jio_ignored(text: str) -> bool:
    normalized = re.sub(r'\s+', ' ', text).strip().lower()
    if not normalized:
        return True
    # The PDF places a non-transaction TOTAL row after the last table row and
    # appends account notes on the final page.  Some of those notes contain
    # dates, so they must be excluded before row grouping.
    if normalized == 'total' or normalized.startswith('total '):
        return True
    return any(marker in normalized for marker in (
        'transaction date', 'value date', 'narration', 'withdrawals', 'deposits',
        'closing balance', 'jio finance', 'jio payments bank limited',
        'registered address', 'page ', 'statement for account', 'account statement',
        'opening balance', 'total withdrawals', 'total deposits', 'no. of withdrawals',
        'no. of deposits', 'account opening date', 'legend for transactions',
        'neft -', 'imps -', 'rtgs -', 'upi -', 'contents of this statement',
        'deposits in the same', 'computer generated statement', 'reach us at',
    ))


def _jio_transaction_type(narration: str, direction: str) -> str:
    if TRANSFER_RE.search(narration):
        return 'transfer'
    if REFUND_RE.search(narration) and direction == 'credit':
        return 'refund'
    if REIMBURSEMENT_RE.search(narration) and direction == 'credit':
        return 'reimbursement'
    # A Jio credit is deliberately not treated as salary or income without
    # user confirmation. Adjustment keeps it visible and reviewable.
    return 'adjustment' if direction == 'credit' else 'expense'


def _jio_summary_value(text: str, label: str) -> Optional[int]:
    match = re.search(label + r'.{0,140}?(' + MONEY_RE.pattern + r')', text, re.IGNORECASE | re.DOTALL)
    return _jio_money_cell([_JioFragment(match.group(1), 0, 0, 0, 1)]) if match else None


def _jio_summary_count(text: str, label: str) -> Optional[int]:
    match = re.search(label + r'.{0,80}?(\d+)', text, re.IGNORECASE | re.DOTALL)
    return int(match.group(1)) if match else None


def _jio_summary(lines: list[list[_JioFragment]]):
    # Use the page's flattened text for the summary.  Coordinate extraction is
    # intentionally used for rows, but the PDF renderer interleaves the
    # summary labels/value row with table rows at identical Y coordinates.
    # Reconstructing the summary from visual lines can therefore attach the
    # summary figures to a transaction.  The raw first-page text has a stable
    # summary block bounded by OPENING BALANCE and TRANSACTION DATE.
    if isinstance(lines, str):
        text = re.sub(r'\s+', ' ', lines).strip()
    else:
        pretable = []
        for line in lines:
            line_text = ' '.join(fragment.text for fragment in line)
            if 'transaction date' in line_text.lower():
                break
            pretable.append(line_text)
        text = re.sub(r'\s+', ' ', ' '.join(pretable)).strip()
    start = re.search(r'opening\s+balance|total\s+withdrawals', text, re.IGNORECASE)
    if not start:
        return None, None, None, None, None, None
    text = text[start.start():]
    table_start = re.search(r'transaction\s+date', text, re.IGNORECASE)
    if table_start:
        text = text[:table_start.start()]
    amounts = [_jio_money_text(match.group(0)) for match in JIO_AMOUNT_RE.finditer(text)]
    # Jio renders summary labels as one row and values as a second row on
    # some PDF versions. Decimal values therefore map by column order, not by
    # proximity to the label text.
    opening = amounts[0] if len(amounts) >= 1 else None
    withdrawals = amounts[1] if len(amounts) >= 2 else None
    deposits = amounts[2] if len(amounts) >= 3 else None
    closing = amounts[-1] if len(amounts) >= 4 else None
    # The statement prints all summary labels first and all values on the next
    # line, so label-proximity regexes can accidentally capture 4231 from the
    # opening amount.  The summary block has four decimal amounts followed by
    # the two integer counts; parse those positions explicitly.
    integers = [int(value) for value in re.findall(r'(?<![\d.])\d+(?![\d.])', text)]
    withdrawal_count, deposit_count = (integers[-2:] if len(integers) >= 2 else (None, None))
    return opening, withdrawals, deposits, withdrawal_count, deposit_count, closing


def _jio_line_date(line: list[_JioFragment]) -> Optional[str]:
    text = ' '.join(fragment.text for fragment in line)
    dates = [match.group(0) for fragment in line for match in JIO_DATE_RE.finditer(fragment.text)]
    # A transaction row always has transaction date and value date.  Requiring
    # both prevents account-opening dates and dates in explanatory notes from
    # becoming synthetic transactions.
    if len(dates) < 2:
        return None
    return _jio_date(dates[0])


def parse_jio_payments_bank_statement(data: bytes, filename: str, password: Optional[str] = None) -> StatementPreview:
    """Parse Jio Finance/Jio Payments Bank's coordinate-based statement table."""
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            if not password:
                raise StatementParseError('This PDF is password protected')
            if reader.decrypt(password) == 0:
                raise StatementParseError('Incorrect PDF password')
    except StatementParseError:
        raise
    except Exception as error:
        raise StatementParseError('Could not read Jio Payments Bank PDF') from error

    lines = _jio_visual_lines(reader)
    raw_pages = [page.extract_text() or '' for page in reader.pages]
    full_text = '\n'.join(' '.join(fragment.text for fragment in line) for line in lines)
    account_match = re.search(r'(?i)account\s*(?:no\.?|number)?\s*[:#-]?\s*(\d{4,})', full_text)
    account_identifier = f'•••• {account_match.group(1)[-4:]}' if account_match else None
    opening, summary_withdrawals, summary_deposits, summary_withdrawal_count, summary_deposit_count, summary_closing = _jio_summary(raw_pages[0] if raw_pages else '')

    row_groups: list[list[_JioFragment]] = []
    current: list[_JioFragment] = []
    for line in lines:
        text = ' '.join(fragment.text for fragment in line)
        date = _jio_line_date(line)
        if date:
            if current:
                row_groups.append(current)
            current = list(line)
        elif current and not _jio_ignored(text):
            current.extend(line)
    if current:
        row_groups.append(current)

    rows: list[StatementRow] = []
    errors: list[dict] = []
    for row_number, fragments in enumerate(row_groups, start=1):
        date = _jio_line_date(fragments)
        if not date:
            errors.append({'row': row_number, 'error': 'Transaction date could not be identified'})
            continue
        width = fragments[0].width
        missing_fields = []
        if len({round(fragment.x, 1) for fragment in fragments}) < 4:
            try:
                narration, withdrawal, deposit, closing = _jio_flattened_row(fragments)
            except ValueError as error:
                errors.append({'page': fragments[0].page, 'row': row_number, 'error': str(error)})
                continue
        else:
            narration_fragments = [fragment for fragment in fragments if 0.25 <= fragment.x / width < 0.64]
            withdrawal_fragments = [fragment for fragment in fragments if 0.64 <= fragment.x / width < 0.79]
            deposit_fragments = [fragment for fragment in fragments if 0.79 <= fragment.x / width < 0.91]
            closing_fragments = [fragment for fragment in fragments if fragment.x / width >= 0.91]
            narration = ' '.join(fragment.text for fragment in narration_fragments)
            narration = JIO_DATE_RE.sub('', narration)
            narration = re.sub(r'\s+', ' ', narration).strip(' -')
            withdrawal = _jio_money_cell(withdrawal_fragments)
            deposit = _jio_money_cell(deposit_fragments)
            closing = _jio_money_cell(closing_fragments)
            if not narration:
                missing_fields.append('narration')
            if withdrawal is None:
                missing_fields.append('withdrawals')
            if deposit is None:
                missing_fields.append('deposits')
            if closing is None:
                missing_fields.append('closing balance')
            if missing_fields:
                try:
                    narration, withdrawal, deposit, closing = _jio_flattened_row(fragments)
                    missing_fields = []
                except ValueError:
                    pass
        if not narration or withdrawal is None or deposit is None or closing is None:
            if not missing_fields:
                if not narration: missing_fields.append('narration')
                if withdrawal is None: missing_fields.append('withdrawals')
                if deposit is None: missing_fields.append('deposits')
                if closing is None: missing_fields.append('closing balance')
            errors.append({'page': fragments[0].page, 'row': row_number, 'error': f'Jio row missing fields: {", ".join(missing_fields)}'})
            continue
        if (withdrawal > 0) == (deposit > 0):
            try:
                fallback_narration, fallback_withdrawal, fallback_deposit, fallback_closing = _jio_flattened_row(fragments)
                if (fallback_withdrawal > 0) != (fallback_deposit > 0):
                    narration, withdrawal, deposit, closing = fallback_narration, fallback_withdrawal, fallback_deposit, fallback_closing
            except ValueError:
                pass
        if (withdrawal > 0) == (deposit > 0):
            errors.append({'page': fragments[0].page, 'row': row_number, 'error': 'Jio row has ambiguous debit/credit columns'})
            continue
        direction = 'debit' if withdrawal > 0 else 'credit'
        amount = withdrawal if direction == 'debit' else deposit
        transaction_type = _jio_transaction_type(narration, direction)
        warnings = []
        if direction == 'credit' and transaction_type == 'adjustment':
            warnings.append('Credit requires classification review; it was not treated as salary or income')
        reference = _jio_reference(narration)
        if not reference:
            warnings.append('Reference not found; duplicate matching will be conservative')
        rows.append(StatementRow(date, narration, amount, direction, reference, closing,
                                 page=fragments[0].page, transaction_type=transaction_type, warnings=warnings))

    balance_reconciled, extracted_closing = _balance_reconciliation(rows, opening)
    withdrawals = sum(row.amount_paise for row in rows if row.direction == 'debit')
    deposits = sum(row.amount_paise for row in rows if row.direction == 'credit')
    discrepancies = []
    checks = {
        'opening_balance_paise': opening,
        'summary_withdrawals_paise': summary_withdrawals,
        'extracted_withdrawals_paise': withdrawals,
        'summary_deposits_paise': summary_deposits,
        'extracted_deposits_paise': deposits,
        'summary_withdrawal_count': summary_withdrawal_count,
        'extracted_withdrawal_count': sum(row.direction == 'debit' for row in rows),
        'summary_deposit_count': summary_deposit_count,
        'extracted_deposit_count': sum(row.direction == 'credit' for row in rows),
        'summary_closing_balance_paise': summary_closing,
        'extracted_closing_balance_paise': extracted_closing,
    }
    for summary_key, extracted_key, label in (
        ('summary_withdrawals_paise', 'extracted_withdrawals_paise', 'withdrawal total'),
        ('summary_deposits_paise', 'extracted_deposits_paise', 'deposit total'),
        ('summary_withdrawal_count', 'extracted_withdrawal_count', 'withdrawal count'),
        ('summary_deposit_count', 'extracted_deposit_count', 'deposit count'),
        ('summary_closing_balance_paise', 'extracted_closing_balance_paise', 'closing balance'),
    ):
        if checks[summary_key] is not None and checks[extracted_key] != checks[summary_key]:
            discrepancies.append(f'{label}: statement={checks[summary_key]}, extracted={checks[extracted_key]}')
    if balance_reconciled is False:
        discrepancies.append('running balance does not reconcile with opening balance and rows')
    warnings = [f'Jio reconciliation discrepancy: {item}' for item in discrepancies]
    if not rows and not errors:
        errors.append({'error': 'No Jio transaction rows found'})
    return StatementPreview(filename, 'pdf', rows, errors, warnings, account_identifier,
                            len(reader.pages), balance_reconciled, opening,
                            summary_closing if summary_closing is not None else extracted_closing,
                            reconciliation=checks, confirmation_blocked=bool(errors or discrepancies),
                            complete_preview=True)


register_bank_parser('jio_payments_bank', parse_jio_payments_bank_statement)


def parse_statement(data: bytes, filename: str, password: Optional[str] = None, bank: Optional[str] = None) -> StatementPreview:
    if len(data) > MAX_UPLOAD_BYTES:
        raise StatementParseError(f'File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit')
    suffix = filename.lower().rsplit('.', 1)[-1] if '.' in filename else ''
    if bank and bank.strip().lower() in BANK_PARSERS:
        return BANK_PARSERS[bank.strip().lower()](data, filename, password)
    if suffix == 'csv':
        return parse_csv_statement(data, filename)
    if suffix == 'pdf':
        try:
            probe = PdfReader(io.BytesIO(data))
            if probe.is_encrypted and password:
                probe.decrypt(password)
            probe_text = '\n'.join((page.extract_text() or '') for page in probe.pages[:2])
            if re.search(r'(?i)jio\s+(?:finance|payments\s+bank)', probe_text):
                return BANK_PARSERS['jio_payments_bank'](data, filename, password)
        except Exception:
            pass
        return parse_pdf_statement(data, filename, password)
    raise StatementParseError('Only .csv and text-based .pdf files are supported')


def preview_to_csv(preview: StatementPreview, categories: Optional[dict[int, str]] = None) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(REQUIRED_CSV_COLUMNS)
    for index, row in enumerate(preview.rows):
        category = (categories or {}).get(index, row.category)
        note = f'{row.description}; source={preview.format}; page={row.page or "n/a"}; category={category}; transaction_type={row.transaction_type}'
        writer.writerow([row.date, row.description, f'{row.amount_paise / 100:.2f}', row.direction, row.reference or '', note])
    return output.getvalue()
