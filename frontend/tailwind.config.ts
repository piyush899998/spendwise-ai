import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#17392f",
        pine: "#1c6b53",
        mint: "#dff0e8",
        cream: "#f7f5ef",
        sand: "#ece9df",
        coral: "#eb8764",
        amber: "#e6ab59",
        lavender: "#8270DD",
        peach: "#F3A65B",
        sky: "#4A9DE3",
        emerald: "#16856B",
        rose: "#E76D85",
      },
      boxShadow: {
        soft: "0 16px 42px rgba(24, 58, 47, 0.08)",
        card: "0 8px 24px rgba(24, 58, 47, 0.06)",
        glow: "0 18px 50px rgba(130, 112, 221, 0.16)",
      },
      fontFamily: {
        sans: ["var(--font-inter)", "ui-sans-serif", "system-ui"],
        display: ["var(--font-space-grotesk)", "ui-sans-serif", "system-ui"],
      },
    },
  },
  plugins: [],
};

export default config;
