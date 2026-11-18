/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // BRD §10 design tokens
        ink: { DEFAULT: '#0B1B2B', 950: '#071320', 900: '#0B1B2B', 800: '#102437', 700: '#17304A', 600: '#21405F' },
        navy: '#0E2A47',
        teal: { DEFAULT: '#00A896', text: '#2CC7B5', dim: '#0A4F4C' },
        coral: { DEFAULT: '#E0484D', text: '#FF8589', strong: '#C7353A', dim: '#4A1C22' },
        amber: { DEFAULT: '#C9820A', text: '#EBA63A', dim: '#463210' },
        orange: { DEFAULT: '#E8762B', text: '#FF9A55', dim: '#4A2A12' },
        violet: { DEFAULT: '#8E7CF0', text: '#B9ADFF' },
        line: '#234059',
        fg: { DEFAULT: '#E6EDF3', muted: '#A9B8C6', faint: '#7F93A6' },
      },
      fontFamily: {
        sans: ['"IBM Plex Sans"', 'Inter', 'system-ui', '-apple-system', '"Segoe UI"', 'Roboto', 'sans-serif'],
        mono: ['"IBM Plex Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      fontSize: { '2xs': ['0.6875rem', '1rem'] },
    },
  },
  plugins: [],
};
