import pytest
from src.engine import *

def test_rahul_rent_no_rule_and_second_payment():
    d=new_ledger();i=create_intent(d,'Rahul',250000,'Housing / Rent','expense',remember=False)
    t=settle_intent(d,i['id']);assert t['category']=='Housing / Rent'
    assert metrics(d)['personal_expense']==250000
    assert d['rules']==[]
    assert suggest(d,'Rahul')==('Uncategorized','needs_review')
    j=create_intent(d,'Rahul',45000,'Food / Dining');settle_intent(d,j['id'])
    assert metrics(d)['by_category']['Food / Dining']==45000

def test_pending_failed_idempotent():
    d=new_ledger();i=create_intent(d,'Rahul',250000)
    assert settle_intent(d,i['id'],'pending') is None
    assert metrics(d)['personal_expense']==0
    t=settle_intent(d,i['id']);assert settle_intent(d,i['id'])['id']==t['id']
    assert len(d['transactions'])==1
    j=create_intent(d,'Rahul',250000);settle_intent(d,j['id'],'failed')
    with pytest.raises(ValueError):settle_intent(d,j['id'],'succeeded')
    assert metrics(d)['personal_expense']==250000

def test_split_and_partial_reimbursements():
    d=new_ledger();i=create_intent(d,'Restaurant',180000,'Food / Dining','shared')
    t=settle_intent(d,i['id']);s=add_split(d,t['id'],equal_split(180000,['Me','Rahul','Priya']))
    m=metrics(d);assert m['personal_expense']==60000 and m['outstanding']==120000
    r=incoming(d,'Rahul',60000);a=allocate(d,s['id'],s['participants'][1]['id'],r['id'],30000)
    assert metrics(d)['outstanding']==90000
    allocate(d,s['id'],s['participants'][1]['id'],r['id'],30000)
    assert metrics(d)['outstanding']==60000
    with pytest.raises(ValueError):allocate(d,s['id'],s['participants'][2]['id'],r['id'],100)
    reverse_allocation(d,a['id']);assert metrics(d)['outstanding']==90000
    assert metrics(d)['personal_expense']==60000

def test_uneven_split_and_transfer():
    p=equal_split(10000,['Me','A','B']);assert sum(x['share_paise'] for x in p)==10000
    assert [x['share_paise'] for x in p]==[3334,3333,3333]
    d=new_ledger();i=create_intent(d,'Own account',500000,'Other','transfer');settle_intent(d,i['id'])
    assert metrics(d)['personal_expense']==0

def test_import_dedup_and_review():
    d=new_ledger();data='date,recipient,amount,direction,reference,note\n2026-11-01,rahul@upi,2500,debit,ABC,\n'
    assert import_csv(d,data)['added']==1
    assert import_csv(d,data)['skipped']==1
    assert len(d['transactions'])==1 and d['transactions'][0]['needs_review']
    review_transaction(d,d['transactions'][0]['id'],'Housing / Rent','expense')
    assert metrics(d)['personal_expense']==250000

def test_offline_report():
    d=new_ledger();assert 'SpendWise demo report' in report_text(d)


def test_transfer_cannot_be_split_or_counted_as_personal_expense():
    d = new_ledger()
    intent = create_intent(d, 'Own account', 500000, 'Other', 'transfer')
    txn = settle_intent(d, intent['id'])
    assert metrics(d)['personal_expense'] == 0
    with pytest.raises(ValueError, match='Only expense transactions can be split'):
        add_split(d, txn['id'], equal_split(txn['amount_paise'], ['Me', 'Savings']))
    assert not d['splits']
    assert metrics(d)['personal_expense'] == 0


def test_manual_and_csv_references_are_deduplicated_globally():
    d = new_ledger()
    incoming_txn = incoming(d, 'Rahul', 60000, reference='REPAY-001')
    assert incoming(d, 'Rahul', 60000, reference='REPAY-001')['id'] == incoming_txn['id']
    data = 'date,recipient,amount,direction,reference,note\n2026-11-01,rahul@upi,600,debit,REPAY-001,\n'
    result = import_csv(d, data)
    assert result['added'] == 0
    assert result['skipped'] == 1
    assert len(d['transactions']) == 1
    assert d['import_batches'][0]['skipped_rows'][0]['reason'] == 'Duplicate external reference'


