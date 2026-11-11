"""Optional provider-independent LLM adapter.

It is disabled unless the user explicitly opts in with
SPENDWISE_AI_EXTERNAL_OPT_IN=true and supplies a provider key.  The local
deterministic classifier remains the normal path.
"""
from __future__ import annotations

import json
import os
import re
import socket
import urllib.error
import urllib.request

from .ai_intelligence import TOOL_NAMES, LLMClassification, LLMToolCall, parse_llm_classification, parse_llm_tool_call


class AIProviderError(RuntimeError):
    def __init__(self, code, message, status=None, provider_code=None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.provider_code = provider_code

    def diagnostic(self):
        diagnostic = {'code': self.code, 'message': str(self)}
        if self.status is not None:
            diagnostic['status'] = self.status
        if self.provider_code:
            diagnostic['provider_code'] = self.provider_code
        return diagnostic


class AIProvider:
    @property
    def enabled(self):
        return False

    def classify(self, payload) -> LLMClassification | None:
        return None

    def choose_tool(self, question, context, tools, history=None) -> LLMToolCall | None:
        return None

    def summarize(self, question, context, tool_name, result) -> str | None:
        return None


class OpenRouterProvider(AIProvider):
    DEFAULT_MODEL = 'nvidia/nemotron-3.5-super-vl:free'

    def __init__(self):
        self.last_error: AIProviderError | None = None

    @property
    def model(self):
        """Effective model ID; safe to report because it contains no credentials."""
        return os.getenv('SPENDWISE_AI_MODEL', self.DEFAULT_MODEL).strip() or self.DEFAULT_MODEL

    @property
    def enabled(self):
        return (
            os.getenv('SPENDWISE_AI_EXTERNAL_OPT_IN', '').lower() == 'true'
            and os.getenv('SPENDWISE_EXTERNAL_AI_ALLOWED', 'true').lower() == 'true'
            and bool(os.getenv('OPENROUTER_API_KEY'))
        )

    @staticmethod
    def _safe_error_message(body):
        try:
            payload = json.loads(body.decode('utf-8', errors='replace') if isinstance(body, bytes) else str(body))
            error = payload.get('error', payload) if isinstance(payload, dict) else {}
            message = error.get('message') if isinstance(error, dict) else None
            provider_code = error.get('code') if isinstance(error, dict) else None
        except (TypeError, ValueError, json.JSONDecodeError):
            message, provider_code = None, None
        message = str(message or 'The provider rejected the request')
        message = re.sub(r'sk-or-v1-[A-Za-z0-9_-]+', '[redacted-key]', message)
        # Provider errors can include key-management URLs.  Keep the useful
        # reason/reset text but never pass workspace/key paths to the UI.
        message = re.sub(r'https?://\S+', '[redacted-provider-link]', message)
        return message[:240], str(provider_code)[:80] if provider_code is not None else None

    @classmethod
    def _http_error(cls, status, body):
        message, provider_code = cls._safe_error_message(body)
        lowered = f'{message} {provider_code or ""}'.casefold()
        if status == 401 or any(term in lowered for term in ('invalid api key', 'invalid key', 'authentication', 'unauthorized')):
            code = 'invalid_credentials'
        elif status == 402 or any(term in lowered for term in ('insufficient credit', 'insufficient balance', 'payment required', 'credits')):
            code = 'insufficient_credits'
        elif status == 403 and any(term in lowered for term in ('limit exceeded', 'daily limit', 'rate limit', 'quota')):
            code = 'rate_limited'
        elif status == 403:
            if any(term in lowered for term in ('policy', 'safety', 'content restriction')):
                code = 'provider_policy'
            elif any(term in lowered for term in ('model', 'permission', 'access to this')):
                code = 'unsupported_model'
            else:
                code = 'forbidden'
        elif status == 404 or any(term in lowered for term in ('model not found', 'unknown model', 'unsupported model')):
            code = 'unsupported_model'
        elif status == 429:
            code = 'rate_limited'
        elif status == 400 and any(term in lowered for term in ('response_format', 'structured output', 'structured_outputs', 'tool_choice', 'not supported')):
            code = 'unsupported_model_features'
        elif 400 <= status < 500:
            code = 'invalid_request'
        else:
            code = 'provider_error'
        return AIProviderError(code, message, status=status, provider_code=provider_code)

    def _request_json(self, messages, max_tokens=500, structured=True):
        if not self.enabled:
            return None
        self.last_error = None
        body = json.dumps({
            'model': self.model,
            'temperature': 0,
            'max_tokens': max_tokens,
            'messages': messages,
            **({'response_format': {'type': 'json_object'}} if structured else {}),
        }).encode()
        request = urllib.request.Request(
            'https://openrouter.ai/api/v1/chat/completions', data=body,
            headers={'Authorization': f'Bearer {os.environ["OPENROUTER_API_KEY"]}', 'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                raw = json.loads(response.read().decode())
            choices = raw.get('choices') if isinstance(raw, dict) else None
            message = choices[0].get('message') if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
            if not isinstance(message, dict):
                raise ValueError('Missing assistant message')
            content = message.get('content')
            if isinstance(content, list):
                # Some compatible endpoints return content blocks. Ignore
                # reasoning blocks and concatenate only user-facing text.
                content = ''.join(block.get('text', '') for block in content
                                  if isinstance(block, dict) and block.get('type') in {'text', 'output_text'}
                                  and isinstance(block.get('text'), str))
            if isinstance(content, str) and content.strip():
                return content
            # Although this client asks for JSON text rather than invoking
            # model tools, tolerate an OpenAI-compatible function-call shape.
            calls = message.get('tool_calls')
            if isinstance(calls, list) and calls:
                call = calls[0]
                function = call.get('function') if isinstance(call, dict) else None
                if isinstance(function, dict) and isinstance(function.get('name'), str):
                    return json.dumps({'tool_name': function['name'], 'arguments': function.get('arguments', {})})
            # Deliberately do not inspect message.reasoning or
            # reasoning_content: hidden reasoning is neither a response nor
            # safe input to the tool parser.
            raise ValueError('Missing assistant response content')
        except urllib.error.HTTPError as error:
            mapped = self._http_error(error.code, error.read())
            self.last_error = mapped
            raise mapped from error
        except (TimeoutError, socket.timeout) as error:
            mapped = AIProviderError('timeout', 'The AI provider did not respond in time')
            self.last_error = mapped
            raise mapped from error
        except urllib.error.URLError as error:
            reason = str(getattr(error, 'reason', '')).casefold()
            code = 'timeout' if 'timed out' in reason or 'timeout' in reason else 'network_error'
            mapped = AIProviderError(code, 'The AI provider could not be reached')
            self.last_error = mapped
            raise mapped from error
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            mapped = AIProviderError('invalid_response', 'The AI provider returned an invalid response')
            self.last_error = mapped
            raise mapped from error

    def _request_structured(self, messages, max_tokens=500):
        try:
            return self._request_json(messages, max_tokens=max_tokens, structured=True)
        except AIProviderError as error:
            if error.code != 'unsupported_model_features':
                raise
            # Some OpenRouter models expose chat but not response_format. The
            # prompt still requires JSON and the parser remains strict.
            return self._request_json(messages, max_tokens=max_tokens, structured=False)

    def classify(self, payload):
        if not self.enabled:
            return None
        try:
            content = self._request_structured([
                {'role': 'system', 'content': 'Return only JSON with suggested_category, merchant, transaction_intent, confidence, explanation. Never infer missing values.'},
                {'role': 'user', 'content': json.dumps(payload, separators=(',', ':'))},
            ])
            return parse_llm_classification(content)
        except (AIProviderError, ValueError, TypeError, json.JSONDecodeError) as error:
            # External AI is optional; malformed or unavailable output is never
            # allowed to affect ledger posting.
            self.last_error = error if isinstance(error, AIProviderError) else AIProviderError('invalid_response', 'The AI provider returned an invalid classification')
            return None

    def choose_tool(self, question, context, tools, history=None):
        first_messages = [
            {'role': 'system', 'content': 'You select exactly one read-only financial analytics tool. Return only JSON: {"tool_name":"...","arguments":{}}. Never answer the question, execute SQL, mutate records, or invent account IDs. Use month YYYY-MM or inclusive date_from/date_to. Use account_name only when the user explicitly asks for an account.'},
            {'role': 'user', 'content': json.dumps({'question': question, 'active_context': context, 'available_tools': tools, 'conversation': (history or [])[-8:]}, separators=(',', ':'))},
        ]
        try:
            content = self._request_structured(first_messages)
            return parse_llm_tool_call(content)
        except AIProviderError as error:
            if error.code != 'invalid_response':
                raise
        except (ValueError, TypeError, json.JSONDecodeError):
            # One constrained retry helps models that explain their answer or
            # ignore the first formatting instruction. Drop history and pass
            # only the exact registry plus the current question/context.
            allowed_names = sorted(TOOL_NAMES)
            simple_messages = [
                {'role': 'system', 'content': (
                    'Output one JSON object only. No Markdown, explanation, or reasoning. '
                    'Required schema: {"tool_name":"ALLOWED_NAME","arguments":{}}. '
                    f'ALLOWED_NAME must be exactly one of: {", ".join(allowed_names)}. '
                    'Allowed argument keys only: month, date_from, date_to, account_name, category, limit. '
                    'Do not include SQL or extra keys.'
                )},
                {'role': 'user', 'content': json.dumps({'question': question, 'active_context': context}, separators=(',', ':'))},
            ]
            try:
                # Retry without response_format for providers that return
                # incompatible structured output envelopes despite HTTP 200.
                retry_content = self._request_json(simple_messages, structured=False)
                return parse_llm_tool_call(retry_content)
            except AIProviderError as error:
                if error.code != 'invalid_response':
                    raise
                mapped = AIProviderError('invalid_response', 'The AI provider returned an invalid tool selection after one format retry')
                self.last_error = mapped
                raise mapped from error
            except (ValueError, TypeError, json.JSONDecodeError) as error:
                mapped = AIProviderError('invalid_response', 'The AI provider returned an invalid tool selection after one format retry')
                self.last_error = mapped
                raise mapped from error

    def summarize(self, question, context, tool_name, result):
        content = self._request_structured([
            {'role': 'system', 'content': 'Summarize only verified backend results. Do not invent or recalculate amounts. Mention the supplied date and account scope when useful. Return only JSON: {"answer":"..."}.'},
            {'role': 'user', 'content': json.dumps({'question': question, 'context': context, 'tool': tool_name, 'verified_result': result}, separators=(',', ':'))},
        ])
        try:
            if isinstance(content, str):
                payload = json.loads(content)
                answer = payload.get('answer') if isinstance(payload, dict) else None
                if isinstance(answer, str) and answer.strip():
                    return answer.strip()[:2000]
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            mapped = AIProviderError('invalid_response', 'The AI provider returned an invalid summary')
            self.last_error = mapped
            raise mapped from error
        mapped = AIProviderError('invalid_response', 'The AI provider returned an invalid summary')
        self.last_error = mapped
        raise mapped
