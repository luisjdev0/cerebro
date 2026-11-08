import { useState, type FormEvent } from 'react';
import { Navigate } from 'react-router-dom';
import { ArrowRight, Lock, User } from 'lucide-react';
import { AuthLoadingState, Button, InputField, InputGroup, LoginBrandIntro } from '@alquimias/ixchel';
import { useTranslation } from 'react-i18next';
import { LanguageSwitcher } from '../components/LanguageSwitcher';
import { useAuth } from '../lib/auth';
import { ApiError } from '../lib/api';

// Own form instead of Ixchel's LoginScreen: that one ships fixed Spanish copy and takes
// no label props, which cannot work with a runtime language switch.
export function Login() {
  const { t } = useTranslation();
  const { session, login } = useAuth();
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [nameError, setNameError] = useState<string>();
  const [passwordError, setPasswordError] = useState<string>();
  const [busy, setBusy] = useState(false);
  if (session) return <Navigate to="/" replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return setNameError(t('login.enterUsername'));
    if (!password) return setPasswordError(t('login.enterPassword'));
    setBusy(true);
    try {
      await login(name.trim(), password);
    } catch (err) {
      // Generic message on purpose: the API answers 401 for every bad-credential case.
      if (err instanceof ApiError && err.status === 429) setPasswordError(t('login.tooMany'));
      else if (err instanceof ApiError && err.status === 0) setPasswordError(err.message);
      else setPasswordError(t('login.invalid'));
      setBusy(false);
    }
  };

  return (
    <div className="relative flex min-h-svh w-full items-center justify-center bg-muted p-6 dark:bg-background">
      <LanguageSwitcher className="absolute top-4 right-4 z-20 md:top-6 md:right-6" />
      <div className="mx-auto w-full max-w-lg">
        {busy ? (
          <AuthLoadingState title={t('login.connecting')} description={t('login.checking')} />
        ) : (
          <>
            <LoginBrandIntro
              logoSrc="/logo.svg"
              logoAlt={t('appName')}
              eyebrow={t('login.eyebrow')}
              title={t('appName')}
              description={t('login.description')}
            />
            <form onSubmit={(e) => void submit(e)} className="login-form space-y-4">
              <InputGroup className="gap-4">
                <InputField
                  id="login-username"
                  label={t('login.username')}
                  icon={User}
                  type="text"
                  borderless
                  value={name}
                  onChange={(v) => {
                    setName(v);
                    if (nameError) setNameError(undefined);
                  }}
                  error={nameError}
                  placeholder={t('login.usernamePlaceholder')}
                  autoComplete="username"
                />
                <InputField
                  id="login-password"
                  label={t('login.password')}
                  icon={Lock}
                  type="password"
                  borderless
                  value={password}
                  onChange={(v) => {
                    setPassword(v);
                    if (passwordError) setPasswordError(undefined);
                  }}
                  error={passwordError}
                  autoComplete="current-password"
                />
              </InputGroup>
              <Button type="submit" variant="primary" trailingIcon={ArrowRight} className="w-full">
                {t('login.submit')}
              </Button>
            </form>
          </>
        )}
      </div>
    </div>
  );
}
