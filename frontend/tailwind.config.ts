import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        mono: ["'JetBrains Mono'", "'Fira Code'", "Menlo", "Consolas", "monospace"],
      },
      colors: {
        // Trading terminal colour palette
        terminal: {
          bg:       "#0d1117",
          surface:  "#161b22",
          border:   "#21262d",
          muted:    "#8b949e",
          text:     "#e6edf3",
          accent:   "#58a6ff",
        },
        bid:   "#3fb950",  // green
        ask:   "#f85149",  // red
        trade: "#d29922",  // amber
      },
      animation: {
        "flash-green": "flashGreen 200ms ease-out",
        "flash-red":   "flashRed   200ms ease-out",
        "pulse-slow":  "pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite",
      },
      keyframes: {
        flashGreen: {
          "0%":   { backgroundColor: "#3fb95033" },
          "100%": { backgroundColor: "transparent" },
        },
        flashRed: {
          "0%":   { backgroundColor: "#f8514933" },
          "100%": { backgroundColor: "transparent" },
        },
      },
    },
  },
  plugins: [],
};
export default config;
