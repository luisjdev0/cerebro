import { Navigate, Route, Routes } from 'react-router-dom';
import { Shell } from './components/Shell';
import { useAuth } from './lib/auth';
import { Access } from './pages/Access';
import { ApiTokens } from './pages/ApiTokens';
import { Logs } from './pages/Logs';
import { Backup } from './pages/Backup';
import { Workflows } from './pages/Workflows';
import { Dashboard } from './pages/Dashboard';
import { Login } from './pages/Login';
import { Documents } from './pages/Documents';
import { Memories } from './pages/Memories';

// Client-side routes must not collide with the gateway's API prefixes
// (/memory, /docs, /flows, /auth, /install): hence /memories, /documents, /workflows.
function RequireAuth({ admin = false, children }: { admin?: boolean; children: React.ReactNode }) {
  const { session, isAdmin } = useAuth();
  if (!session) return <Navigate to="/login" replace />;
  if (admin && !isAdmin) return <Navigate to="/" replace />;
  return <>{children}</>;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<RequireAuth><Shell /></RequireAuth>}>
        <Route index element={<Dashboard />} />
        <Route path="memories" element={<Memories />} />
        <Route path="documents" element={<Documents />} />
        <Route path="workflows" element={<Workflows />} />
        <Route path="admin" element={<RequireAuth admin><Access /></RequireAuth>} />
        <Route path="admin/backup" element={<RequireAuth admin><Backup /></RequireAuth>} />
        <Route path="admin/logs" element={<RequireAuth admin><Logs /></RequireAuth>} />
        <Route path="admin/tokens" element={<RequireAuth admin><ApiTokens /></RequireAuth>} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
