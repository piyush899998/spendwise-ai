"""User-scoped persistence for non-ledger AI artifacts."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from .db import session_scope
from .models import ChatActionPreview, ChatMessage, ChatSession, FinancialReport, InsightFeedback, RecurringCandidate
from .engine import uid


def _now():
    return datetime.now(timezone.utc)


class AIRepository:
    def __init__(self, engine):
        self.engine = engine

    def feedback(self, user_id, insight_key, value):
        with session_scope(self.engine) as session:
            row = InsightFeedback(id=uid(), user_id=user_id, insight_key=insight_key, feedback=value)
            session.add(row)
            return {'id': row.id, 'insight_key': row.insight_key, 'feedback': row.feedback}

    def chat(self, user_id, session_id=None, context=None, user_message=None, assistant_message=None, tool_name=None):
        with session_scope(self.engine) as session:
            if session_id:
                chat_session = session.scalar(select(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == user_id))
                if chat_session is None:
                    raise ValueError('Chat session not found')
            else:
                chat_session = ChatSession(id=uid(), user_id=user_id, context_json=json.dumps(context or {}))
                session.add(chat_session)
            if context:
                chat_session.context_json = json.dumps(context)
            if user_message is not None:
                session.add(ChatMessage(id=uid(), session_id=chat_session.id, role='user', content=user_message))
            if assistant_message is not None:
                session.add(ChatMessage(id=uid(), session_id=chat_session.id, role='assistant', content=assistant_message, tool_name=tool_name))
            messages = session.scalars(select(ChatMessage).where(ChatMessage.session_id == chat_session.id).order_by(ChatMessage.created_at, ChatMessage.id)).all()
            return {'session_id': chat_session.id, 'messages': [{'role': item.role, 'content': item.content, 'tool': item.tool_name} for item in messages]}

    def conversation(self, user_id, session_id, limit=12):
        with session_scope(self.engine) as session:
            chat_session = session.scalar(select(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == user_id))
            if chat_session is None:
                raise ValueError('Chat session not found')
            messages = session.scalars(select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.created_at, ChatMessage.id).limit(limit)).all()
            return {'context': json.loads(chat_session.context_json or '{}'), 'messages': [{'role': item.role, 'content': item.content, 'tool': item.tool_name} for item in messages]}

    def update_chat_context(self, user_id, session_id, updates):
        with session_scope(self.engine) as session:
            row = session.scalar(select(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == user_id))
            if row is None:
                raise ValueError('Chat session not found')
            context = json.loads(row.context_json or '{}')
            context.update(updates)
            row.context_json = json.dumps(context)
            return context

    def create_action_preview(self, user_id, session_id, action_type, payload, preview, idempotency_key):
        with session_scope(self.engine) as session:
            owner = session.scalar(select(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == user_id))
            if owner is None:
                raise ValueError('Chat session not found')
            existing = session.scalar(select(ChatActionPreview).where(
                ChatActionPreview.user_id == user_id, ChatActionPreview.session_id == session_id,
                ChatActionPreview.idempotency_key == idempotency_key,
            ))
            if existing:
                if existing.action_type != action_type or json.loads(existing.payload_json) != payload:
                    raise ValueError('Idempotency key was already used for a different action')
                return self._action_view(existing)
            row = ChatActionPreview(
                id=uid(), user_id=user_id, session_id=session_id, idempotency_key=idempotency_key,
                action_type=action_type, payload_json=json.dumps(payload), preview_json=json.dumps(preview),
                status='pending', expires_at=_now() + timedelta(minutes=15),
            )
            session.add(row)
            session.flush()
            return self._action_view(row)

    def action_preview(self, user_id, preview_id):
        with session_scope(self.engine) as session:
            row = session.scalar(select(ChatActionPreview).where(
                ChatActionPreview.id == preview_id, ChatActionPreview.user_id == user_id,
            ))
            if row is None:
                raise ValueError('Action preview not found')
            return self._action_view(row)

    def cancel_action_preview(self, user_id, preview_id):
        with session_scope(self.engine) as session:
            row = session.scalar(select(ChatActionPreview).where(
                ChatActionPreview.id == preview_id, ChatActionPreview.user_id == user_id,
            ))
            if row is None:
                raise ValueError('Action preview not found')
            if row.status == 'pending':
                row.status = 'cancelled'
            return self._action_view(row)

    @staticmethod
    def _action_view(row):
        return {'id': row.id, 'session_id': row.session_id, 'action_type': row.action_type,
                'payload': json.loads(row.payload_json), 'preview': json.loads(row.preview_json),
                'status': row.status, 'expires_at': row.expires_at.isoformat(),
                'committed_at': row.committed_at.isoformat() if row.committed_at else None}

    def report(self, user_id, report_type, account_id, start: date, end: date, content):
        with session_scope(self.engine) as session:
            existing = session.scalar(select(FinancialReport).where(
                FinancialReport.user_id == user_id, FinancialReport.report_type == report_type,
                FinancialReport.account_id == account_id, FinancialReport.date_from == start,
                FinancialReport.date_to == end,
            ))
            if existing:
                return self._report_view(existing)
            row = FinancialReport(id=uid(), user_id=user_id, report_type=report_type, account_id=account_id,
                                  date_from=start, date_to=end, status='ready', content_json=json.dumps(content))
            session.add(row)
            session.flush()
            return self._report_view(row)

    def reports(self, user_id, limit=50):
        with session_scope(self.engine) as session:
            rows = session.scalars(select(FinancialReport).where(FinancialReport.user_id == user_id).order_by(FinancialReport.created_at.desc()).limit(limit)).all()
            return [self._report_view(row) for row in rows]

    def report_by_id(self, user_id, report_id):
        with session_scope(self.engine) as session:
            row = session.scalar(select(FinancialReport).where(FinancialReport.id == report_id, FinancialReport.user_id == user_id))
            if row is None:
                raise ValueError('Report not found')
            return self._report_view(row)

    def candidates(self, user_id, candidates):
        with session_scope(self.engine) as session:
            output = []
            for item in candidates:
                existing = session.scalar(select(RecurringCandidate).where(RecurringCandidate.user_id == user_id, RecurringCandidate.candidate_key == item['candidate_key']))
                if existing:
                    existing.amount_paise = item['amount_paise']; existing.interval_days = item['interval_days']; existing.confidence = item['confidence']
                    output.append(self._candidate_view(existing)); continue
                row = RecurringCandidate(id=uid(), user_id=user_id, candidate_key=item['candidate_key'], merchant=item['merchant'], amount_paise=item['amount_paise'], interval_days=item['interval_days'], confidence=item['confidence'], status=item.get('status', 'candidate'))
                session.add(row); output.append(self._candidate_view(row))
            return output

    def update_candidate(self, user_id, candidate_id, status):
        with session_scope(self.engine) as session:
            row = session.scalar(select(RecurringCandidate).where(RecurringCandidate.id == candidate_id, RecurringCandidate.user_id == user_id))
            if row is None: raise ValueError('Recurring candidate not found')
            if status not in {'candidate', 'confirmed', 'dismissed'}: raise ValueError('Invalid candidate status')
            row.status = status
            return self._candidate_view(row)

    @staticmethod
    def _report_view(row):
        return {'id': row.id, 'report_type': row.report_type, 'account_id': row.account_id, 'date_from': row.date_from.isoformat(), 'date_to': row.date_to.isoformat(), 'status': row.status, 'created_at': row.created_at.isoformat(), 'content': json.loads(row.content_json)}

    @staticmethod
    def _candidate_view(row):
        return {'id': row.id, 'candidate_key': row.candidate_key, 'merchant': row.merchant, 'amount_paise': row.amount_paise, 'interval_days': row.interval_days, 'confidence': row.confidence, 'status': row.status}
