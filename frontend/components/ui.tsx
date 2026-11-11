import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 rounded-2xl text-sm font-semibold transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-pine/40 disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        primary: "bg-pine text-white shadow-card hover:-translate-y-0.5 hover:bg-[#155b46]",
        secondary: "bg-white text-ink ring-1 ring-sand hover:bg-[#fcfbf7]",
        ghost: "text-ink/65 hover:bg-mint/60 hover:text-ink",
        coral: "bg-coral text-white shadow-card hover:-translate-y-0.5 hover:bg-[#dc7655]",
      },
      size: { sm: "h-9 px-3", md: "h-11 px-5", lg: "h-13 px-6 text-base" },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}
export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, asChild = false, ...props }, ref) => {
  const Comp = asChild ? Slot : "button";
  return <Comp className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />;
});
Button.displayName = "Button";

export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-[28px] bg-white shadow-card ring-1 ring-ink/[0.04]", className)} {...props} />;
}

export function Badge({ className, children, tone = "neutral" }: { className?: string; children: React.ReactNode; tone?: "neutral" | "green" | "amber" | "coral" | "lavender" }) {
  const tones = {
    neutral: "bg-sand/70 text-ink/65",
    green: "bg-mint text-pine",
    amber: "bg-[#fff1d8] text-[#95601a]",
    coral: "bg-[#ffebe4] text-[#a34e35]",
    lavender: "bg-[#eeeaff] text-[#6453bf]",
  };
  return <span className={cn("inline-flex items-center rounded-full px-2.5 py-1 text-[11px] font-semibold", tones[tone], className)}>{children}</span>;
}

export function Input({ className, ...props }: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn("h-12 w-full rounded-2xl bg-cream px-4 text-sm text-ink outline-none ring-1 ring-transparent transition focus:bg-white focus:ring-pine/30", className)} {...props} />;
}

export function Select({ className, ...props }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cn("h-12 w-full rounded-2xl bg-cream px-4 text-sm text-ink outline-none ring-1 ring-transparent transition focus:bg-white focus:ring-pine/30", className)} {...props} />;
}

export function Label({ children }: { children: React.ReactNode }) {
  return <label className="mb-2 block text-xs font-semibold uppercase tracking-[0.14em] text-ink/45">{children}</label>;
}

export function Progress({ value, className }: { value: number; className?: string }) {
  return <div className={cn("h-2 overflow-hidden rounded-full bg-sand", className)}><div className="h-full rounded-full bg-pine transition-all" style={{ width: `${Math.min(100, Math.max(0, value))}%` }} /></div>;
}
