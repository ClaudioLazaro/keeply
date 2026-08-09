/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx}",
    "./components/**/*.{js,ts,jsx,tsx}",
    "./entities/**/*.{js,ts,jsx,tsx}",
    "./features/**/*.{js,ts,jsx,tsx}",
    "./widgets/**/*.{js,ts,jsx,tsx}",
    "./shared/**/*.{js,ts,jsx,tsx}",
    "./node_modules/@tremor/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: "class",
  theme: {
    extend: {
      zIndex: {
        60: "60",
      },
      gridTemplateColumns: {
        20: "repeat(20, minmax(0, 1fr))",
        24: "repeat(24, minmax(0, 1fr))",
      },
      minHeight: {
        "screen-minus-200": "calc(100vh - 200px)",
      },
      colors: {
        // light mode
        tremor: {
          brand: {
            faint: "rgb(255 247 237)", // orange-50
            muted: "rgb(255 237 213)", // orange-200
            subtle: "rgb(251 146 60)", // orange-400
            DEFAULT: "rgb(249 115 22)", // orange-500
            emphasis: "#374151", //  gray-700
            inverted: "#ffffff", // white
          },
          // Warm neutrals (stone), not pure white and not pure black.
          // #fff against #000 is 21:1 — the maximum a screen can produce, and
          // the classic glare pairing. This is an operations console people
          // read for a whole shift, so both ends move toward the middle while
          // staying far above AA. Measured ratios are in the comments.
          background: {
            muted: "#f5f5f4", // stone-100
            subtle: "#f5f5f4", // stone-100
            DEFAULT: "#fafaf9", // stone-50 — off-white, warm, pairs with the orange brand
            emphasis: "#292524", // stone-800
          },
          border: {
            DEFAULT: "#d6d3d1", // stone-300 — the old gray-200 barely separated surfaces
          },
          ring: {
            DEFAULT: "#d6d3d1", // stone-300
          },
          content: {
            subtle: "#57534e", // stone-600  — 7.30:1
            DEFAULT: "#44403c", // stone-700 — 9.84:1
            emphasis: "#1c1917", // stone-900 — 16.74:1
            strong: "#0c0a09", // stone-950 — 18.92:1
            inverted: "#fafaf9",
          },
        },
        // dark mode
        "dark-tremor": {
          brand: {
            faint: "#0B1229", // custom
            muted: "#172554", // blue-950
            subtle: "#1e40af", // blue-800
            DEFAULT: "#3b82f6", // blue-500
            emphasis: "#60a5fa", // blue-400
            inverted: "#030712", // gray-950
          },
          background: {
            muted: "#131A2B", // custom
            subtle: "#1f2937", // gray-800
            DEFAULT: "#111827", // gray-900
            emphasis: "#d1d5db", // gray-300
          },
          border: {
            DEFAULT: "#3f4a5f", // 1.99:1 — gray-800 was invisible against the surface
          },
          ring: {
            DEFAULT: "#3f4a5f",
          },
          // Body text was #6b7280 on #111827 — 3.67:1, below the AA minimum
          // of 4.5:1, and `subtle` was 2.35:1. Dark mode was not merely dim,
          // it was non-compliant, and squinting at low-contrast grey is the
          // fatigue this change is meant to remove.
          content: {
            subtle: "#94a3b8", // slate-400 — 6.92:1 (was 2.35:1)
            DEFAULT: "#cbd5e1", // slate-300 — 11.95:1 (was 3.67:1)
            emphasis: "#e2e8f0", // slate-200 — 14.39:1
            strong: "#f8fafc", // slate-50 — 16.96:1
            inverted: "#0c0a09",
          },
        },
      },
      boxShadow: {
        // light
        "tremor-input": "0 1px 2px 0 rgb(0 0 0 / 0.05)",
        "tremor-card":
          "0 1px 3px 0 rgb(0 0 0 / 0.1), 0 1px 2px -1px rgb(0 0 0 / 0.1)",
        "tremor-dropdown":
          "0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1)",
        // dark
        "dark-tremor-input": "0 1px 2px 0 rgb(0 0 0 / 0.05)",
        "dark-tremor-card":
          "0 1px 3px 0 rgb(0 0 0 / 0.1), 0 1px 2px -1px rgb(0 0 0 / 0.1)",
        "dark-tremor-dropdown":
          "0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1)",
      },
      borderRadius: {
        "tremor-small": "0.375rem",
        "tremor-default": "0.5rem",
        "tremor-full": "9999px",
      },
      fontSize: {
        "tremor-label": ["0.75rem"],
        "tremor-default": ["0.875rem", { lineHeight: "1.25rem" }],
        "tremor-title": ["1.125rem", { lineHeight: "1.75rem" }],
        "tremor-metric": ["1.875rem", { lineHeight: "2.25rem" }],
      },
      keyframes: {
        hide: {
          from: { opacity: "1" },
          to: { opacity: "0" },
        },
        slideDownAndFade: {
          from: { opacity: "0", transform: "translateY(-6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        slideLeftAndFade: {
          from: { opacity: "0", transform: "translateX(6px)" },
          to: { opacity: "1", transform: "translateX(0)" },
        },
        slideUpAndFade: {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        slideRightAndFade: {
          from: { opacity: "0", transform: "translateX(-6px)" },
          to: { opacity: "1", transform: "translateX(0)" },
        },
        drawerSlideLeftAndFade: {
          from: { opacity: "0", transform: "translateX(100%)" },
          to: { opacity: "1", transform: "translateX(0)" },
        },
        drawerSlideRightAndFade: {
          from: { opacity: "1", transform: "translateX(0)" },
          to: { opacity: "0", transform: "translateX(100%)" },
        },
      },
      animation: {
        "scroll-shadow-left":
          "auto linear 0s 1 normal none running scroll-shadow-left",
        "scroll-shadow-right":
          "auto linear 0s 1 normal none running scroll-shadow-right",
        // Tremor tooltip
        hide: "hide 150ms cubic-bezier(0.16, 1, 0.3, 1)",
        slideDownAndFade:
          "slideDownAndFade 150ms cubic-bezier(0.16, 1, 0.3, 1)",
        slideLeftAndFade:
          "slideLeftAndFade 150ms cubic-bezier(0.16, 1, 0.3, 1)",
        slideUpAndFade: "slideUpAndFade 150ms cubic-bezier(0.16, 1, 0.3, 1)",
        slideRightAndFade:
          "slideRightAndFade 150ms cubic-bezier(0.16, 1, 0.3, 1)",
        // Tremor Drawer
        drawerSlideLeftAndFade:
          "drawerSlideLeftAndFade 150ms cubic-bezier(0.16, 1, 0.3, 1)",
        drawerSlideRightAndFade: "drawerSlideRightAndFade 150ms ease-in",
      },
    },
  },
  safelist: [
    {
      pattern:
        /^(bg-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-(?:50|100|200|300|400|500|600|700|800|900|950))$/,
      variants: ["hover", "ui-selected"],
    },
    {
      pattern:
        /^(text-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-(?:50|100|200|300|400|500|600|700|800|900|950))$/,
      variants: ["hover", "ui-selected"],
    },
    {
      pattern:
        /^(border-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-(?:50|100|200|300|400|500|600|700|800|900|950))$/,
      variants: ["hover", "ui-selected"],
    },
    {
      pattern:
        /^(ring-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-(?:50|100|200|300|400|500|600|700|800|900|950))$/,
    },
    {
      pattern:
        /^(stroke-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-(?:50|100|200|300|400|500|600|700|800|900|950))$/,
    },
    {
      pattern:
        /^(fill-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-(?:50|100|200|300|400|500|600|700|800|900|950))$/,
    },
  ],
  plugins: [
    require("@headlessui/tailwindcss"),
    require("@tailwindcss/typography"),
  ],
};
