/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Outfit", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
      },
      colors: {
        wave: {
          50: "#f0fdfa",
          100: "#ccfbf1",
          400: "#2dd4bf",
          500: "#14b8a6",
          600: "#0d9488",
          900: "#134e4a",
        },
      },
      boxShadow: {
        glow: "0 0 60px -12px rgba(20, 184, 166, 0.35)",
      },
      animation: {
        pulsebar: "pulsebar 1.5s ease-in-out infinite",
        fadein: "fadein 0.4s ease-out forwards",
      },
      keyframes: {
        pulsebar: {
          "0%, 100%": { opacity: "0.45" },
          "50%": { opacity: "1" },
        },
        fadein: {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
    },
  },
  plugins: [],
};
