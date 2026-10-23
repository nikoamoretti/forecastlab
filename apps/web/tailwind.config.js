/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: "media",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      // Each color is a CSS variable (globals.css) so dark mode swaps the whole palette;
      // "white" is the card surface, which turns dark too.
      colors: Object.fromEntries(["ink", "paper", "rule", "copper", "pine", "moss", "brick", "white"]
        .map(name => [name, `rgb(var(--${name}) / <alpha-value>)`])),
      fontFamily: {
        serif: ["Fraunces", "Iowan Old Style", "Palatino", "serif"],
        sans: ["IBM Plex Sans", "Source Sans 3", "system-ui", "sans-serif"],
        mono: ["IBM Plex Mono", "ui-monospace", "monospace"]
      }
    }
  },
  plugins: []
};
