export type Classification = { category: string; source: string; confidence: number | null; confirmed: boolean; needs_review: boolean };
export type Account = { id: string; name: string; bank: string; account_type: string; masked_account_number: string; opening_balance_paise: number; current_balance_paise: number; is_active: boolean; created_at: string };
export type Transaction = { id: string; account_id?: string | null; recipient: string; merchant?: string | null; amount_paise: number; direction: "debit" | "credit"; status: string; source?: string | null; category: string; treatment: string; transaction_type?: string | null; note?: string; occurred_at: string; needs_review: boolean; classification: Classification };
export type Budget = { id: string; month: string; category: string; limit_paise: number; spent_paise: number; remaining_paise: number; overspent: boolean };
export type Goal = { id: string; name: string; target_paise: number; saved_paise: number; target_date?: string | null; color: string; is_active: boolean };
export type RecurringSchedule = { id: string; account_id: string; name: string; schedule_type: string; amount_paise: number; category: string; frequency: string; next_due_date: string; note: string; is_active: boolean };
export type Metrics = { gross_paid: number; gross_cash_outflow?: number; unclassified_debits_paise?: number; credits_received: number; income_received?: number; refunds_received?: number; reimbursements_received?: number; net_cash_outflow: number; personal_expense: number; outstanding: number; savings_rate_percent?: number; by_category: Record<string, number>; review_count: number };
export type DatePeriod = "this_month" | "last_month" | "last_3_months" | "last_6_months" | "this_year" | "all_time" | "month" | "custom";
export type GlobalFilters = { accountId?: string; period: DatePeriod; month?: string; year?: number; dateFrom?: string; dateTo?: string };
export type MarketPulse = { status: "available" | "partial" | "unavailable"; source: string | null; updated_at: string | null; gold: { status: string; source?: string; updated_at?: string | null; purity: string; unit: string; value_paise: number | null; price_22k_paise?: number | null; change_percent?: number | null; history?: { date: string; value: number }[]; historical?: { status: string; range: string; history: { date: string; value_paise: number }[]; source?: string; unit?: string; latest_observation?: string; change_paise?: number; change_percent?: number | null; available_ranges?: string[]; dropped_unmatched_dates?: number } }; indices: { symbol: string; status: string; value?: number; change?: number | null; change_percent?: number | null; observed_at?: string; source?: string; history: { date: string; value: number }[] }[]; news: { headline: string; publisher: string; published_at: string; url: string }[]; education: { title: string; body: string; kind: string }[]; provider_errors?: Record<string, string> };
export type Dashboard = { month: string; profile?: { display_name?: string | null }; filters: { account_id?: string | null; date_from: string; date_to: string; period: string }; metrics: Metrics; previous_metrics: Metrics; budget_utilization_percent: number; accounts: (Account & { balance_as_of_end_paise?: number })[]; budgets: Budget[]; goals: Goal[]; recurring_schedules: RecurringSchedule[]; category_breakdown: { category: string; amount_paise: number }[]; merchant_breakdown: { merchant: string; amount_paise: number }[]; transactions: Transaction[]; classification_sources: Record<string, number>; review_transactions: Transaction[]; report_text: string; trend: { month: string; income_received: number; personal_expense: number; gross_paid: number; outstanding: number }[]; daily_spending: { date: string; amount_paise: number }[] };
export type Participant = { id: string; name: string; share_paise: number; is_self: boolean; allocated_paise: number; remaining_paise: number; status?: string };
export type Split = { id: string; transaction_id: string; recipient: string; total_paise: number; participants: Participant[]; allocations: { id: string; share_id: string; credit_id: string; amount_paise: number; reversed: boolean }[] };
export type StatementPreviewRow = { index: number; date: string; description: string; amount_paise: number; direction: "debit" | "credit"; reference?: string | null; running_balance_paise?: number | null; page?: number | null; category: string; confidence?: number | null; warnings: string[]; transaction_type: string };
export type StatementPreview = { token: string; filename: string; format: "csv" | "pdf"; page_count: number; row_count: number; preview_rows: StatementPreviewRow[]; errors: { row?: number; page?: number; error: string }[]; warnings: string[]; account_identifier?: string | null; balance_reconciled?: boolean | null; opening_balance_paise?: number | null; closing_balance_paise?: number | null; reconciliation?: Record<string, number | null>; confirmation_blocked?: boolean; complete_preview?: boolean; requires_confirmation: boolean };
export type AIInsight = { key: string; kind: string; title: string; message: string; amount_paise?: number; transaction_id?: string; why?: string; confidence?: number };
export type AIAnalysis = { period: { date_from: string; date_to: string; account_id?: string | null }; metrics: Metrics; previous_metrics: Metrics; category_breakdown: { category: string; amount_paise: number }[]; merchant_breakdown: { merchant: string; amount_paise: number }[]; insights: AIInsight[]; largest_transactions: (Transaction & { id: string })[]; recurring_candidates: { id: string; merchant: string; amount_paise: number; interval_days: number; confidence: number; status: string }[] };
export type AIReport = { id: string; report_type: string; account_id?: string | null; date_from: string; date_to: string; status: string; created_at: string; content: AIAnalysis & { report_type?: string; generated_at?: string; scope_label?: string } };
export type CategorizationSummary = { scanned: number; automatically_categorized: number; suggested_for_review: number; still_uncategorized: number; skipped_manual: number; skipped_already_classified?: number; changed_transaction_ids?: string[]; period?: { date_from: string; date_to: string; account_id?: string | null } };
export type AIChatMessage = { role: "user" | "assistant"; content: string; tool?: string | null };
export type AIProviderDiagnostic = { code: string; message: string; status?: number };
export type CategoryRegistry = { categories: string[]; aliases?: Record<string, string> };
export type AIChatResponse = { answer: string; tool?: string | null; session_id: string; provider_status: "available" | "local_fallback" | "unavailable"; provider_model: string; ai_notice: string; provider_diagnostic?: AIProviderDiagnostic | null; context: { account_id?: string | null; account?: string; date_from: string; date_to: string; selected_transaction_ids?: string[] }; data?: { transactions?: (Transaction & { personal_expense_paise?: number })[]; total_count?: number; offset?: number; limit?: number; has_more?: boolean; gross_debit_paise?: number; confirmed_personal_expense_paise?: number; sum_semantics?: string }; transactions?: (Transaction & { id: string; personal_expense_paise?: number })[]; action_preview?: ChatAction; messages: AIChatMessage[] };
export type ChatAction = { id: string; status: "pending" | "committed" | "cancelled" | "expired"; action_type: string; expires_at: string; preview: { total_paise: number; split_method: string; transaction_count: number; note: string; participants: { name: string; is_self: boolean; share_paise: number; contact_status?: "self" | "existing" | "create_on_confirm" }[]; transactions: { id: string; date: string; recipient: string; account_id?: string | null; amount_paise: number; who_paid: string; shares: { name: string; share_paise: number; is_self: boolean }[] }[] } };

