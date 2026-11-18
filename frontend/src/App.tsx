import { lazy, Suspense } from 'react';
import { Navigate, Outlet, Route, Routes, useLocation } from 'react-router-dom';
import { AppShell } from './layout/AppShell';
import { LoadingState } from './components/ui';
import { canSeeAlerts, canManageDevices, isAdmin, useAuth } from './state/auth';
import { SignInPage } from './pages/SignIn';
import { PatientsPage } from './pages/Patients';
import { PatientLayout } from './pages/PatientLayout';
import { LiveConsolePage } from './pages/LiveConsole';

const HistoryPage = lazy(() => import('./pages/History'));
const SessionPage = lazy(() => import('./pages/SessionDetail'));
const InsightsPage = lazy(() => import('./pages/Insights'));
const ReportsPage = lazy(() => import('./pages/Reports'));
const AlertsPage = lazy(() => import('./pages/Alerts'));
const AlertDetailPage = lazy(() => import('./pages/AlertDetail'));
const AdminPage = lazy(() => import('./pages/Admin'));
const AboutPage = lazy(() => import('./pages/About'));

function RequireAuth() {
  const token = useAuth((s) => s.accessToken);
  const explicit = useAuth((s) => s.explicitSignOut);
  const loc = useLocation();
  if (!token) return <Navigate to="/login" replace state={explicit ? null : { from: loc.pathname + loc.search }} />;
  return (
    <AppShell>
      <Suspense fallback={<LoadingState />}>
        <Outlet />
      </Suspense>
    </AppShell>
  );
}

function RequireRole({ allow }: { allow: (r: Parameters<typeof isAdmin>[0]) => boolean }) {
  const role = useAuth((s) => s.user?.role);
  if (!allow(role)) {
    return (
      <div role="alert" className="panel mx-auto mt-10 max-w-md p-6 text-center">
        <h1>Not available for your role</h1>
        <p className="mt-2 text-sm text-fg-muted">Your account does not have access to this page.</p>
      </div>
    );
  }
  return <Outlet />;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<SignInPage />} />
      <Route element={<RequireAuth />}>
        <Route index element={<Navigate to="/patients" replace />} />
        <Route path="/patients" element={<PatientsPage />} />
        <Route path="/patients/:id" element={<PatientLayout />}>
          <Route index element={<Navigate to="live" replace />} />
          <Route path="live" element={<LiveConsolePage />} />
          <Route path="history" element={<HistoryPage />} />
          <Route path="sessions/:sessionId" element={<SessionPage />} />
          <Route path="insights" element={<InsightsPage />} />
          <Route path="reports" element={<ReportsPage />} />
        </Route>
        <Route element={<RequireRole allow={canSeeAlerts} />}>
          <Route path="/alerts" element={<AlertsPage />} />
          <Route path="/alerts/:alertId" element={<AlertDetailPage />} />
        </Route>
        <Route element={<RequireRole allow={(r) => isAdmin(r) || canManageDevices(r)} />}>
          <Route path="/admin" element={<AdminPage />} />
          <Route path="/admin/:tab" element={<AdminPage />} />
        </Route>
        <Route path="/about" element={<AboutPage />} />
        <Route path="*" element={
          <div className="panel mx-auto mt-10 max-w-md p-6 text-center">
            <h1>Page not found</h1>
            <p className="mt-2 text-sm text-fg-muted">The address does not match any screen.</p>
          </div>
        } />
      </Route>
    </Routes>
  );
}
