import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { SESSION_STORAGE_KEY, loadSession, loginWithPassword, logout as apiLogout, saveSession, setUnauthorizedHandler, type Session } from './api';

type AuthContextValue = {
  session: Session | null;
  login: (name: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  /** True when the session ended on its own (expired, revoked, or closed in another tab), not by signing out. */
  sessionEnded: boolean;
  /** True if the session may see a module (admins and unrestricted tokens see all). */
  canSee: (module: 'memory' | 'docs' | 'flows') => boolean;
  isAdmin: boolean;
};

// setTimeout overflows past ~24.8 days; a longer TTL just re-checks on the next 401.
const MAX_TIMER_MS = 2_000_000_000;

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(() => loadSession());

  const [sessionEnded, setSessionEnded] = useState(false);

  const drop = useCallback(() => {
    saveSession(null);
    setSession(null);
  }, []);
  // A session that ends by itself (timer, a 401, another tab) must say so on the login screen.
  const end = useCallback(() => {
    drop();
    setSessionEnded(true);
  }, [drop]);

  useEffect(() => {
    setUnauthorizedHandler(end);
    return () => setUnauthorizedHandler(null);
  }, [end]);

  // Keep every open tab in step: signing out (or in) in one tab is seen by the others.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key !== SESSION_STORAGE_KEY && e.key !== null) return;
      const next = loadSession();
      setSession((prev) => {
        if (!next && prev) setSessionEnded(true);
        return next;
      });
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  // Drop the session the moment it expires, not only on the next failing request.
  useEffect(() => {
    if (!session) return;
    const ms = new Date(session.expires_at).getTime() - Date.now();
    const id = window.setTimeout(end, Math.min(Math.max(ms, 0), MAX_TIMER_MS));
    return () => window.clearTimeout(id);
  }, [session, end]);

  const value = useMemo<AuthContextValue>(
    () => ({
      session,
      login: async (name, password) => {
        const s = await loginWithPassword(name, password);
        saveSession(s);
        setSessionEnded(false);
        setSession(s);
      },
      logout: async () => {
        await apiLogout();
        setSessionEnded(false);
        drop();
      },
      sessionEnded,
      canSee: (module) => !session || session.allowed_modules === null || session.allowed_modules.includes(module),
      isAdmin: session?.access_level === 'admin',
    }),
    [session, drop, sessionEnded],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}
