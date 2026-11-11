"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AlertCircle, Check, FileText, Loader2, LockKeyhole, UploadCloud, X } from "lucide-react";
import { api, Account, CategorizationSummary, StatementPreview } from "@/lib/api";
import { Badge, Button, Select } from "@/components/ui";
import { formatRupees } from "@/lib/utils";

const REQUIRED_COLUMNS = ["date", "recipient", "amount", "direction", "reference", "note"];
type ParsedCsv = { headers: string[]; rows: string[][] };

function parseCsv(text: string): ParsedCsv {
  const records: string[][] = []; let row: string[] = []; let cell = ""; let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index]; const next = text[index + 1];
    if (char === '"' && quoted && next === '"') { cell += '"'; index += 1; continue; }
    if (char === '"') { quoted = !quoted; continue; }
    if (char === "," && !quoted) { row.push(cell.trim()); cell = ""; continue; }
    if ((char === "\n" || char === "\r") && !quoted) {
      if (char === "\r" && next === "\n") index += 1;
      row.push(cell.trim()); if (row.some(value => value)) records.push(row); row = []; cell = ""; continue;
    }
    cell += char;
  }
  if (cell || row.length) { row.push(cell.trim()); records.push(row); }
  return { headers: records[0] || [], rows: records.slice(1) };
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function csvValidation(parsed: ParsedCsv) {
  const headers = parsed.headers.map(header => header.toLowerCase().trim());
  const missing = REQUIRED_COLUMNS.filter(column => !headers.includes(column));
  if (missing.length) return { headers, missing, errors: [] as string[] };
  const position = Object.fromEntries(REQUIRED_COLUMNS.map(column => [column, headers.indexOf(column)]));
  const errors = parsed.rows.slice(0, 10).flatMap((row, index) => {
    const number = index + 2; const messages: string[] = [];
    if (!row[position.recipient]?.trim()) messages.push(`Row ${number}: recipient is missing`);
    if (!row[position.date] || Number.isNaN(Date.parse(row[position.date]))) messages.push(`Row ${number}: date is invalid`);
    if (!row[position.amount] || Number.isNaN(Number(row[position.amount]))) messages.push(`Row ${number}: amount is invalid`);
    if (!["debit", "credit"].includes((row[position.direction] || "").toLowerCase())) messages.push(`Row ${number}: direction must be debit or credit`);
    return messages;
  });
  return { headers, missing, errors };
}

type ImportResult = { added: number; skipped: number; error_count: number; errors: { row: number; error: string }[]; categorization?: CategorizationSummary };

