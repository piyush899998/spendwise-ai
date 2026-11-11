"""SpendWise AI — Jupiter-inspired, original fintech prototype. Run: streamlit run app.py"""
import csv, io, json
from pathlib import Path
import pandas as pd
import plotly.express as px
import streamlit as st
from src.db import create_database
from src.engine import (new_ledger,create_intent,settle_intent,paise,rupees,metrics,suggest,
    equal_split,add_split,incoming,allocate,reverse_allocation,import_csv,review_transaction,report_text,weekly_report,
    allocated_to_share)
from src.repositories import LedgerRepository

st.set_page_config(page_title='SpendWise AI',page_icon='💸',layout='wide',initial_sidebar_state='expanded')
st.markdown('''<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&display=swap');
html,body,[class*="css"], [data-testid="stApp"] {font-family:'DM Sans',sans-serif;}
[data-testid="stApp"] {background:#f7f8f3;color:#162c27;}
[data-testid="stSidebar"] {background:#e8f1e9;}
h1,h2,h3 {color:#173e34!important;letter-spacing:-.035em;}
[data-testid="stMetric"] {background:#fff;border:1px solid #e2eae1;border-radius:18px;padding:17px;}
[data-testid="stMetricValue"] {color:#14664c;font-weight:800;}
div.stButton>button[kind="primary"] {background:#14664c;border-radius:14px;border:0;color:white;}
div.stButton>button {border-radius:14px;}
.hero {background:linear-gradient(115deg,#153e32,#237c60);padding:30px;border-radius:24px;color:white;margin-bottom:22px;}
.hero h1 {color:white!important;margin:0;font-size:2.1rem;}.hero p {color:#d3eee1;margin:8px 0 0;}
.notice {background:#fff6df;border:1px solid #f3dfb2;border-radius:12px;padding:12px;color:#664a16;}
</style>''',unsafe_allow_html=True)

USER_ID = 'demo-user'


@st.cache_resource
def get_repository():
    return LedgerRepository(create_database())


repository = get_repository()
if 'db' not in st.session_state:
    st.session_state.db = repository.load(USER_ID)
db=st.session_state.db


def persist():
    repository.save(st.session_state.db, user_id=USER_ID, display_name='Demo User')


with st.sidebar:
    st.markdown('## ◉ SpendWise AI')
    st.caption('Your money. Your context.')
    page=st.radio('Navigate',['Overview','Send money','Transactions','Split & settle','Import CSV','Automation'],label_visibility='collapsed')
    st.divider()
    st.caption('🔒 Local demo · Synthetic data recommended')
    if st.button('Reset demo data'):
        repository.clear(USER_ID)
        st.session_state.db=new_ledger();st.rerun()

if page=='Overview':
    st.markdown('<div class="hero"><h1>Money, understood.</h1><p>Track what you actually spend — not just what leaves your account.</p></div>',unsafe_allow_html=True)
    m=metrics(db)
    cols=st.columns(4)
    for c,(label,key) in zip(cols,[('Personal spending','personal_expense'),('Gross payments','gross_paid'),('Friends owe you','outstanding'),('Net cash outflow','net_cash_outflow')]):
        c.metric(label,rupees(m[key]))
    a,b=st.columns([1.25,1])
    with a:
        st.subheader('Where your money goes')
        if m['by_category']:
            df=pd.DataFrame([{'Category':k,'Amount':v/100} for k,v in m['by_category'].items() if v])
            if len(df):
                fig=px.pie(df,names='Category',values='Amount',hole=.6,color_discrete_sequence=['#1b7055','#a6c8a8','#f1bb73','#7ba69d','#d6a5a2'])
                fig.update_layout(paper_bgcolor='rgba(0,0,0,0)',showlegend=True,margin=dict(l=5,r=5,t=5,b=5))
                st.plotly_chart(fig,use_container_width=True)
        else:st.info('Create a simulated payment to see your spending breakdown.')
    with b:
        st.subheader('Needs your attention')
        st.metric('Transactions to review',m['review_count'])
        st.caption('Unknown person-to-person payments are not silently treated as rent or food.')
        st.subheader('Recent activity')
        for t in reversed(db['transactions'][-5:]):
            st.write(f"{'↗' if t['direction']=='debit' else '↙'} **{t['recipient']}** · {rupees(t['amount_paise'])} · {t['category']}")
    st.info('Try: ₹2,500 rent to Rahul → ₹1,800 dinner split → reimbursements → inspect how personal spending differs from cash flow.')

