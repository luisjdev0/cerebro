import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { loadSession, loginWithPassword, logout as apiLogout, saveSession, setUnauthorizedHandler, type Session } from './api';

type AuthContextValue = {
  session: Session | null;
  login: (name: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  /** True if the session may see a module (admins and unrestricted tokens see all). */
  canSee: (module: 'memory' | 'docs' | 'flows') => boolean;
  isAdmin: boolean;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(() => loadSession());

  const drop = useCallback(() => {
    saveSession(null);
    setSession(null);
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(drop);
    return () => setUnauthorizedHandler(null);
  }, [drop]);

  // Drop the session the moment it expires, not only on the next failing request.
  useEffect(() => {
    if (!session) return;
    const ms = new Date(session.expires_at).getTime() - Date.now();
    const id = window.setTimeout(drop, Math.max(ms, 0));
    return () => window.clearTimeout(id);
  }, [session, drop]);

  const value = useMemo<AuthContextValue>(
    () => ({
      session,
      login: async (name, password) => {
        const s = await loginWithPassword(name, password);
        saveSession(s);
        setSession(s);
      },
      logout: async () => {
        await apiLogout();
        drop();
      },
      canSee: (module) => !session || session.allowed_modules === null || session.allowed_modules.includes(module),
      isAdmin: session?.access_level === 'admin',
    }),
    [session, drop],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}
