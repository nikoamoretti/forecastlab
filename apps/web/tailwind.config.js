/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#1c1916",
        paper: "#f3efe6",
        rule: "#d7cbb8",
        copper: "#9a4b24",
        pine: "#215c4b",
        moss: "#3f6f5c"
      },
      fontFamily: {
        serif: ["Fraunces", "Iowan Old Style", "Palatino", "serif"],
        sans: ["IBM Plex Sans", "Source Sans 3", "system-ui", "sans-serif"],
        mono: ["IBM Plex Mono", "ui-monospace", "monospace"]
      }
    }
  },
  plugins: []
};