elif page=='Send money':
    st.title('Send money')
    st.caption('A simulated payment flow — no UPI transaction is initiated.')
    with st.form('pay'):
        recipient=st.text_input('Recipient',value='Rahul')
        amount=st.number_input('Amount (₹)',min_value=0.01,value=2500.0,step=50.0)
        st.markdown('**Optional payment context**')
        category=st.selectbox('What is this for?',['Housing / Rent','Food / Dining','Groceries','Transport','Shopping','Bills','Family support','Other','Uncategorized'])
        treatment=st.selectbox('How should it be tracked?',['expense','shared','transfer','loan'],format_func=lambda x:{'expense':'Personal expense','shared':'Shared expense','transfer':'Internal / non-expense transfer','loan':'Loan / repayment'}[x])
        note=st.text_input('Payment note (optional)')
        remember=st.checkbox('Suggest this category for future payments to this person',value=False)
        outcome=st.selectbox('Simulated payment result',['succeeded','pending','failed'])
        submitted=st.form_submit_button('Simulate payment',type='primary')
    if submitted:
        try:
            intent=create_intent(db,recipient,paise(amount),category,treatment,note,remember)
            txn=settle_intent(db,intent['id'],outcome)
            persist()
            if txn:
                st.success(f"Payment succeeded: {rupees(txn['amount_paise'])} to {recipient} · {category}")
                st.caption('This categorization applies only to this payment unless you opted into a reusable rule.')
            else:st.warning(f'Payment {outcome}. Nothing posted to spending totals.')
        except ValueError as e:st.error(str(e))
    st.divider()
    st.subheader('Why this matters')
    st.write('Rahul can receive rent today and dinner money tomorrow. We never assume that the same recipient always means the same expense category.')

elif page=='Transactions':
    st.title('Transactions')
    tx=db['transactions']
    if not tx:st.info('No transactions yet. Simulate a payment or import a CSV.')
    else:
        frame=pd.DataFrame([{'ID':t['id'][:8],'Date':t['occurred_at'][:10],'Person':t['recipient'],'Direction':t['direction'],
                            'Amount (₹)':t['amount_paise']/100,'Category':t['category'],'Treatment':t['treatment'],
                            'Classification source':t.get('classification',{}).get('source','legacy'),
                            'Confidence':t.get('classification',{}).get('confidence'),
                            'Confirmed':t.get('classification',{}).get('confirmed',t.get('confirmed',False)),
                            'Review':t['needs_review']} for t in reversed(tx)])
        st.dataframe(frame,use_container_width=True,hide_index=True)
        pending=[t for t in tx if t['needs_review']]
        if pending:
            st.subheader('Review uncertain transfers')
            t=st.selectbox('Choose a transaction',pending,format_func=lambda x:f"{x['recipient']} · {rupees(x['amount_paise'])} · {x['id'][:8]}")
            with st.form('review'):
                category=st.selectbox('Correct category',['Housing / Rent','Food / Dining','Groceries','Transport','Shopping','Bills','Family support','Other','Uncategorized'])
                treatment=st.selectbox('Financial treatment',['expense','transfer','loan','reimbursement','income'])
                if st.form_submit_button('Confirm categorization'):
                    review_transaction(db,t['id'],category,treatment);persist();st.success('Classification corrected; original transaction preserved.');st.rerun()

