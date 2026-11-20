/** @type {import('tailwindcss').Config} */
// ECG-paper palette: warm paper surfaces, near-black ink, one deep crimson reserved for critical meaning.
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: '#17130F',
        paper: {
          DEFAULT: '#FBF6EE',
          raised: '#FFFDF8',
          sunk: '#F3ECE0',
          deep: '#E8DFD0',
          edge: '#D9CDBA',
        },
        accent: {
          DEFAULT: '#0F766E',
          hover: '#0B5F58',
          text: '#0B5F58',
          dim: 'rgba(15, 118, 110, 0.10)',
        },
        alert: {
          DEFAULT: '#B3122E',
          text: '#9B0F26',
          strong: '#B3122E',
          dim: 'rgba(179, 18, 46, 0.10)',
        },
        amber: {
          DEFAULT: '#C26A04',
          text: '#8A4B08',
          dim: 'rgba(194, 106, 4, 0.12)',
        },
        orange: {
          DEFAULT: '#E2570F',
          text: '#A8420A',
          dim: 'rgba(226, 87, 15, 0.12)',
        },
        cyan: {
          DEFAULT: '#0E7490',
          text: '#0E7490',
        },
        line: '#E3D9C8',
        fg: {
          DEFAULT: '#17130F',
          muted: '#4B433B',
          faint: '#6A6158',
        },
        'on-destructive': '#ffffff',
      },
      fontFamily: {
        sans: ['"Public Sans"', 'system-ui', 'sans-serif'],
        display: ['"Public Sans"', 'system-ui', 'sans-serif'],
        mono: ['"IBM Plex Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      fontSize: { '2xs': ['0.75rem', '1.05rem'] },
      borderRadius: {
        sm: '4px',
        DEFAULT: '6px',
        md: '8px',
        lg: '10px',
        xl: '14px',
      },
      boxShadow: {
        sm: '0 1px 2px rgba(23,19,15,0.08)',
        md: '0 6px 16px rgba(23,19,15,0.12)',
      },
    },
  },
  plugins: [],
};
