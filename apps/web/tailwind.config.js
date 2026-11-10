/** @type {import('tailwindcss').Config} */
const variable = name => `rgb(var(--${name}) / <alpha-value>)`;

module.exports = {
  darkMode: "media",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      // Each color is a CSS variable (globals.css) so dark mode swaps the whole palette;
      // "white" is the card surface, which turns dark too.
      colors: {
        ...Object.fromEntries(["ink", "paper", "rule", "copper", "pine", "moss", "brick", "white"].map(name => [name, variable(name)])),
        // Chart series: a series keeps its colour wherever it is drawn.
        chart: { us: variable("chart-us"), market: variable("chart-market"), claude: variable("chart-claude") }
      },
      // The font files are loaded by next/font in app/layout.tsx.
      fontFamily: {
        serif: ["var(--font-serif)", "Iowan Old Style", "Palatino", "serif"],
        sans: ["var(--font-sans)", "Source Sans 3", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"]
      }
    }
  },
  plugins: []
};