elif page=='Split & settle':
    st.title('Split & settle')
    st.caption('Track your share separately from the amount you initially paid.')
    eligible=[t for t in db['transactions'] if t['direction']=='debit' and t['status']=='succeeded' and not any(s['transaction_id']==t['id'] for s in db['splits'])]
    with st.expander('Create a split',expanded=not db['splits']):
        if eligible:
            txn=st.selectbox('Select paid transaction',eligible,format_func=lambda x:f"{x['recipient']} · {rupees(x['amount_paise'])}")
            names=st.text_input('Participants (comma separated; yourself first)',value='Me, Rahul, Priya')
            if st.button('Split equally'):
                try:
                    participants=[n.strip() for n in names.split(',') if n.strip()]
                    split=add_split(db,txn['id'],equal_split(txn['amount_paise'],participants))
                    persist()
                    st.success('Split recorded: '+', '.join(f"{p['name']} {rupees(p['share_paise'])}" for p in split['participants']))
                except ValueError as e:st.error(str(e))
        else:st.info('Create a successful outgoing payment first.')
    if db['splits']:
        st.subheader('Outstanding shares')
        for s in db['splits']:
            original=next(t for t in db['transactions'] if t['id']==s['transaction_id'])
            st.markdown(f"**{original['recipient']} · {rupees(original['amount_paise'])}**")
            for p in s['participants']:
                if p['is_self']:st.caption(f"Your share: {rupees(p['share_paise'])}");continue
                remaining=p['share_paise']-allocated_to_share(db,p['id'])
                st.write(f"{p['name']} owes **{rupees(remaining)}**")
        st.divider()
        st.subheader('Record incoming money')
        with st.form('incoming'):
            sender=st.text_input('Sender',value='Rahul')
            amount=st.number_input('Received amount (₹)',min_value=0.01,value=600.0,step=50.0)
            if st.form_submit_button('Add incoming payment'):
                incoming(db,sender,paise(amount));persist();st.success('Incoming payment added. Confirm a match below before settling a share.')
        available=[t for t in db['transactions'] if t['direction']=='credit' and t['amount_paise']>sum(a['amount_paise'] for a in db['allocations'] if a['credit_id']==t['id'] and not a['reversed'])]
        shares=[(s,p) for s in db['splits'] for p in s['participants'] if not p['is_self'] and p['share_paise']>allocated_to_share(db,p['id'])]
        if available and shares:
            with st.form('match'):
                credit=st.selectbox('Incoming payment',available,format_func=lambda x:f"{x['recipient']} · {rupees(x['amount_paise'])}")
                target=st.selectbox('Match to outstanding share',shares,format_func=lambda x:f"{x[1]['name']} · remaining {rupees(x[1]['share_paise']-allocated_to_share(db,x[1]['id']))}")
                match_amount=st.number_input('Match amount (₹)',min_value=0.01,value=100.0)
                if st.form_submit_button('Confirm reimbursement'):
                    try:
                        allocate(db,target[0]['id'],target[1]['id'],credit['id'],paise(match_amount))
                        persist()
                        st.success('Confirmed. Outstanding balance updated.');st.rerun()
                    except ValueError as e:st.error(str(e))
        active=[a for a in db['allocations'] if not a['reversed']]
        if active:
            with st.expander('Reversal / audit'):
                a=st.selectbox('Confirmed match',active,format_func=lambda x:f"{x['id'][:8]} · {rupees(x['amount_paise'])}")
                if st.button('Reverse selected match'):
                    reverse_allocation(db,a['id']);persist();st.rerun()

elif page=='Import CSV':
    st.title('Import transactions')
    st.markdown('<div class="notice">Use fictional/sample data in this portfolio demo. Uploaded data stays in this browser session while the app is running; do not upload real private statements to a public deployment.</div>',unsafe_allow_html=True)
    st.code('date,recipient,amount,direction,reference,note\n2026-11-01,rahul@upi,2500,debit,DEMO-001,\n2026-11-02,store@upi,350,debit,DEMO-002,groceries',language='csv')
    uploaded=st.file_uploader('Upload canonical CSV',type=['csv'])
    if uploaded and st.button('Validate and import'):
        try:
            result=import_csv(db,uploaded.getvalue().decode('utf-8-sig'),filename=uploaded.name)
            persist()
            st.success(f"Batch {result['batch_id'][:8]}: imported {result['added']} rows; skipped {result['skipped']} duplicates.")
            if result['skipped_rows']:
                st.warning(f"Skipped {len(result['skipped_rows'])} duplicate row(s); no new transaction was created.")
                st.json(result['skipped_rows'])
            if result['errors']:
                st.error(f"Rejected {result['error_count']} invalid row(s). Valid rows were retained; rejected rows were not posted.")
                st.json(result['errors'])
        except (UnicodeError,ValueError) as e:st.error(str(e))
    st.download_button('Download sample CSV',Path('data/synthetic_transactions.csv').read_bytes(),file_name='synthetic_transactions.csv',mime='text/csv')

elif page=='Automation':
    st.title('Automated insights')
    st.caption('Deterministic metrics first; AI narration can be added after validating the numbers.')
    st.subheader('Weekly report preview')
    st.info(report_text(db))
    st.json(weekly_report(db))
    st.download_button('Download aggregate KPI JSON',json.dumps(weekly_report(db),indent=2),file_name='weekly_kpis.json',mime='application/json')
    st.download_button('Download n8n workflow template',Path('workflows/weekly_spending_report.json').read_text(),file_name='weekly_spending_report.json',mime='application/json')
    st.caption('The included n8n workflow uses sample aggregate KPIs. Configure scheduling and email credentials yourself. No real payment data is sent to a model.')
