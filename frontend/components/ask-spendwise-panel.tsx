"use client";

import { useEffect, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import { Bot, Check, ChevronDown, ChevronUp, LoaderCircle, MessageCircle, Plus, Sparkles, Split as SplitIcon, X } from "lucide-react";
import { api, type Account, type AIChatMessage, type AIChatResponse, type ChatAction, type GlobalFilters, type Split as SplitRecord, type Transaction } from "@/lib/api";
import { formatDate, formatRupees } from "@/lib/utils";
import { Badge, Button, Card } from "@/components/ui";

type PersonInput = { name: string; is_self: boolean; amount: string; percent: string };

export function AskSpendwisePanel({ filters, accounts, onRefresh }: { filters: GlobalFilters; accounts: Account[]; onRefresh: () => Promise<void> }) {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<AIChatMessage[]>([]);
  const [answer, setAnswer] = useState<AIChatResponse | null>(null);
  const [sessionId, setSessionId] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [selectionTotal, setSelectionTotal] = useState(0);
  const [selectingAll, setSelectingAll] = useState(false);
  const [offset, setOffset] = useState(0);
  const [splitOpen, setSplitOpen] = useState(false);
  const [splitMethod, setSplitMethod] = useState<"equal" | "custom" | "percentage">("equal");
  const [people, setPeople] = useState<PersonInput[]>([{ name: "You", is_self: true, amount: "", percent: "" }, { name: "", is_self: false, amount: "", percent: "" }]);
  const [action, setAction] = useState<ChatAction | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string[]>([]);
  const [splits, setSplits] = useState<SplitRecord[]>([]);
  useEffect(() => { void api.splits().then(result => setSplits(result.splits)).catch(() => setSplits([])); }, []);

  const ask = async (value = question) => {
    if (!value.trim() || busy) return;
    setBusy(true); setError(null); setNotice(null);
    try {
      const result = await api.aiChat(filters, value.trim(), sessionId);
      setAnswer(result); setMessages(result.messages); setSessionId(result.session_id); setQuestion("");
      if (result.action_preview) {
        setAction(result.action_preview);
        setSplitMethod("equal");
        setPeople(result.action_preview.preview.participants.map(person => ({
          name: person.name, is_self: person.is_self, amount: "", percent: "",
        })));
        setSplitOpen(true);
      }
      setOffset(0);
      if (result.context.selected_transaction_ids) setSelected(result.context.selected_transaction_ids);
      if (result.tool === "get_transactions" && result.data?.has_more) setOffset((result.data.offset || 0) + (result.data.transactions?.length || 0));
      if (result.transactions?.length) {
        const currentIds = new Set(result.transactions.map(row => row.id));
        setSelected(current => current.filter(id => currentIds.has(id)));
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : "The assistant could not answer"); }
    finally { setBusy(false); }
  };

  const accountName = accounts.find(account => account.id === filters.accountId)?.name || "All Accounts";
  const contextLabel = answer ? `${answer.context.account || accountName} · ${answer.context.date_from} to ${answer.context.date_to}` : `${accountName} · ${filters.period.replaceAll("_", " ")}`;
  const transactionHref = (id: string) => {
    const params = new URLSearchParams({ screen: "transactions", period: filters.period });
    if (filters.accountId) params.set("account_id", filters.accountId);
    if (filters.month) params.set("month", filters.month);
    if (filters.year) params.set("year", String(filters.year));
    if (filters.dateFrom) params.set("date_from", filters.dateFrom);
    if (filters.dateTo) params.set("date_to", filters.dateTo);
    params.set("transaction_id", id);
    return `?${params.toString()}`;
  };
  const prompts = ["How much did I spend on food?", "What about August?", "Which account contributed the most?", "Show my five largest expenses", "Which subscriptions am I paying for?"];
  const rows = answer?.transactions || [];
  const hasMore = answer?.data?.has_more === true;

  const syncSelection = async (next: string[], all = false) => {
    if (!sessionId) return;
    setSelectingAll(true); setError(null);
    try {
      const result = await api.aiChatSelection(sessionId, next, all);
      setSelected(result.selected_transaction_ids); setSelectionTotal(result.gross_debit_paise);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not update selection"); }
    finally { setSelectingAll(false); }
  };

  const loadMore = async () => {
    if (!sessionId || busy) return;
    setBusy(true); setError(null);
    try {
      const page = await api.aiChatTransactionPage(sessionId, offset);
      setAnswer(current => current ? { ...current, transactions: [...(current.transactions || []), ...page.transactions], data: { ...current.data, total_count: page.total_count, has_more: page.has_more, gross_debit_paise: page.gross_debit_paise, confirmed_personal_expense_paise: page.confirmed_personal_expense_paise } } : current);
      setOffset(offset + page.transactions.length);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not load more transactions"); }
    finally { setBusy(false); }
  };

  const toPaise = (value: string) => {
    if (!/^\d+(\.\d{1,2})?$/.test(value.trim())) return null;
    const [rupees, fraction = ""] = value.trim().split(".");
    return Number(rupees) * 100 + Number((fraction + "00").slice(0, 2));
  };

  const createPreview = async () => {
    if (!sessionId) return;
    setActionBusy(true); setError(null); setNotice(null);
    try {
      if (action?.status === "pending") await api.aiCancelAction(action.id);
      const participants = people.map(person => ({
        name: person.name.trim(), is_self: person.is_self,
        ...(splitMethod === "custom" ? { share_paise: toPaise(person.amount) ?? -1 } : {}),
        ...(splitMethod === "percentage" ? { basis_points: /^\d+(\.\d{1,2})?$/.test(person.percent) ? Math.round(Number(person.percent) * 100) : -1 } : {}),
      }));
      const result = await api.aiSplitPreview({ session_id: sessionId, idempotency_key: crypto.randomUUID(), split_method: splitMethod, participants });
      setAction(result.action);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not prepare split preview"); }
    finally { setActionBusy(false); }
  };

  const confirmAction = async () => {
    if (!action) return;
    setActionBusy(true); setError(null);
    try {
      const result = await api.aiConfirmAction(action.id);
      setNotice(result.idempotent_replay ? "This preview was already confirmed; no duplicate split was created." : `Shared expense recorded for ${result.transaction_count || action.preview.transaction_count} transaction(s).`);
      setAction({ ...action, status: "committed" }); setSplitOpen(false); const latest = await api.splits(); setSplits(latest.splits); await onRefresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not confirm this action"); }
    finally { setActionBusy(false); }
  };

  const cancelAction = async () => {
    if (!action) return;
    setActionBusy(true);
    try { await api.aiCancelAction(action.id); setAction({ ...action, status: "cancelled" }); setNotice("Preview cancelled. No ledger changes were made."); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not cancel preview"); }
    finally { setActionBusy(false); }
  };

  const onComposerKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void ask(); }
  };
  const onSubmit = (event: FormEvent) => { event.preventDefault(); void ask(); };

  return <div className="mx-auto max-w-5xl animate-rise-in space-y-6">
    <header><Badge tone="lavender">Ask SpendWise AI</Badge><h1 className="mt-3 font-display text-3xl font-semibold tracking-tight sm:text-4xl">Your ledger, in conversation.</h1><p className="mt-2 max-w-2xl text-sm leading-6 text-ink/50">Ask in your own words. Answers and transaction lists use the active filters; any shared-expense change requires a backend-validated preview and your confirmation.</p></header>
    <Card className="bg-gradient-to-br from-[#142f29] via-[#16856B] to-[#8270DD] p-5 text-white sm:p-6"><div className="flex items-center gap-3"><div className="rounded-2xl bg-white/15 p-3"><Bot size={20} /></div><div><p className="font-semibold">Current financial context</p><p className="text-xs text-white/75">{contextLabel}</p></div></div><div className="mt-5 flex flex-wrap gap-2">{prompts.map(prompt => <button key={prompt} onClick={() => void ask(prompt)} disabled={busy} className="rounded-full bg-white/15 px-3 py-2 text-left text-xs font-semibold transition hover:bg-white/25 disabled:opacity-50">{prompt}</button>)}</div></Card>

    {messages.length > 0 && <Card className="p-5 sm:p-6"><div className="mb-4 flex items-center gap-2 text-xs font-bold uppercase tracking-[0.12em] text-ink/40"><MessageCircle size={14} /> Conversation history</div><div className="max-h-[360px] space-y-3 overflow-y-auto pr-1">{messages.map((message, index) => <div key={`${message.role}-${index}`} className={message.role === "user" ? "ml-6 rounded-2xl bg-mint p-3 text-sm sm:ml-16" : "mr-6 rounded-2xl bg-cream p-3 text-sm sm:mr-16"}><p className="whitespace-pre-wrap">{message.content}</p>{message.tool && <p className="mt-1 text-[11px] text-ink/40">Verified tool: {message.tool}</p>}</div>)}</div></Card>}

    <Card className="p-5 sm:p-6"><div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="font-display text-xl font-semibold">Answer and evidence</h2><p className="mt-1 text-xs text-ink/45">{answer ? `${answer.context.account || accountName} · ${answer.context.date_from} to ${answer.context.date_to}` : "Ask anything about your financial data"}</p></div>{answer && <Badge tone={answer.provider_status === "available" ? "green" : "amber"}>{answer.provider_status === "available" ? "AI assisted" : "Local calculation"}</Badge>}</div>
      {answer ? <div className="mt-4 rounded-3xl bg-cream p-4 sm:p-5"><p className="text-sm leading-7 sm:text-base">{answer.answer}</p><p className="mt-3 text-xs text-ink/45">{answer.ai_notice}</p>{answer.provider_diagnostic && <p className="mt-1 text-xs text-ink/45">Provider detail: {answer.provider_diagnostic.status ? `HTTP ${answer.provider_diagnostic.status}` : answer.provider_diagnostic.code} · {answer.provider_diagnostic.message}</p>}
        {answer.data && <div className="mt-4 grid gap-2 sm:grid-cols-3">{typeof answer.data.total_count === "number" && <div className="rounded-2xl bg-white p-3"><p className="text-[11px] text-ink/45">Matching transactions</p><p className="mt-1 font-semibold">{answer.data.total_count}</p></div>}{typeof answer.data.gross_debit_paise === "number" && <div className="rounded-2xl bg-white p-3"><p className="text-[11px] text-ink/45">Gross debit total</p><p className="mt-1 font-semibold">{formatRupees(answer.data.gross_debit_paise)}</p></div>}{typeof answer.data.confirmed_personal_expense_paise === "number" && <div className="rounded-2xl bg-white p-3"><p className="text-[11px] text-ink/45">Confirmed personal share</p><p className="mt-1 font-semibold">{formatRupees(answer.data.confirmed_personal_expense_paise)}</p></div>}</div>}
      </div> : <p className="mt-4 rounded-2xl bg-cream p-5 text-sm text-ink/45">Answers include the actual account and inclusive date period used.</p>}
    </Card>

    {rows.length > 0 && <Card className="p-5 sm:p-6"><div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><div><h2 className="font-display text-xl font-semibold">Transactions in this result</h2><p className="mt-1 text-xs text-ink/45">Selection: {selected.length} · selected gross debits: {formatRupees(selectionTotal)}</p></div><div className="flex flex-wrap gap-2"><Button size="sm" variant="secondary" disabled={selectingAll} onClick={() => void syncSelection([], true)}>Select all matching</Button><Button size="sm" variant="ghost" disabled={selectingAll} onClick={() => void syncSelection([])}>Clear selection</Button><Button size="sm" disabled={!selected.length || selectingAll} onClick={() => setSplitOpen(value => !value)}><SplitIcon size={14} /> Split selected</Button></div></div>
      <div className="mt-4 divide-y divide-sand">{rows.map((transaction: Transaction & { personal_expense_paise?: number }) => {
        const checked = selected.includes(transaction.id);
        const isExpanded = expanded.includes(transaction.id);
        return <article key={transaction.id} className="flex gap-3 py-4"><input aria-label={`Select transaction ${transaction.recipient}`} type="checkbox" className="mt-1 h-4 w-4 accent-[#16856B]" checked={checked} disabled={selectingAll} onChange={() => { const next = checked ? selected.filter(id => id !== transaction.id) : [...selected, transaction.id]; void syncSelection(next); }} /><div className="min-w-0 flex-1"><div className="flex flex-wrap items-start justify-between gap-2"><div className="min-w-0"><p className="truncate text-sm font-semibold">{transaction.merchant || transaction.recipient}</p><p className="mt-1 text-xs text-ink/50">{formatDate(transaction.occurred_at)} · {accounts.find(account => account.id === transaction.account_id)?.name || "Account"} · {transaction.category}</p><p className="mt-1 text-[11px] text-ink/40">{transaction.transaction_type || transaction.direction} · {transaction.treatment === "shared" ? `your share ${formatRupees(transaction.personal_expense_paise || 0)}` : transaction.direction === "debit" ? "debit" : "credit"}</p></div><div className="flex items-center gap-3 whitespace-nowrap"><span className={`text-sm font-bold ${transaction.direction === "credit" ? "text-pine" : "text-ink"}`}>{transaction.direction === "credit" ? "+" : "−"}{formatRupees(transaction.amount_paise)}</span><a className="text-xs font-semibold text-pine underline" href={transactionHref(transaction.id)}>Details</a></div></div><button type="button" onClick={() => setExpanded(current => isExpanded ? current.filter(id => id !== transaction.id) : [...current, transaction.id])} aria-expanded={isExpanded} className="mt-2 inline-flex items-center gap-1 text-[11px] font-semibold text-ink/50">Original narration {isExpanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}</button>{isExpanded && <p className="mt-2 break-words rounded-xl bg-white p-3 text-xs leading-5 text-ink/65">{transaction.recipient}{transaction.note ? ` · ${transaction.note}` : ""}</p>}</div></article>;
      })}</div>
      {hasMore && <div className="mt-4 flex justify-center"><Button variant="secondary" disabled={busy} onClick={() => void loadMore()}>{busy ? <LoaderCircle size={14} className="animate-spin" /> : <Plus size={14} />} Load next page</Button></div>}
      {splitOpen && <div className="mt-5 rounded-3xl border border-sand bg-[#fbf9f4] p-4 sm:p-5"><div className="flex items-start justify-between gap-3"><div><h3 className="font-display text-lg font-semibold">Prepare shared-expense preview</h3><p className="mt-1 text-xs leading-5 text-ink/50">No transaction changes occur until you confirm the server-generated preview.</p></div><button aria-label="Close split editor" className="rounded-full p-2 hover:bg-white" onClick={() => setSplitOpen(false)}><X size={16} /></button></div><div className="mt-4 flex flex-wrap gap-2">{(["equal", "custom", "percentage"] as const).map(method => <button key={method} onClick={() => setSplitMethod(method)} className={`rounded-full px-3 py-2 text-xs font-semibold capitalize ${splitMethod === method ? "bg-pine text-white" : "bg-white text-ink/60"}`}>{method} split</button>)}</div><div className="mt-3 space-y-2">{people.map((person, index) => <div key={index} className="grid gap-2 sm:grid-cols-[1fr_auto_1fr] sm:items-center"><input className="min-w-0 rounded-xl border border-sand bg-white px-3 py-2 text-sm" value={person.name} aria-label={`Participant ${index + 1} name`} onChange={event => setPeople(current => current.map((item, i) => i === index ? { ...item, name: event.target.value } : item))} placeholder="Participant name" /><label className="flex items-center gap-2 text-xs text-ink/60"><input type="checkbox" checked={person.is_self} onChange={event => setPeople(current => current.map((item, i) => ({ ...item, is_self: i === index ? event.target.checked : event.target.checked ? false : item.is_self })))} /> This is me</label>{splitMethod === "custom" ? <input className="rounded-xl border border-sand bg-white px-3 py-2 text-sm" value={person.amount} onChange={event => setPeople(current => current.map((item, i) => i === index ? { ...item, amount: event.target.value } : item))} placeholder="Share ₹" inputMode="decimal" /> : splitMethod === "percentage" ? <input className="rounded-xl border border-sand bg-white px-3 py-2 text-sm" value={person.percent} onChange={event => setPeople(current => current.map((item, i) => i === index ? { ...item, percent: event.target.value } : item))} placeholder="Share %" inputMode="decimal" /> : <span className="text-xs text-ink/35">Calculated in paise by backend</span>}</div>)}</div><div className="mt-3 flex flex-wrap gap-2"><Button size="sm" variant="secondary" onClick={() => setPeople(current => [...current, { name: "", is_self: false, amount: "", percent: "" }])}><Plus size={14} /> Add participant</Button>{people.length > 2 && <Button size="sm" variant="ghost" onClick={() => setPeople(current => current.slice(0, -1))}>Remove last</Button>}<Button size="sm" disabled={actionBusy || !selected.length} onClick={() => void createPreview()}>{actionBusy ? "Preparing…" : "Review split"}</Button></div></div>}
      {action?.status === "pending" && <div className="mt-5 rounded-3xl border border-[#c5e6d8] bg-white p-4 sm:p-5"><div className="flex flex-wrap items-center justify-between gap-3"><div><Badge tone="green">Confirmation required</Badge><p className="mt-2 font-display text-xl font-semibold">{formatRupees(action.preview.total_paise)} · {action.preview.transaction_count} transaction(s)</p><p className="mt-1 text-xs text-ink/45">Paid by you · {action.preview.split_method} split</p></div><div className="flex gap-2"><Button variant="secondary" disabled={actionBusy} onClick={() => void cancelAction()}>Cancel</Button><Button disabled={actionBusy} onClick={() => void confirmAction()}><Check size={14} /> {actionBusy ? "Confirming…" : "Confirm split"}</Button></div></div><div className="mt-4 grid gap-2 sm:grid-cols-2">{action.preview.participants.map(person => <div key={person.name} className="rounded-xl bg-cream px-3 py-2 text-sm"><div className="flex justify-between"><span>{person.name}{person.is_self ? " (you)" : ""}</span><strong>{formatRupees(person.share_paise)}</strong></div>{person.contact_status === "create_on_confirm" && <p className="mt-1 text-[11px] text-amber-700">New contact · created only if you confirm</p>}{person.contact_status === "existing" && <p className="mt-1 text-[11px] text-ink/45">Saved contact</p>}</div>)}</div><div className="mt-4 max-h-48 space-y-2 overflow-y-auto">{action.preview.transactions.map(transaction => <div key={transaction.id} className="flex justify-between gap-3 rounded-xl bg-cream px-3 py-2 text-xs"><span className="truncate">{transaction.date} · {transaction.recipient}</span><span className="font-semibold">{formatRupees(transaction.amount_paise)}</span></div>)}</div><p className="mt-3 text-xs text-ink/45">{action.preview.note}</p></div>}
    </Card>}

    {splits.some(split => split.participants.some(person => !person.is_self && person.remaining_paise > 0) || split.allocations.length > 0) && <Card className="p-5 sm:p-6"><h2 className="font-display text-xl font-semibold">Shared-expense balances</h2><p className="mt-1 text-xs text-ink/45">Outstanding shares and reimbursements come from saved split records.</p><div className="mt-4 space-y-3">{splits.map(split => <div key={split.id} className="rounded-2xl bg-cream p-4"><div className="flex flex-wrap justify-between gap-2"><p className="text-sm font-semibold">{split.recipient}</p><p className="text-xs text-ink/45">Original debit {formatRupees(split.total_paise)}</p></div><div className="mt-2 flex flex-wrap gap-2">{split.participants.filter(person => !person.is_self).map(person => <span key={person.id} className="rounded-full bg-white px-3 py-1.5 text-xs">{person.name}: {person.remaining_paise > 0 ? `${formatRupees(person.remaining_paise)} outstanding` : "settled"}</span>)}</div>{split.allocations.length > 0 && <p className="mt-2 text-[11px] text-ink/45">{split.allocations.filter(item => !item.reversed).length} reimbursement allocation(s) recorded</p>}</div>)}</div></Card>}

    <Card className="p-4 sm:p-5"><form onSubmit={onSubmit} className="space-y-3"><label htmlFor="spendwise-chat-input" className="text-xs font-semibold text-ink/55">Ask a follow-up or a new question</label><textarea id="spendwise-chat-input" value={question} onChange={event => setQuestion(event.target.value)} onKeyDown={onComposerKey} placeholder="Type any financial question… (Enter to send, Shift+Enter for a new line)" rows={3} maxLength={1000} className="w-full resize-y rounded-2xl border border-sand bg-white px-4 py-3 text-sm leading-6 outline-none transition focus:border-[#16856B] focus:ring-2 focus:ring-[#16856B]/15" /><div className="flex items-center justify-between gap-3"><p className="text-[11px] text-ink/40">{question.length}/1000 · free-form questions supported</p><Button disabled={busy || !question.trim()}>{busy ? <><LoaderCircle size={15} className="animate-spin" /> Thinking…</> : <><Sparkles size={15} /> Ask</>}</Button></div></form></Card>
    {notice && <p role="status" className="rounded-2xl bg-mint p-4 text-sm text-pine">{notice}</p>}
    {error && <p role="alert" className="rounded-2xl bg-[#ffebe4] p-4 text-sm text-[#a34e35]">{error}</p>}
  </div>;
}
