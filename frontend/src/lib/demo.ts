// Build-time flag: false in a normal production build, so the bundler drops everything gated on it.
export const SHOW_DEMO = import.meta.env.DEV || import.meta.env.VITE_MOCK === '1';

export const DEMO_PASSWORD = SHOW_DEMO ? 'CardioSense!23' : '';
export const DEMO_ACCOUNTS = SHOW_DEMO
  ? [
      { email: 'clinician@cardiosense.dev', role: 'Clinician', use: 'Live console, alerts, insights' },
      { email: 'admin@cardiosense.dev', role: 'Org admin', use: 'Users, devices, audit' },
      { email: 'tech@cardiosense.dev', role: 'Technician', use: 'Device pairing and signal health' },
      { email: 'patient@cardiosense.dev', role: 'Patient', use: 'Limited patient-facing view' },
    ]
  : [];
