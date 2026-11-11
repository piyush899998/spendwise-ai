"use client";

import { motion, useReducedMotion } from "framer-motion";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { TrendingDown, TrendingUp } from "lucide-react";
import { Card } from "@/components/ui";
import { cn, formatRupees } from "@/lib/utils";

export function AnimatedNumber({ value, className }: { value: number; className?: string }) {
  const reduced = useReducedMotion();
  return <motion.span className={className} initial={reduced ? false : { opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }}>{formatRupees(value)}</motion.span>;
}

export function FinanceKpi({ label, value, displayValue, caption, tone = "emerald", onClick }: { label: string; value: number; displayValue?: string; caption: string; tone?: "emerald" | "lavender" | "peach" | "sky" | "coral"; onClick?: () => void }) {
  const tones = {
    emerald: "bg-[#e4f4ed] text-[#16856b]",
    lavender: "bg-[#eeeaff] text-[#6453bf]",
    peach: "bg-[#fff0df] text-[#bd6b16]",
    sky: "bg-[#e6f2fc] text-[#2877b7]",
    coral: "bg-[#ffebef] text-[#b9445c]",
  };
  return <motion.div whileHover={{ y: -3 }} transition={{ duration: 0.2 }}><Card className={cn("p-5 transition-shadow hover:shadow-soft", onClick && "cursor-pointer")} onClick={onClick} role={onClick ? "button" : undefined} tabIndex={onClick ? 0 : undefined} onKeyDown={event => { if (onClick && (event.key === "Enter" || event.key === " ")) onClick(); }}><div className={cn("inline-flex rounded-2xl px-3 py-2 text-xs font-bold", tones[tone])}>{label}</div><p className="mt-5 font-display text-2xl font-semibold tracking-tight">{displayValue || <AnimatedNumber value={value} />}</p><p className="mt-2 text-xs text-ink/45">{caption}</p></Card></motion.div>;
}

export function TrendChart({ data, onMonthClick }: { data: { month: string; personal_expense: number; gross_paid: number; outstanding: number }[]; onMonthClick?: (month: string) => void }) {
  // Recharts' formatter overload is stricter than its runtime value contract.
  // @ts-expect-error Recharts supplies nullable numeric tooltip values.
  return <Card className="p-5 sm:p-6"><div className="flex items-start justify-between"><div><p className="text-xs font-semibold uppercase tracking-[0.14em] text-ink/40">Cash story</p><h2 className="mt-1 font-display text-xl font-semibold">Income vs expense trend</h2></div><div className="flex items-center gap-3 text-[11px] font-semibold text-ink/45"><span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-[#16856b]" />Personal</span><span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-[#8270dd]" />Gross paid</span></div></div><div className="mt-5 h-64 w-full"><ResponsiveContainer width="100%" height="100%"><AreaChart data={data} onClick={payload => { const month = payload?.activeLabel; if (month && onMonthClick) onMonthClick(String(month)); }} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}><defs><linearGradient id="personalFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#16856b" stopOpacity={0.32} /><stop offset="100%" stopColor="#16856b" stopOpacity={0.02} /></linearGradient><linearGradient id="grossFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#8270dd" stopOpacity={0.22} /><stop offset="100%" stopColor="#8270dd" stopOpacity={0.02} /></linearGradient></defs><CartesianGrid stroke="#ece9df" vertical={false} /><XAxis dataKey="month" tickFormatter={month => String(month).slice(5)} tick={{ fontSize: 11, fill: "#789087" }} axisLine={false} tickLine={false} /><YAxis tickFormatter={value => `₹${Math.round(Number(value) / 1000)}k`} tick={{ fontSize: 10, fill: "#789087" }} axisLine={false} tickLine={false} /><Tooltip formatter={(value: number) => formatRupees(value)} labelFormatter={label => `Month ${label}`} contentStyle={{ borderRadius: 16, border: "1px solid #ece9df", boxShadow: "0 12px 30px rgba(24,58,47,.12)" }} /><Area type="monotone" dataKey="personal_expense" name="Personal expense" stroke="#16856b" fill="url(#personalFill)" strokeWidth={3} /><Area type="monotone" dataKey="gross_paid" name="Gross paid" stroke="#8270dd" fill="url(#grossFill)" strokeWidth={2} strokeDasharray="5 5" /></AreaChart></ResponsiveContainer></div><p className="mt-2 text-xs text-ink/35">Select a month to inspect its ledger. Values come from posted transactions.</p></Card>;
}

export function TrendDelta({ current, previous }: { current: number; previous: number }) {
  const delta = current - previous;
  const down = delta <= 0;
  return <span className={cn("inline-flex items-center gap-1 text-xs font-semibold", down ? "text-[#16856b]" : "text-[#b9445c]")}>{down ? <TrendingDown size={13} /> : <TrendingUp size={13} />}{formatRupees(Math.abs(delta))} vs prior month</span>;
}
