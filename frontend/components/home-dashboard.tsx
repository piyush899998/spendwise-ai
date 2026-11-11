"use client";

import * as React from "react";
import { motion, useReducedMotion } from "framer-motion";
import { ArrowDownLeft, ArrowUpRight, ArrowUpRight as OpenIcon, CalendarClock, ChevronRight, CreditCard, Landmark, Plus, RefreshCw, Sparkles, Target, WalletCards } from "lucide-react";
import { type Dashboard, type Transaction } from "@/lib/api";
import { formatDate, formatRupees, monthLabel } from "@/lib/utils";
import { Badge, Button, Card, Progress } from "@/components/ui";
import { TrendChart } from "@/components/finance";
import { DashboardVisuals } from "@/components/dashboard-visuals";
import { MarketModules } from "@/components/market-modules";

type Screen = "home" | "send" | "split" | "insights" | "accounts" | "transactions" | "recurring" | "budgets" | "goals";

const tileStyles = {
  emerald: "bg-[#16856B] text-white",
  lavender: "bg-[#8270DD] text-white",
  coral: "bg-[#E76D85] text-white",
  peach: "bg-[#F3A65B] text-[#4a2e12]",
  sky: "bg-[#4A9DE3] text-white",
};

function HomeTransaction({ transaction }: { transaction: Transaction }) {
  const credit = transaction.direction === "credit";
  return <div className="flex items-center gap-3 py-3"><div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl ${credit ? "bg-[#e4f4ed] text-[#16856B]" : "bg-[#ffebef] text-[#E76D85]"}`}>{credit ? <ArrowDownLeft size={17} /> : <ArrowUpRight size={17} />}</div><div className="min-w-0 flex-1"><p className="truncate text-sm font-semibold">{transaction.recipient}</p><p className="mt-1 truncate text-xs text-ink/45">{transaction.category} · {formatDate(transaction.occurred_at)}</p></div><p className={`text-sm font-bold ${credit ? "text-[#16856B]" : "text-ink"}`}>{credit ? "+" : "−"}{formatRupees(transaction.amount_paise)}</p></div>;
}

function MetricTile({ label, value, caption, tone, onClick }: { label: string; value: string; caption: string; tone: keyof typeof tileStyles; onClick: () => void }) {
  const reduced = useReducedMotion();
  return <motion.button whileHover={reduced ? undefined : { y: -4, scale: 1.01 }} whileTap={reduced ? undefined : { scale: 0.98 }} onClick={onClick} className={`min-h-[148px] rounded-[30px] p-5 text-left shadow-card transition-shadow hover:shadow-soft ${tileStyles[tone]}`}><div className="flex items-start justify-between"><span className="text-xs font-bold uppercase tracking-[0.14em] opacity-75">{label}</span><OpenIcon size={16} className="opacity-70" /></div><p className="mt-8 font-display text-3xl font-semibold tracking-tight">{value}</p><p className="mt-2 max-w-[14rem] text-xs leading-5 opacity-75">{caption}</p></motion.button>;
}

export function HomeDashboard({ data, month, accountId, onRefresh, onNavigate }: { data: Dashboard; month: string; accountId?: string; onRefresh: () => void; onNavigate: (screen: Screen) => void }) {
  const [greeting, setGreeting] = React.useState("Welcome back");
  const [quoteIndex, setQuoteIndex] = React.useState(0);
  const quotes = [
    "Small decisions today build financial freedom tomorrow.",
    "Every rupee deserves a purpose.",
    "Wealth grows when intention becomes habit.",
    "Track your money. Understand your choices.",
    "Financial freedom begins with financial clarity.",
  ];
  React.useEffect(() => {
    const hour = new Date().getHours();
    setGreeting(hour < 5 ? "Good night" : hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : hour < 22 ? "Good evening" : "Good night");
    const day = new Date();
    setQuoteIndex((day.getFullYear() + day.getMonth() + day.getDate()) % quotes.length);
  }, [quotes.length]);
  const selectedAccount = accountId ? data.accounts.find(account => account.id === accountId) : undefined;
  const combinedBalance = selectedAccount?.current_balance_paise ?? data.accounts.reduce((sum, account) => sum + account.current_balance_paise, 0);
  const totalCategorySpend = data.category_breakdown.reduce((sum, item) => sum + item.amount_paise, 0) || 1;
  const topCategory = data.category_breakdown[0];
  const reduced = useReducedMotion();
  return <div className="relative -mx-5 overflow-hidden px-5 pb-10 sm:-mx-8 sm:px-8 lg:-mx-12 lg:px-12">
    <div className="pointer-events-none absolute -top-32 right-[-10rem] h-[34rem] w-[34rem] rounded-full bg-[#e6e0ff] opacity-70 blur-3xl" />
    <div className="pointer-events-none absolute left-[-12rem] top-[32rem] h-[26rem] w-[26rem] rounded-full bg-[#ffe4c7] opacity-50 blur-3xl" />
    <div className="relative space-y-5 pt-2 sm:pt-4">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between"><div><div className="flex items-center gap-2"><Badge tone="green">Personal cockpit</Badge><span className="text-xs font-semibold text-ink/35">{monthLabel(data.month)}</span></div><h1 className="mt-4 max-w-2xl font-display text-4xl font-semibold leading-[1.02] tracking-[-0.045em] text-ink sm:text-6xl">{greeting}{data.profile?.display_name && data.profile.display_name !== "Demo User" ? `, ${data.profile.display_name}` : ""}.</h1><p className="mt-3 max-w-xl text-sm leading-6 text-ink/50">See the whole picture in one glance — what came in, what moved out, and what deserves your attention next.</p><button type="button" onClick={() => setQuoteIndex(index => (index + 1) % quotes.length)} className="mt-4 max-w-xl text-left text-sm font-semibold text-[#7C3AED] transition hover:text-[#5b21b6]" aria-label="Show another finance quote">“{quotes[quoteIndex]}” <span className="ml-1 text-xs font-normal text-ink/35">Next quote</span></button></div><div className="flex items-center gap-2"><button onClick={onRefresh} className="flex h-11 w-11 items-center justify-center rounded-full bg-white text-ink/50 shadow-card transition hover:text-pine" aria-label="Refresh dashboard"><RefreshCw size={17} /></button><select aria-label="Choose month" value={month} onChange={event => window.dispatchEvent(new CustomEvent("spendwise-month", { detail: event.target.value }))} className="h-11 rounded-full bg-white px-4 text-xs font-bold text-ink shadow-card outline-none ring-1 ring-transparent focus:ring-pine/30"><option value={month}>{monthLabel(month)}</option></select></div></header>

      <DashboardVisuals data={data} />
      <MarketModules />

      <section className="grid gap-5 xl:grid-cols-[1.35fr_.65fr]">
        <motion.div initial={reduced ? false : { opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="relative min-h-[330px] overflow-hidden rounded-[36px] bg-gradient-to-br from-[#102f28] via-[#16856B] to-[#8270DD] p-6 text-white shadow-glow sm:p-8"><div className="absolute -right-20 -top-24 h-72 w-72 rounded-full border-[42px] border-white/10" /><div className="absolute -bottom-36 right-20 h-72 w-72 rounded-full border-[34px] border-[#F3A65B]/35" /><div className="relative flex h-full flex-col justify-between"><div className="flex items-start justify-between"><div><div className="flex items-center gap-2 text-sm font-semibold text-white/70"><WalletCards size={16} /> {selectedAccount ? selectedAccount.name : `Total across ${data.accounts.length || 0} accounts`}</div><p className="mt-6 font-display text-5xl font-semibold tracking-[-0.05em] sm:text-7xl">{formatRupees(combinedBalance)}</p><p className="mt-2 text-sm text-white/60">Current balance · period metrics below use the active date range.</p></div><div className="rounded-2xl bg-white/15 p-3 backdrop-blur"><Sparkles size={20} /></div></div><div className="mt-10 flex flex-wrap items-end justify-between gap-5"><div><p className="text-xs uppercase tracking-[0.16em] text-white/50">Net cash flow</p><p className="mt-1 text-2xl font-bold">{formatRupees((data.metrics.income_received || data.metrics.credits_received) - data.metrics.net_cash_outflow)}</p></div><div className="flex gap-2"><Button size="sm" variant="secondary" onClick={() => onNavigate("transactions")}><Plus size={14} /> Add entry</Button><Button size="sm" variant="ghost" className="bg-white/10 text-white hover:bg-white/20 hover:text-white" onClick={() => onNavigate("accounts")}><CreditCard size={14} /> Accounts</Button></div></div></div></motion.div>
        <Card className="relative overflow-hidden bg-white/80 p-6 backdrop-blur sm:p-7"><div className="absolute right-0 top-0 h-32 w-32 rounded-bl-[5rem] bg-[#eeeaff]" /><div className="relative"><div className="flex items-center justify-between"><div><p className="text-xs font-bold uppercase tracking-[0.14em] text-ink/40">Account snapshot</p><h2 className="mt-1 font-display text-xl font-semibold">Your foundations</h2></div><Landmark className="text-[#8270DD]" size={22} /></div><div className="mt-7 space-y-4">{data.accounts.slice(0, 3).map((account, index) => <button key={account.id} onClick={() => onNavigate("accounts")} className="flex w-full items-center gap-3 rounded-2xl p-2 text-left transition hover:bg-cream"><div className={`flex h-10 w-10 items-center justify-center rounded-xl text-white ${index === 0 ? "bg-[#16856B]" : index === 1 ? "bg-[#4A9DE3]" : "bg-[#E76D85]"}`}><CreditCard size={16} /></div><div className="min-w-0 flex-1"><p className="truncate text-sm font-semibold">{account.name}</p><p className="mt-1 text-xs text-ink/40">{account.bank} · {account.masked_account_number}</p></div><p className="text-sm font-bold">{formatRupees(account.current_balance_paise)}</p></button>)}{!data.accounts.length && <p className="rounded-2xl bg-cream p-4 text-sm text-ink/50">Add a simulated account to see your full balance.</p>}</div><button onClick={() => onNavigate("accounts")} className="mt-7 flex items-center gap-2 text-xs font-bold text-[#8270DD]">Manage accounts <ChevronRight size={14} /></button></div></Card>
      </section>

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4"><MetricTile label="Income" value={formatRupees(data.metrics.income_received || data.metrics.credits_received)} caption="Posted credits this month" tone="sky" onClick={() => onNavigate("transactions")} /><MetricTile label="Personal spend" value={formatRupees(data.metrics.personal_expense)} caption="Your actual consumption" tone="coral" onClick={() => onNavigate("transactions")} /><MetricTile label="Savings rate" value={`${data.metrics.savings_rate_percent || 0}%`} caption="Income retained after personal spend" tone="lavender" onClick={() => onNavigate("insights")} /><MetricTile label="Budget used" value={`${data.budget_utilization_percent}%`} caption="Across active monthly budgets" tone="peach" onClick={() => onNavigate("budgets")} /></section>

      <section className="grid gap-5 xl:grid-cols-[1.35fr_.65fr]"><TrendChart data={data.trend} onMonthClick={selected => window.dispatchEvent(new CustomEvent("spendwise-month", { detail: selected }))} /><Card className="overflow-hidden bg-[#122f29] p-6 text-white sm:p-7"><div className="flex items-start justify-between"><div><p className="text-xs font-bold uppercase tracking-[0.14em] text-white/45">Where it went</p><h2 className="mt-1 font-display text-xl font-semibold">Category mosaic</h2></div><Target className="text-[#F3A65B]" size={22} /></div><div className="mt-7 flex items-center gap-5"><div className="relative h-32 w-32 shrink-0 rounded-full" style={{ background: `conic-gradient(#E76D85 0 ${Math.min(100, (data.category_breakdown[0]?.amount_paise || 0) / totalCategorySpend * 100)}%, #8270DD ${Math.min(100, (data.category_breakdown[0]?.amount_paise || 0) / totalCategorySpend * 100)}% ${Math.min(100, ((data.category_breakdown[0]?.amount_paise || 0) + (data.category_breakdown[1]?.amount_paise || 0)) / totalCategorySpend * 100)}%, #4A9DE3 0 100%)` }}><div className="absolute inset-4 flex items-center justify-center rounded-full bg-[#122f29] text-center"><span className="font-display text-2xl font-semibold">{topCategory ? Math.round(topCategory.amount_paise / totalCategorySpend * 100) : 0}%</span></div></div><div className="min-w-0 space-y-3">{data.category_breakdown.slice(0, 3).map((item, index) => <button key={item.category} onClick={() => onNavigate("insights")} className="flex w-full items-center gap-2 text-left text-xs"><span className={`h-2.5 w-2.5 rounded-full ${index === 0 ? "bg-[#E76D85]" : index === 1 ? "bg-[#8270DD]" : "bg-[#4A9DE3]"}`} /><span className="truncate text-white/65">{item.category}</span><span className="ml-auto font-bold text-white">{formatRupees(item.amount_paise)}</span></button>)}{!data.category_breakdown.length && <p className="text-xs text-white/50">Your first expense will create the mosaic.</p>}</div></div><button onClick={() => onNavigate("insights")} className="mt-8 flex items-center gap-2 text-xs font-bold text-[#F3A65B]">Open insights <ChevronRight size={14} /></button></Card></section>

      <section className="grid gap-5 lg:grid-cols-[1.1fr_.9fr]"><Card className="p-6 sm:p-7"><div className="flex items-center justify-between"><div><p className="text-xs font-bold uppercase tracking-[0.14em] text-ink/40">Latest movement</p><h2 className="mt-1 font-display text-2xl font-semibold">Recent transactions</h2></div><button onClick={() => onNavigate("transactions")} className="text-xs font-bold text-pine">See all</button></div><div className="mt-4 divide-y divide-sand">{data.transactions.slice(0, 5).map(transaction => <HomeTransaction transaction={transaction} key={transaction.id} />)}{!data.transactions.length && <p className="py-10 text-center text-sm text-ink/45">No transactions in this period yet.</p>}</div></Card><Card className="bg-gradient-to-br from-[#fff0df] via-white to-[#e6f2fc] p-6 sm:p-7"><div className="flex items-center justify-between"><div><p className="text-xs font-bold uppercase tracking-[0.14em] text-ink/40">On the horizon</p><h2 className="mt-1 font-display text-2xl font-semibold">Upcoming commitments</h2></div><CalendarClock className="text-[#E76D85]" size={23} /></div><div className="mt-5 space-y-3">{data.recurring_schedules.filter(schedule => schedule.is_active).slice(0, 3).map(schedule => <button key={schedule.id} onClick={() => onNavigate("recurring")} className="flex w-full items-center gap-3 rounded-2xl bg-white/70 p-3 text-left transition hover:bg-white"><div className="flex h-9 w-9 items-center justify-center rounded-xl bg-[#ffebef] text-[#E76D85]"><CalendarClock size={15} /></div><div className="min-w-0 flex-1"><p className="truncate text-sm font-semibold">{schedule.name}</p><p className="mt-1 text-xs text-ink/45">Due {schedule.next_due_date}</p></div><p className="text-sm font-bold">{formatRupees(schedule.amount_paise)}</p></button>)}{!data.recurring_schedules.filter(schedule => schedule.is_active).length && <p className="rounded-2xl bg-white/70 p-4 text-sm text-ink/50">No upcoming payments. Nice and quiet.</p>}</div><Button className="mt-5" size="sm" variant="secondary" onClick={() => onNavigate("recurring")}><CalendarClock size={15} /> Manage schedules</Button></Card></section>
    </div>
  </div>;
}