def test_csv_invalid_rows_are_reported_and_not_imported():
    d = new_ledger()
    data = '''date,recipient,amount,direction,reference,note
2026-11-01,,100,debit,VALID-001,
2026-02-30,rahul@upi,100,debit,VALID-002,
2026-11-01,rahul@upi,not-money,debit,VALID-003,
2026-11-01,rahul@upi,100,debit,not valid,
2026-11-01,rahul@upi,100,debit,VALID-004,
'''
    result = import_csv(d, data, filename='bad.csv')
    assert result['added'] == 1
    assert result['error_count'] == 4
    assert len(result['errors']) == 4
    assert len(d['transactions']) == 1
    batch = d['import_batches'][0]
    assert batch['filename'] == 'bad.csv'
    assert batch['row_count'] == 5
    assert batch['error_count'] == 4
    assert batch['added'] == 1


def test_csv_rejects_fractional_paise_and_non_iso_dates():
    d = new_ledger()
    data = '''date,recipient,amount,direction,reference,note
01/11/2026,rahul@upi,100.001,debit,BAD-001,
'''
    result = import_csv(d, data)
    assert result['added'] == 0
    assert result['error_count'] == 1
    assert not d['transactions']


def test_classification_model_is_recorded_and_review_is_a_user_confirmation():
    d = new_ledger()
    intent = create_intent(d, 'Rahul', 250000, 'Housing / Rent', 'expense')
    txn = settle_intent(d, intent['id'])
    assert txn['classification'] == {
        'category': 'Housing / Rent', 'source': 'user', 'confidence': 1.0,
        'confirmed': True, 'needs_review': False,
    }

    imported = import_csv(d, 'date,recipient,amount,direction,reference,note\n2026-11-02,cafe@upi,450,debit,FOOD-001,dinner\n')
    assert imported['added'] == 1
    imported_txn = d['transactions'][-1]
    assert imported_txn['classification']['source'] == 'note_suggestion'
    assert imported_txn['classification']['confidence'] == 0.6
    assert imported_txn['classification']['confirmed'] is False

    review_transaction(d, imported_txn['id'], 'Food / Dining', 'expense')
    assert imported_txn['classification']['source'] == 'user'
    assert imported_txn['classification']['confidence'] == 1.0
    assert imported_txn['classification']['confirmed'] is True

    saved = create_intent(d, 'Rahul', 10000, 'Housing / Rent', 'expense', remember=True)
    settle_intent(d, saved['id'])
    rule_import = import_csv(d, 'date,recipient,amount,direction,reference,note\n2026-11-03,Rahul,100,debit,RULE-001,\n')
    assert rule_import['added'] == 1
    rule_txn = d['transactions'][-1]
    assert rule_txn['classification']['source'] == 'approved_rule'
    assert rule_txn['classification']['confidence'] == 0.95
    assert rule_txn['classification']['confirmed'] is True


def test_307_row_csv_import_repeats_idempotently_and_maps_cash_flow():
    ledger = new_ledger()
    rows = ['date,recipient,amount,direction,reference,note']
    for index in range(307):
        is_credit = index < 19
        recipient = 'Employer Payroll' if is_credit else 'Store'
        direction = 'credit' if is_credit else 'debit'
        amount = 500 if is_credit else 125 + index
        rows.append(f'2026-01-{(index % 28) + 1:02d},{recipient},{amount},{direction},FULL-307-{index:03d},synthetic')
    csv_text = '\n'.join(rows) + '\n'

    first = import_csv(ledger, csv_text, filename='full-307.csv')
    second = import_csv(ledger, csv_text, filename='full-307.csv')

    assert first['added'] == 307
    assert first['skipped'] == 0
    assert first['error_count'] == 0
    assert second['added'] == 0
    assert second['skipped'] == 307
    assert second['error_count'] == 0
    assert len(ledger['transactions']) == 307
    assert sum(item['direction'] == 'credit' for item in ledger['transactions']) == 19
    assert all(item['transaction_type'] == ('income' if item['direction'] == 'credit' else 'expense') for item in ledger['transactions'])
    totals = metrics(ledger)
    assert totals['income_received'] == 19 * 50000
    assert totals['gross_paid'] == sum(125 + index for index in range(19, 307)) * 100
