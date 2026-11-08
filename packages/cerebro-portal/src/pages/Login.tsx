import { useState, type FormEvent } from 'react';
import { Navigate } from 'react-router-dom';
import { ArrowRight, Lock, User } from 'lucide-react';
import { AuthLoadingState, Button, InputField, InputGroup, LoginBrandIntro, LoginScreenDivider } from '@alquimias/ixchel';
import { useTranslation } from 'react-i18next';
import { CodeBlock } from '../components/CodeBlock';
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

  // The gateway serves the portal and the installers from the same origin, so the
  // address the browser used is the one the CLI must log in to.
  const origin = window.location.origin;

  return (
    <div className="flex h-svh w-full flex-col overflow-hidden bg-muted dark:bg-background">
      <div className="relative flex min-h-0 flex-1 flex-col overflow-x-hidden overflow-y-auto lg:flex-row lg:overflow-hidden">
        <LanguageSwitcher className="absolute top-4 right-4 z-20 md:top-6 md:right-6" />
        <LoginScreenDivider />

        <div className="flex min-h-0 flex-1 items-center justify-center overflow-y-auto p-4 md:p-8 lg:w-[44%] lg:shrink-0 lg:flex-none">
          <div className="w-full max-w-lg px-4 py-6 md:px-8">
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
                        placeholder={t('login.passwordPlaceholder')}
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
        </div>

        <div className="hidden min-h-0 flex-1 flex-col overflow-hidden lg:flex lg:w-[56%]">
          <div className="relative flex h-full min-h-0 flex-1 flex-col overflow-hidden border border-border bg-gradient-to-br from-card via-brand-ghost to-accent p-10 text-foreground shadow-xl lg:border-l-0 dark:border-transparent dark:bg-card dark:from-card dark:via-card dark:to-card dark:text-white dark:shadow-2xl">
            <div className="pointer-events-none absolute -top-10 -right-10 size-[500px] bg-gradient-to-br from-brand-soft/35 via-brand-peri/15 to-transparent opacity-70 dark:hidden" />
            <div className="pointer-events-none absolute top-0 right-0 size-80 rounded-full bg-brand-soft/15 blur-3xl dark:bg-white/[0.04]" />

            <div className="relative z-10 flex min-h-0 flex-1 flex-col justify-between gap-8 overflow-y-auto">
              <div className="max-w-xl pt-12">
                <h2 className="font-display text-3xl leading-tight font-extrabold tracking-tight">{t('login.tagline')}</h2>
                <p className="mt-3 text-sm leading-relaxed text-muted-foreground dark:text-white/70">{t('login.taglineBody')}</p>
              </div>

              <div className="max-w-xl space-y-3">
                <h3 className="text-xs font-bold tracking-wider text-brand-soft uppercase">{t('login.cliTitle')}</h3>
                <div className="space-y-1">
                  <p className="text-xs text-muted-foreground">{t('login.cliLinux')}</p>
                  <CodeBlock code={`curl -fsSL ${origin}/install/install.sh | sh`} />
                </div>
                <div className="space-y-1">
                  <p className="text-xs text-muted-foreground">{t('login.cliWindows')}</p>
                  <CodeBlock code={`irm ${origin}/install/install.ps1 | iex`} />
                </div>
                <div className="space-y-1">
                  <p className="text-xs text-muted-foreground">{t('login.cliLogin')}</p>
                  <CodeBlock code={`cerebro login --url ${origin}`} />
                </div>
              </div>

              <footer className="flex flex-wrap items-center gap-x-2 gap-y-1 border-t pt-4 text-xs text-muted-foreground dark:border-white/10">
                <span>
                  {t('login.createdBy')}{' '}
                  <a href="https://github.com/luisjdev0" target="_blank" rel="noreferrer" className="font-semibold text-foreground hover:underline dark:text-white">
                    luisjdev
                  </a>
                </span>
                <span aria-hidden>·</span>
                <a href="https://github.com/luisjdev0/cerebro/blob/main/LICENSE" target="_blank" rel="noreferrer" className="hover:underline">
                  {t('login.license')}
                </a>
                <span aria-hidden>·</span>
                <a href="https://github.com/luisjdev0/cerebro" target="_blank" rel="noreferrer" className="hover:underline">
                  GitHub
                </a>
              </footer>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
