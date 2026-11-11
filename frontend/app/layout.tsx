import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SpendWise AI",
  description: "Understand what your money was for.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
