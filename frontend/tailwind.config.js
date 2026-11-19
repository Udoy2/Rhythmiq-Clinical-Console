/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: {
          DEFAULT: '#10243E',
          950: '#F5F7FA',
          900: '#FFFFFF',
          800: '#eef4f9',
          700: '#e2ebf3',
          600: '#DCE5EE',
        },
        navy: '#0B1F3A',
        teal: {
          DEFAULT: '#00B86B',
          text: '#047857',
          dim: 'rgba(0, 184, 107, 0.12)',
        },
        coral: {
          DEFAULT: '#FF5F57',
          text: '#dc2626',
          strong: '#FF5F57',
          dim: 'rgba(255, 95, 87, 0.12)',
        },
        amber: {
          DEFAULT: '#d97706',
          text: '#b45309',
          dim: 'rgba(217, 119, 6, 0.12)',
        },
        orange: {
          DEFAULT: '#ea580c',
          text: '#c2410c',
          dim: 'rgba(234, 88, 12, 0.12)',
        },
        violet: {
          DEFAULT: '#22D3EE',
          text: '#0891b2',
        },
        line: '#DCE5EE',
        fg: {
          DEFAULT: '#0B1F3A',
          muted: '#3D4F63',
          faint: '#5B6B7C',
        },
        'on-destructive': '#ffffff',
        accent: {
          DEFAULT: '#00B86B',
          hover: '#00965a',
          text: '#047857',
        },
      },
      fontFamily: {
        sans: ['"DM Sans"', 'system-ui', 'sans-serif'],
        display: ['"Manrope"', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      fontSize: { '2xs': ['0.6875rem', '1rem'] },
      borderRadius: {
        sm: '12px',
        DEFAULT: '16px',
        md: '20px',
        lg: '22px',
        xl: '28px',
      },
      boxShadow: {
        sm: '0 8px 24px rgba(11,31,58,0.08)',
        md: '0 18px 50px rgba(11,31,58,0.10)',
      },
    },
  },
  plugins: [],
};