export function CsvImportDialog({ onClose, onImported }: { onClose: () => void; onImported: () => Promise<void> }) {
  const [file, setFile] = useState<File | null>(null); const [text, setText] = useState("");
  const [parsed, setParsed] = useState<ParsedCsv>({ headers: [], rows: [] }); const [dragging, setDragging] = useState(false);
  const [advanced, setAdvanced] = useState(false); const [password, setPassword] = useState(""); const [accounts, setAccounts] = useState<Account[]>([]);
  const [accountId, setAccountId] = useState(""); const [preview, setPreview] = useState<StatementPreview | null>(null);
  const [corrections, setCorrections] = useState<Record<number, string>>({}); const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null); const [result, setResult] = useState<ImportResult | null>(null);
  const [mounted, setMounted] = useState(false); const closeButtonRef = useRef<HTMLButtonElement>(null);
  const csvCheck = useMemo(() => csvValidation(parsed), [parsed]);

  useEffect(() => { void api.accounts().then(response => setAccounts(response.accounts)).catch(() => undefined); }, []);
  useEffect(() => {
    setMounted(true);
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKeyDown);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeButtonRef.current?.focus();
    return () => { document.removeEventListener("keydown", onKeyDown); document.body.style.overflow = previousOverflow; };
  }, [onClose]);

  const loadFile = async (candidate: File) => {
    setError(null); setResult(null); setPreview(null);
    if (!candidate.name.toLowerCase().endsWith(".csv") && !candidate.name.toLowerCase().endsWith(".pdf")) { setError("Choose a .csv or text-based .pdf file."); return; }
    setFile(candidate);
    if (candidate.name.toLowerCase().endsWith(".csv")) { const contents = await candidate.text(); setText(contents); setParsed(parseCsv(contents)); }
    else { setText(""); setParsed({ headers: [], rows: [] }); }
  };

  const previewImport = async () => {
    if (!file && !text.trim()) { setError("Choose a statement file or paste CSV text first."); return; }
    if (file?.name.toLowerCase().endsWith(".csv") && csvCheck.missing.length) { setError(`Missing required columns: ${csvCheck.missing.join(", ")}`); return; }
    const upload = file || new File([text], "pasted-import.csv", { type: "text/csv" });
    setBusy(true); setError(null); setResult(null);
    try { setPreview(await api.previewStatement(upload, password, accountId || undefined)); setPassword(""); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not preview statement"); }
    finally { setBusy(false); }
  };

  const confirmImport = async () => {
    if (!preview || preview.errors.length) return;
    setBusy(true); setError(null);
    try { const response = await api.confirmStatement(preview.token, { account_id: accountId || undefined, category_corrections: corrections }); setResult(response); setPreview(null); await onImported(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not confirm statement"); }
    finally { setBusy(false); }
  };

  const drop = async (event: React.DragEvent<HTMLDivElement>) => { event.preventDefault(); setDragging(false); const dropped = event.dataTransfer.files[0]; if (dropped) await loadFile(dropped); };
  const previewRows = preview?.preview_rows || [];
  const confirmationBlocked = Boolean(preview?.errors.length || preview?.confirmation_blocked);
  const groupedErrors = useMemo(() => {
    const groups = new Map<string, { count: number; locations: string[] }>();
    (preview?.errors || []).forEach(item => {
      const current = groups.get(item.error) || { count: 0, locations: [] };
      current.count += 1;
      if (current.locations.length < 3) current.locations.push(`p${item.page || "?"}/r${item.row || "?"}`);
      groups.set(item.error, current);
    });
    return Array.from(groups.entries());
  }, [preview?.errors]);
  const dialog = <div className="fixed inset-0 z-[100] flex items-center justify-center overflow-y-auto bg-[#17392f]/45 p-3 backdrop-blur-sm sm:p-6" role="dialog" aria-modal="true" aria-label="Import statement" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="max-h-[94vh] w-full max-w-4xl overflow-y-auto rounded-[30px] bg-white p-6 shadow-glow sm:p-8">
      <div className="flex items-start justify-between"><div><Badge tone="green">Private, staged import</Badge><h2 className="mt-3 font-display text-2xl font-semibold">Bring in a statement</h2><p className="mt-1 text-sm text-ink/50">Nothing reaches your ledger until you review and confirm.</p></div><button ref={closeButtonRef} onClick={onClose} className="rounded-full p-2 text-ink/40 hover:bg-cream" aria-label="Close import dialog"><X size={18} /></button></div>
      <div className={`mt-6 rounded-[26px] border-2 border-dashed p-7 text-center transition ${dragging ? "border-pine bg-mint/50" : "border-sand bg-cream/50"}`} onDragOver={event => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={drop}>
        <UploadCloud className="mx-auto text-pine" size={28} /><p className="mt-3 text-sm font-semibold">Drop a CSV or PDF statement here</p><p className="mt-1 text-xs text-ink/45">Text-based PDFs only · maximum 10 MB</p>
        <label className="mt-4 inline-flex h-10 cursor-pointer items-center gap-2 rounded-2xl bg-pine px-4 text-sm font-semibold text-white shadow-card hover:bg-[#155b46]"><FileText size={15} /> Choose statement<input className="sr-only" type="file" accept=".csv,.pdf,text/csv,application/pdf" onChange={event => { const selected = event.target.files?.[0]; if (selected) void loadFile(selected); }} /></label>
      </div>
      {file && <div className="mt-4 flex items-center gap-3 rounded-2xl bg-[#e6f2fc] p-4"><FileText className="text-sky" size={20} /><div className="min-w-0 flex-1"><p className="truncate text-sm font-semibold">{file.name}</p><p className="mt-1 text-xs text-ink/45">{formatBytes(file.size)}{file.name.toLowerCase().endsWith(".csv") ? ` · ${parsed.rows.length} data rows` : ""}</p></div><Badge tone="green">Ready</Badge></div>}
      {file?.name.toLowerCase().endsWith(".pdf") && <div className="mt-4 rounded-2xl bg-[#f0edff] p-4"><label className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.12em] text-ink/50"><LockKeyhole size={15} /> Temporary PDF password</label><input className="mt-2 h-10 w-full rounded-xl bg-white px-3 text-sm outline-none ring-1 ring-transparent focus:ring-lavender/50" type="password" value={password} onChange={event => setPassword(event.target.value)} autoComplete="off" placeholder="Only used to unlock this preview" /><p className="mt-2 text-xs text-ink/45">Never stored, logged or sent to external services.</p></div>}
      {accounts.length > 0 && <div className="mt-4"><label className="text-xs font-bold uppercase tracking-[0.12em] text-ink/45" htmlFor="import-account">Link to account (optional)</label><Select id="import-account" className="mt-2" value={accountId} onChange={event => setAccountId(event.target.value)}><option value="">Unassigned</option>{accounts.map(account => <option value={account.id} key={account.id}>{account.name} · {account.masked_account_number}</option>)}</Select></div>}
      {file?.name.toLowerCase().endsWith(".csv") && <div className="mt-5 flex items-center justify-between"><div><p className="text-xs font-bold uppercase tracking-[0.14em] text-ink/40">Local preview</p><p className="mt-1 text-sm text-ink/55">First {Math.min(10, parsed.rows.length)} of {parsed.rows.length} transactions</p></div><button className="text-xs font-semibold text-pine" onClick={() => setAdvanced(value => !value)} aria-expanded={advanced}>{advanced ? "Hide" : "Show"} advanced paste</button></div>}
      {advanced && <textarea className="mt-3 min-h-28 w-full rounded-2xl bg-cream p-4 text-xs outline-none ring-1 ring-transparent focus:bg-white focus:ring-pine/30" value={text} onChange={event => { setText(event.target.value); setFile(null); setParsed(parseCsv(event.target.value)); setPreview(null); }} placeholder="date,recipient,amount,direction,reference,note" aria-label="Paste CSV text" />}
      {file?.name.toLowerCase().endsWith(".csv") && parsed.headers.length > 0 && <div className="mt-3 overflow-x-auto rounded-2xl border border-sand"><table className="min-w-full text-left text-xs"><thead className="bg-cream"><tr>{parsed.headers.map(header => <th className="whitespace-nowrap px-3 py-2 font-bold text-ink/50" key={header}>{header}</th>)}</tr></thead><tbody>{parsed.rows.slice(0, 10).map((row, index) => <tr className="border-t border-sand" key={`${index}-${row[0]}`}>{parsed.headers.map((_, column) => <td className="max-w-44 truncate px-3 py-2 text-ink/65" key={column}>{row[column] || "—"}</td>)}</tr>)}</tbody></table></div>}
      {file?.name.toLowerCase().endsWith(".csv") && csvCheck.missing.length > 0 && <div className="mt-4 flex gap-2 rounded-2xl bg-[#ffebef] p-4 text-sm text-[#b9445c]"><AlertCircle className="shrink-0" size={17} />Missing required columns: {csvCheck.missing.join(", ")}</div>}
      {preview && <div className="mt-5 rounded-[24px] border border-sand p-4"><div className="flex flex-wrap items-center justify-between gap-2"><div><p className="text-xs font-bold uppercase tracking-[0.14em] text-ink/40">Server preview · {preview.format.toUpperCase()}</p><p className="mt-1 text-sm text-ink/60">{preview.row_count} complete transaction rows · {preview.page_count} page{preview.page_count === 1 ? "" : "s"}{preview.account_identifier ? ` · ${preview.account_identifier}` : ""}</p></div><Badge tone={confirmationBlocked ? "amber" : "green"}>{confirmationBlocked ? "Resolution required" : "Ready to confirm"}</Badge></div>{preview.reconciliation && <div className="mt-3 grid gap-2 rounded-2xl bg-cream p-3 text-xs text-ink/60 sm:grid-cols-2"><span>Opening: {preview.reconciliation.opening_balance_paise == null ? "not found" : formatRupees(preview.reconciliation.opening_balance_paise)}</span><span>Statement closing: {preview.reconciliation.summary_closing_balance_paise == null ? "not found" : formatRupees(preview.reconciliation.summary_closing_balance_paise)}</span><span>Last running balance: {preview.reconciliation.extracted_closing_balance_paise == null ? "not found" : formatRupees(preview.reconciliation.extracted_closing_balance_paise)}</span><span>Debits: {preview.reconciliation.extracted_withdrawals_paise == null ? "not found" : formatRupees(preview.reconciliation.extracted_withdrawals_paise)}</span><span>Credits: {preview.reconciliation.extracted_deposits_paise == null ? "not found" : formatRupees(preview.reconciliation.extracted_deposits_paise)}</span></div>}{(preview.warnings.length > 0 || preview.errors.length > 0) && <div className="mt-3 space-y-2">{preview.warnings.map(warning => <p className="rounded-xl bg-[#fff0df] p-3 text-xs text-[#8d5518]" key={warning}><AlertCircle className="mr-1 inline" size={14} />{warning}</p>)}{groupedErrors.map(([message, group]) => <p className="rounded-xl bg-[#ffebef] p-3 text-xs text-[#b9445c]" key={message}><AlertCircle className="mr-1 inline" size={14} />{message} · {group.count} row{group.count === 1 ? "" : "s"} ({group.locations.join(", ")}{group.count > group.locations.length ? ", …" : ""})</p>)}</div>}{confirmationBlocked && <p className="mt-3 rounded-xl bg-[#ffebef] p-3 text-xs font-semibold text-[#b9445c]">Bulk confirmation is blocked until every extraction and reconciliation discrepancy is resolved.</p>}
        {previewRows.length > 0 && <div className="mt-3 overflow-x-auto rounded-2xl border border-sand"><table className="min-w-full text-left text-xs"><thead className="bg-cream"><tr><th className="px-3 py-2">Date</th><th className="px-3 py-2">Description</th><th className="px-3 py-2">Amount</th><th className="px-3 py-2">Category correction</th></tr></thead><tbody>{previewRows.map(row => <tr className="border-t border-sand" key={row.index}><td className="whitespace-nowrap px-3 py-2">{row.date}</td><td className="max-w-52 truncate px-3 py-2">{row.description}<span className="block text-[10px] text-ink/40">{row.direction} · {row.reference || "no reference"}</span></td><td className="whitespace-nowrap px-3 py-2 font-semibold">{formatRupees(row.amount_paise)}</td><td className="px-3 py-2"><input className="h-8 w-36 rounded-lg border border-sand px-2 text-xs" value={corrections[row.index] || ""} placeholder={row.category} onChange={event => setCorrections(current => ({ ...current, [row.index]: event.target.value }))} aria-label={`Category correction for row ${row.index + 1}`} /></td></tr>)}</tbody></table></div>}
      </div>}
      {result && <><div className="mt-4 grid gap-2 sm:grid-cols-3"><div className="rounded-2xl bg-mint p-4"><p className="text-xs text-ink/45">Imported</p><p className="mt-1 text-2xl font-bold text-pine">{result.added}</p></div><div className="rounded-2xl bg-[#fff0df] p-4"><p className="text-xs text-ink/45">Skipped duplicates</p><p className="mt-1 text-2xl font-bold text-[#8d5518]">{result.skipped}</p></div><div className="rounded-2xl bg-[#ffebef] p-4"><p className="text-xs text-ink/45">Rejected rows</p><p className="mt-1 text-2xl font-bold text-[#b9445c]">{result.error_count}</p></div></div>{result.categorization && <div className="mt-3 rounded-2xl bg-[#eeeaff] p-4"><p className="text-sm font-semibold">Categorization complete</p><div className="mt-3 grid grid-cols-2 gap-3 text-xs sm:grid-cols-4"><span>Auto: <strong>{result.categorization.automatically_categorized}</strong></span><span>Review: <strong>{result.categorization.suggested_for_review}</strong></span><span>Uncategorized: <strong>{result.categorization.still_uncategorized}</strong></span><span>Manual skipped: <strong>{result.categorization.skipped_manual}</strong></span></div></div>}</>}
      {error && <p className="mt-4 flex items-center gap-2 rounded-2xl bg-[#ffebef] p-4 text-sm text-[#b9445c]"><AlertCircle size={17} />{error}</p>}
      <div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end"><Button variant="ghost" onClick={onClose}>Cancel</Button>{preview ? <Button onClick={confirmImport} disabled={busy || confirmationBlocked}>{busy ? <Loader2 className="animate-spin" size={16} /> : <Check size={16} />} Confirm and import</Button> : <Button onClick={previewImport} disabled={busy || (!file && !text.trim())}>{busy ? <Loader2 className="animate-spin" size={16} /> : <UploadCloud size={16} />} Preview securely</Button>}</div>
    </div>
  </div>;
  return mounted ? createPortal(dialog, document.body) : null;
}
