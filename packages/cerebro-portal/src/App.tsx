import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { Shell } from './components/Shell';
import { useAuth } from './lib/auth';
import { ComingSoon } from './pages/ComingSoon';
import { Dashboard } from './pages/Dashboard';
import { Login } from './pages/Login';
import { useTranslation } from 'react-i18next';

// Client-side routes must not collide with the gateway's API prefixes
// (/memory, /docs, /flows, /auth, /install): hence /memories, /documents, /workflows.
function RequireAuth({ admin = false, children }: { admin?: boolean; children: React.ReactNode }) {
  const { session, isAdmin } = useAuth();
  const location = useLocation();
  if (!session) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  if (admin && !isAdmin) return <Navigate to="/" replace />;
  return <>{children}</>;
}

export function App() {
  const { t } = useTranslation();
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<RequireAuth><Shell /></RequireAuth>}>
        <Route index element={<Dashboard />} />
        <Route path="memories/*" element={<ComingSoon title={t('nav.memory')} />} />
        <Route path="documents/*" element={<ComingSoon title={t('nav.documents')} />} />
        <Route path="workflows/*" element={<ComingSoon title={t('nav.workflows')} />} />
        <Route path="admin/*" element={<RequireAuth admin><ComingSoon title={t('nav.admin')} /></RequireAuth>} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