// Uvicorn's local default bind is IPv4 loopback. Use the same address to avoid
// localhost resolving to IPv6 on machines where the API only listens on IPv4.
const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000/api";

function filterParams(filters: GlobalFilters) { const search = new URLSearchParams(); if (filters.accountId) search.set("account_id", filters.accountId); search.set("period", filters.period); if (filters.month) search.set("month", filters.month); if (filters.year) search.set("year", String(filters.year)); if (filters.dateFrom) search.set("date_from", filters.dateFrom); if (filters.dateTo) search.set("date_to", filters.dateTo); return search; }

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers || {});
  if (!(init?.body instanceof FormData)) headers.set("Content-Type", "application/json");
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, { ...init, headers, cache: "no-store" });
  } catch (reason) {
    const detail = reason instanceof Error ? reason.message : "network request failed";
    throw new Error(`Cannot reach SpendWise API at ${API_URL}. Check that the backend is running and that its CORS origins include this frontend. (${detail})`);
  }
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    let detail = text;
    try { detail = JSON.parse(text)?.detail || text; } catch { /* retain plain-text server detail */ }
    throw new Error(detail.trim().slice(0, 500) || `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  categories: () => request<CategoryRegistry>("/categories"),
  marketPulse: (refresh = false, goldRange = "1m") => request<MarketPulse>(`/market/pulse?gold_range=${encodeURIComponent(goldRange)}${refresh ? "&refresh=true" : ""}`),
  dashboard: (filters: GlobalFilters) => request<Dashboard>(`/dashboard?${filterParams(filters).toString()}`),
  accounts: () => request<{ accounts: Account[] }>("/accounts"),
  accountDashboard: (accountId: string, filters: Omit<GlobalFilters, "accountId">) => api.dashboard({ ...filters, accountId }),
  splits: () => request<{ splits: Split[] }>("/splits"),
  transactions: (params: { month?: string; account_id?: string; transaction_type?: string; category?: string; q?: string; date_from?: string; date_to?: string; period?: string; year?: number }) => {
    const search = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => { if (value) search.set(key, String(value)); });
    return request<{ transactions: Transaction[] }>(`/transactions?${search.toString()}`);
  },
  createAccount: (payload: { name: string; bank: string; account_type: string; masked_account_number: string; opening_balance_paise: number }) => request<{ account: Account }>("/accounts", { method: "POST", body: JSON.stringify(payload) }),
  updateAccount: (accountId: string, payload: { name?: string; bank?: string; account_type?: string; masked_account_number?: string; is_active?: boolean }) => request<{ account: Account }>(`/accounts/${accountId}`, { method: "PATCH", body: JSON.stringify(payload) }),
  createEntry: (payload: { account_id: string; amount_paise: number; transaction_type: string; direction?: string; category: string; recipient: string; note: string; reference?: string }) => request<{ transaction: Transaction }>("/transactions", { method: "POST", body: JSON.stringify(payload) }),
  transaction: (id: string) => request<{ transaction: Transaction }>(`/transactions/${id}`),
  updateTransaction: (id: string, payload: { category?: string; note?: string }) => request<{ transaction: Transaction }>(`/transactions/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  createTransfer: (payload: { from_account_id: string; to_account_id: string; amount_paise: number; note: string; reference?: string }) => request<{ transfer: { group_id: string } }>("/transfers", { method: "POST", body: JSON.stringify(payload) }),
  budgets: (month: string) => request<{ budgets: Budget[] }>(`/budgets?month=${month}`),
  createBudget: (payload: { month: string; category: string; limit_paise: number }) => request<{ budget: Budget }>("/budgets", { method: "POST", body: JSON.stringify(payload) }),
  goals: () => request<{ goals: Goal[] }>("/goals"),
  createGoal: (payload: { name: string; target_paise: number; target_date?: string; color: string }) => request<{ goal: Goal }>("/goals", { method: "POST", body: JSON.stringify(payload) }),
  contributeGoal: (goalId: string, payload: { amount_paise: number; note: string }) => request<{ goal: Goal }>(`/goals/${goalId}/contributions`, { method: "POST", body: JSON.stringify(payload) }),
  recurring: () => request<{ schedules: RecurringSchedule[] }>("/recurring"),
  createRecurring: (payload: { account_id: string; name: string; schedule_type: string; amount_paise: number; category: string; next_due_date: string; frequency: string; note: string }) => request<{ schedule: RecurringSchedule }>("/recurring", { method: "POST", body: JSON.stringify(payload) }),
  updateRecurring: (id: string, payload: Partial<RecurringSchedule>) => request<{ schedule: RecurringSchedule }>(`/recurring/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  confirmRecurring: (scheduleId: string, dueDate: string) => request<{ transaction: Transaction }>(`/recurring/${scheduleId}/confirm?due_date=${dueDate}`, { method: "POST" }),
  resetDemo: (seed: boolean) => request<{ seeded: boolean }>("/demo/reset", { method: "POST", body: JSON.stringify({ seed }) }),
  createPayment: (payload: { recipient: string; amount_paise: number; category: string; treatment: string; note: string; remember: boolean; outcome: string; account_id?: string }) => request<{ intent: Record<string, unknown>; transaction: Transaction | null }>("/payments", { method: "POST", body: JSON.stringify(payload) }),
  createSplit: (payload: { transaction_id: string; participants: { name: string; share_paise: number; is_self: boolean }[] }) => request<{ split: Split }>("/splits", { method: "POST", body: JSON.stringify(payload) }),
  createIncoming: (payload: { sender: string; amount_paise: number; reference?: string }) => request<{ transaction: Transaction }>("/incoming", { method: "POST", body: JSON.stringify(payload) }),
  createReimbursement: (payload: { split_id: string; share_id: string; credit_id: string; amount_paise: number }) => request<{ allocation: { id: string } }>("/reimbursements", { method: "POST", body: JSON.stringify(payload) }),
  reverseReimbursement: (allocationId: string) => request<{ status: string }>(`/reimbursements/${allocationId}/reverse`, { method: "POST" }),
  reviewTransaction: (transactionId: string, payload: { category: string; treatment: string }) => request<{ transaction: Transaction }>(`/transactions/${transactionId}/review`, { method: "POST", body: JSON.stringify(payload) }),
  aiInsights: (filters: GlobalFilters) => request<AIAnalysis>(`/ai/insights?${filterParams(filters).toString()}`),
  aiReview: (filters: GlobalFilters) => request<{ transactions: Transaction[]; count: number }>(`/ai/categorization/review?${filterParams(filters).toString()}`),
  aiBulkCategorize: (filters: GlobalFilters) => request<CategorizationSummary>(`/ai/categorization/bulk?${filterParams(filters).toString()}`, { method: "POST" }),
  aiCategorize: (id: string, payload: { category: string; treatment: string; apply_to_similar: boolean }) => request<{ transaction: Transaction }>(`/ai/categorization/${id}`, { method: "POST", body: JSON.stringify(payload) }),
  aiFeedback: (key: string, feedback: "useful" | "not_useful" | "dismissed") => request<{ id: string }>(`/ai/insights/${encodeURIComponent(key)}/feedback`, { method: "POST", body: JSON.stringify({ feedback }) }),
  aiChat: (filters: GlobalFilters, question: string, session_id?: string) => request<AIChatResponse>(`/ai/chat?${filterParams(filters).toString()}`, { method: "POST", body: JSON.stringify({ question, session_id }) }),
  aiChatTransactionPage: (session_id: string, offset: number) => request<{ transactions: (Transaction & { personal_expense_paise?: number })[]; total_count: number; offset: number; has_more: boolean; gross_debit_paise: number; confirmed_personal_expense_paise: number }>(`/ai/chat/transactions?session_id=${encodeURIComponent(session_id)}&offset=${offset}`),
  aiChatSelection: (session_id: string, transaction_ids: string[], select_all_matching = false) => request<{ selected_transaction_ids: string[]; count: number; gross_debit_paise: number }>("/ai/chat/selection", { method: "POST", body: JSON.stringify({ session_id, transaction_ids, select_all_matching }) }),
  aiSplitPreview: (payload: { session_id: string; idempotency_key: string; split_method: "equal" | "custom" | "percentage"; participants: { name: string; is_self: boolean; share_paise?: number; basis_points?: number }[] }) => request<{ action: ChatAction }>("/ai/actions/split/preview", { method: "POST", body: JSON.stringify(payload) }),
  aiConfirmAction: (preview_id: string) => request<{ status: string; idempotent_replay?: boolean; transaction_count?: number }>("/ai/actions/confirm", { method: "POST", body: JSON.stringify({ preview_id }) }),
  aiCancelAction: (preview_id: string) => request<{ status: string }>("/ai/actions/cancel", { method: "POST", body: JSON.stringify({ preview_id }) }),
  aiGenerateReport: (filters: GlobalFilters, report_type: "weekly" | "monthly") => request<{ report: AIReport }>(`/ai/reports?${filterParams(filters).toString()}`, { method: "POST", body: JSON.stringify({ report_type }) }),
  aiReports: () => request<{ reports: AIReport[] }>("/ai/reports"),
  aiReportDownloadUrl: (id: string, format: "pdf" | "json" = "pdf") => `${API_URL}/ai/reports/${id}/download?format=${format}`,
  importCsv: (filename: string, text: string) => request<{ batch_id: string; added: number; skipped: number; error_count: number; errors: { row: number; error: string }[]; skipped_rows: { row: number; reason: string }[] }>("/imports", { method: "POST", body: JSON.stringify({ filename, text }) }),
  previewStatement: (file: File, password?: string, accountId?: string) => { const form = new FormData(); form.append("file", file); if (password) form.append("password", password); if (accountId) form.append("account_id", accountId); return request<StatementPreview>("/imports/preview", { method: "POST", body: form }); },
  confirmStatement: (token: string, payload: { account_id?: string; category_corrections: Record<number, string> }) => request<{ batch_id: string; added: number; skipped: number; error_count: number; errors: { row: number; error: string }[]; confirmed: boolean; categorization: CategorizationSummary }>(`/imports/${token}/confirm`, { method: "POST", body: JSON.stringify(payload) }),
};
