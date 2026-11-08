import { Navigate, useLocation } from 'react-router-dom';
import { LoginScreen } from '@alquimias/ixchel';
import { useAuth } from '../lib/auth';
import { ApiError } from '../lib/api';

export function Login() {
  const { session, login } = useAuth();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? '/';
  if (session) return <Navigate to={from} replace />;

  return (
    <LoginScreen
      identifier="username"
      showPassword
      brand={{
        logoSrc: '/logo.svg',
        logoAlt: 'cerebro',
        eyebrow: 'Memory · Documents · Flows',
        title: 'cerebro',
        description: 'Persistent memory, complete documents and workflows for your agents, on your own instance.',
      }}
      onSubmit={async ({ email: name, password }) => {
        try {
          await login(name, password);
        } catch (err) {
          // Generic message on purpose: the API answers 401 for every bad-credential case.
          if (err instanceof ApiError && err.status === 429) throw new Error('Too many attempts. Try again in a few minutes.');
          if (err instanceof ApiError && err.status === 0) throw new Error(err.message);
          throw new Error('Invalid username or password');
        }
      }}
    />
  );
}
