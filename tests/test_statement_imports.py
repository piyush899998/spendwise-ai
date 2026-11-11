from io import BytesIO
from datetime import date, timedelta

from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter

from src.statement_imports import parse_jio_payments_bank_statement, parse_pdf_statement, parse_statement


def pdf_with_pages(page_lines):
    objects = [
        '<< /Type /Catalog /Pages 2 0 R >>',
        '',
        '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    ]
    page_ids = []
    for lines in page_lines:
        stream = 'BT /F1 10 Tf 40 760 Td ' + ' '.join(
            f'({line.replace("(", "[").replace(")", "]")}) Tj 0 -18 Td' for line in lines
        ) + ' ET'
        content_id = len(objects) + 1
        page_id = content_id + 1
        objects.append(f'<< /Length {len(stream.encode())} >>\nstream\n{stream}\nendstream')
        objects.append('<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents '
                       f'{content_id} 0 R >>')
        page_ids.append(page_id)
    kids = ' '.join(f'{page_id} 0 R' for page_id in page_ids)
    objects[1] = f'<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>'
    body = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body.extend(f'{index} 0 obj\n{obj}\nendobj\n'.encode())
    xref = len(body)
    body.extend(f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode())
    body.extend(''.join(f'{offset:010d} 00000 n \n' for offset in offsets[1:]).encode())
    body.extend(f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    return bytes(body)


def encrypted(pdf_bytes, password):
    reader = PdfReader(BytesIO(pdf_bytes))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt(password)
    result = BytesIO()
    writer.write(result)
    return result.getvalue()


def jio_pdf(rows_by_page, summary, page_notes=None):
    objects = [
        '<< /Type /Catalog /Pages 2 0 R >>', '',
        '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    ]
    page_ids = []
    for page_index, rows in enumerate(rows_by_page):
        commands = ['BT /F1 8 Tf']
        if page_index == 0:
            commands.append('1 0 0 1 40 760 Tm (Jio Finance Account Statement Account No: DEMO-ACCOUNT-0001) Tj')
            commands.append('1 0 0 1 40 735 Tm (Opening Balance 1000.00 Total Withdrawals 5895.49 Total Deposits 5491.35 No. of Withdrawals 64 No. of Deposits 20 Closing Balance 595.86) Tj')
        commands.append('1 0 0 1 40 700 Tm (TRANSACTION DATE VALUE DATE NARRATION WITHDRAWALS DEPOSITS CLOSING BALANCE) Tj')
        for row_index, row in enumerate(rows):
            y = 675 - row_index * 15
            commands.extend([
                f'1 0 0 1 40 {y} Tm ({row[0]}) Tj',
                f'1 0 0 1 130 {y} Tm ({row[0]}) Tj',
                f'1 0 0 1 220 {y} Tm ({row[1]}) Tj',
                f'1 0 0 1 450 {y} Tm ({row[2]}) Tj',
                f'1 0 0 1 540 {y} Tm ({row[3]}) Tj',
                f'1 0 0 1 640 {y} Tm ({row[4]}) Tj',
            ])
            if row[5]:
                commands.append(f'1 0 0 1 220 {y - 8} Tm ({row[5]}) Tj')
        for note_index, note in enumerate((page_notes or {}).get(page_index, [])):
            commands.append(f'1 0 0 1 40 {120 - note_index * 14} Tm ({note}) Tj')
        commands.append('ET')
        stream = '\n'.join(commands)
        content_id = len(objects) + 1
        page_id = content_id + 1
        objects.append(f'<< /Length {len(stream.encode())} >>\nstream\n{stream}\nendstream')
        objects.append('<< /Type /Page /Parent 2 0 R /MediaBox [0 0 700 800] /Resources << /Font << /F1 3 0 R >> >> /Contents '
                       f'{content_id} 0 R >>')
        page_ids.append(page_id)
    objects[1] = f'<< /Type /Pages /Kids [{" ".join(f"{page_id} 0 R" for page_id in page_ids)}] /Count {len(page_ids)} >>'
    body = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(body)); body.extend(f'{index} 0 obj\n{obj}\nendobj\n'.encode())
    xref = len(body)
    body.extend(f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode())
    body.extend(''.join(f'{offset:010d} 00000 n \n' for offset in offsets[1:]).encode())
    body.extend(f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    return bytes(body)


def test_multi_page_pdf_extracts_rows_account_and_reconciliation():
    data = pdf_with_pages([
        ['Account XXXX1234', 'Opening Balance: 1000.00', '2026-01-01 Payroll credit 500.00 balance 1500.00 REF: PAY-1'],
        ['2026-01-02 Cafe debit 125.00 balance 1375.00 REF: EXP-1'],
    ])
    preview = parse_pdf_statement(data, 'statement.pdf')
    assert preview.page_count == 2
    assert preview.account_identifier == 'XXXX1234'
    assert len(preview.rows) == 2
    assert preview.rows[0].direction == 'credit'
    assert preview.rows[1].direction == 'debit'
    assert preview.balance_reconciled is True
    assert preview.rows[0].reference == 'PAY-1'


def test_password_protected_pdf_requires_temporary_password():
    data = encrypted(pdf_with_pages([['2026-01-01 Payroll credit 500.00 balance 500.00 REF: PAY-1']]), 'secret')
    try:
        parse_statement(data, 'locked.pdf')
        assert False, 'expected password error'
    except ValueError as error:
        assert 'password' in str(error).lower()
    assert len(parse_statement(data, 'locked.pdf', password='secret').rows) == 1


def test_malformed_pdf_is_rejected_without_inventing_rows():
    try:
        parse_statement(b'not a pdf', 'broken.pdf')
        assert False, 'expected parse error'
    except ValueError as error:
        assert 'pdf' in str(error).lower()


def test_csv_preview_requires_columns_and_preserves_repeated_transactions():
    bad = parse_statement(b'date,recipient\n2026-01-01,Store\n', 'broken.csv')
    assert bad.errors and 'amount' in bad.errors[0]['error']
    good = parse_statement(
        b'date,recipient,amount,direction,reference,note\n2026-01-01,Store,10.00,debit,REF-1,groceries\n',
        'statement.csv',
    )
    assert len(good.rows) == 1
    assert good.rows[0].amount_paise == 1000


def test_api_preview_does_not_post_until_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv('SPENDWISE_DATABASE_URL', f'sqlite:///{tmp_path / "statement-api.db"}')
    from backend.main import app

    client = TestClient(app)
    data = pdf_with_pages([['Account XXXX9876', 'Opening Balance: 1000.00', '2026-01-01 Payroll credit 500.00 balance 1500.00 REF: PAY-1']])
    preview = client.post('/api/imports/preview', files={'file': ('statement.pdf', data, 'application/pdf')})
    assert preview.status_code == 200
    payload = preview.json()
    assert payload['row_count'] == 1
    assert client.get('/api/transactions').json()['transactions'] == []
    confirmed = client.post(f"/api/imports/{payload['token']}/confirm", json={'category_corrections': {'0': 'Other'}})
    assert confirmed.status_code == 200
    assert confirmed.json()['added'] == 1
    assert client.get('/api/transactions').json()['transactions'][0]['source'] == 'pdf'


def test_repeated_pdf_confirmation_is_duplicate_safe_and_transfer_is_not_expense(tmp_path, monkeypatch):
    monkeypatch.setenv('SPENDWISE_DATABASE_URL', f'sqlite:///{tmp_path / "repeat.db"}')
    from backend.main import app

    client = TestClient(app)
    data = pdf_with_pages([[
        '2026-01-01 Own account transfer debit 500.00 balance 500.00 REF: MOVE-1',
    ]])
    first = client.post('/api/imports/preview', files={'file': ('statement.pdf', data, 'application/pdf')}).json()
    second = client.post('/api/imports/preview', files={'file': ('statement.pdf', data, 'application/pdf')}).json()
    assert client.post(f"/api/imports/{first['token']}/confirm", json={'category_corrections': {}}).json()['added'] == 1
    repeated = client.post(f"/api/imports/{second['token']}/confirm", json={'category_corrections': {}}).json()
    assert repeated['added'] == 0
    assert repeated['skipped'] == 1
    transaction = client.get('/api/transactions').json()['transactions'][0]
    assert transaction['transaction_type'] == 'transfer'
    assert transaction['treatment'] == 'transfer'


def test_statement_preview_does_not_call_bank_parser_for_unknown_bank():
    preview = parse_statement(
        b'date,recipient,amount,direction,reference,note\n2026-01-01,Refund shop,10.00,credit,REF-2,refund\n',
        'statement.csv',
    )
    assert preview.rows[0].transaction_type == 'refund'


def test_jio_parser_reconciles_mult_page_summary_and_wrapped_narrations():
    rows = []
    balance = 100000
    for index in range(84):
        occurred = date(2026, 9, 1) + timedelta(days=index % 30)
        is_credit = 1 <= index <= 20
        credit_index = index - 1
        debit_index = index if index == 0 else index - 20
        amount = (549135 - 19 if credit_index == 19 else 1) if is_credit else (589549 - 63 if debit_index == 63 else 1)
        balance += amount if is_credit else -amount
        prefix = 'UPI/CR/111111111111/SYNTHETIC PERSON/NESF/Payment' if is_credit else 'UPI/DR/222222222222/SYNTHETIC GROCER/HDFC/demo/Payment'
        if index == 0:
            prefix = 'Quarterly A/c Subscription Fee Jun-2026 to Aug-2026'
        rows.append((occurred.strftime('%d-%b-%Y'), prefix[:42], f'{0 if is_credit else amount / 100:,.2f}', f'{amount / 100:,.2f}' if is_credit else '0.00', f'{balance / 100:,.2f}', prefix[42:] if len(prefix) > 42 else ''))
    data = jio_pdf(
        [rows[index:index + 14] for index in range(0, len(rows), 14)],
        {},
        page_notes={4: ['Total 5,895.49 5,491.35'], 5: ['Account opening date: 28-Apr-2024', 'Legend for transactions in your account statement']},
    )
    preview = parse_jio_payments_bank_statement(data, 'jio-september.pdf')
    assert preview.page_count == 6
    assert len(preview.rows) == 84
    assert preview.reconciliation['extracted_withdrawal_count'] == 64
    assert preview.reconciliation['extracted_deposit_count'] == 20
    assert preview.reconciliation['extracted_withdrawals_paise'] == 589549
    assert preview.reconciliation['extracted_deposits_paise'] == 549135
    assert preview.reconciliation['extracted_closing_balance_paise'] == 59586
    assert preview.opening_balance_paise == 100000
    assert preview.closing_balance_paise == 59586
    assert preview.confirmation_blocked is False
    assert preview.complete_preview is True
    assert 'Subscription' in preview.rows[0].description
    references = [row.reference for row in preview.rows if row.reference]
    assert len(set(references)) < len(references)
    assert preview.rows[0].transaction_type == 'expense'
    assert any(row.direction == 'credit' and row.transaction_type == 'adjustment' for row in preview.rows)


def test_jio_parser_auto_detects_bank_and_blocks_inconsistent_statement():
    data = jio_pdf([[
        ('01-Sep-2026', 'UPI/DR/123456789012/Netflix/JIOP', '10.00', '0.00', '90.00', 'Subscription'),
    ]], {})
    preview = parse_statement(data, 'statement.pdf')
    assert preview.format == 'pdf'
    assert preview.confirmation_blocked is True
    assert preview.warnings


def test_jio_parser_reports_page_and_row_for_incomplete_columns():
    data = jio_pdf([[
        ('01-Sep-2026', 'UPI/DR/123456789012/Store', '10.00', '', '90.00', ''),
    ]], {})
    preview = parse_jio_payments_bank_statement(data, 'incomplete-jio.pdf')
    assert preview.errors
    assert preview.errors[0]['page'] == 1
    assert preview.errors[0]['row'] == 1
    assert 'withdrawal' in preview.errors[0]['error'] or 'deposit' in preview.errors[0]['error']
