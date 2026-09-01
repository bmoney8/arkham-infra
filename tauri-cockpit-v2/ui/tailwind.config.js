/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        arkham: {
          bg: "#0b0e14",
          panel: "#11151f",
          edge: "#1e2530",
          accent: "#e5484d",
          text: "#e6e9ef",
          dim: "#8b93a7",
        },
      },
    },
  },
  plugins: [],
};
