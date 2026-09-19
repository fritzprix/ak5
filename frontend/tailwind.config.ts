import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "var(--background)",
        foreground: "var(--foreground)",
      },
      keyframes: {
        pulseBorder: {
          "0%, 100%": { borderColor: "rgba(59, 130, 246, 0.8)", boxShadow: "0 0 12px rgba(59, 130, 246, 0.4)" },
          "50%": { borderColor: "rgba(147, 51, 234, 0.8)", boxShadow: "0 0 16px rgba(147, 51, 234, 0.6)" },
        },
      },
      animation: {
        "agent-pulse": "pulseBorder 2s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
export default config;
