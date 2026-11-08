import { useState, type FormEvent } from 'react';
import { Navigate } from 'react-router-dom';
import { ArrowRight, Brain, FileText, Lock, User, Workflow } from 'lucide-react';
import { AuthLoadingState, Button, InputField, InputGroup, LoginBrandIntro, LoginScreenDivider } from '@alquimias/ixchel';
import { useTranslation } from 'react-i18next';
import { Terminal } from '../components/Terminal';
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

        <div className="flex min-h-0 flex-1 items-center justify-center overflow-y-auto p-4 md:p-8 lg:w-[58%] lg:shrink-0 lg:flex-none">
          <div className="w-full max-w-xl px-4 py-6 md:px-8">
            <div className="mx-auto w-full max-w-xl">
              {busy ? (
                <AuthLoadingState title={t('login.connecting')} description={t('login.checking')} />
              ) : (
                <>
                  <div className="brand-mono">
                  <LoginBrandIntro
                    logoSrc="/logo.svg"
                    logoAlt={t('appName')}
                    eyebrow={t('login.eyebrow')}
                    title={t('appName')}
                    description={t('login.description')}
                  />
                  </div>
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

        <div className="hidden min-h-0 flex-1 flex-col overflow-hidden lg:flex lg:w-[42%]">
          <div className="relative flex h-full min-h-0 flex-1 flex-col overflow-hidden border-l border-border bg-gradient-to-br from-card via-brand-ghost to-accent p-8 text-foreground xl:p-10 dark:border-white/10 dark:bg-card dark:from-card dark:via-card dark:to-card dark:text-white">
            <div className="pointer-events-none absolute -top-24 -right-24 size-[420px] rounded-full bg-brand-soft/30 blur-3xl dark:bg-brand-soft/10" />
            <div className="pointer-events-none absolute -bottom-32 -left-20 size-[360px] rounded-full bg-brand-peri/20 blur-3xl dark:bg-white/[0.03]" />

            <div className="relative z-10 flex min-h-0 flex-1 flex-col justify-between gap-6 overflow-y-auto">
              <div className="pt-10">
                <h2 className="font-display text-3xl leading-tight font-extrabold tracking-tight xl:text-4xl">{t('login.tagline')}</h2>
                <p className="mt-3 text-sm leading-relaxed text-muted-foreground dark:text-white/70">{t('login.taglineBody')}</p>
                <ul className="mt-6 space-y-3">
                  {[
                    { icon: Brain, title: t('nav.memory'), text: t('login.featMemory') },
                    { icon: FileText, title: t('nav.documents'), text: t('login.featDocs') },
                    { icon: Workflow, title: t('nav.workflows'), text: t('login.featFlows') },
                  ].map(({ icon: Icon, title, text }) => (
                    <li key={title} className="flex items-start gap-3 rounded-xl border bg-background/60 p-3 backdrop-blur dark:border-white/10 dark:bg-white/[0.04]">
                      <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-brand-soft/15 text-brand-soft">
                        <Icon className="size-[18px]" />
                      </span>
                      <div className="min-w-0">
                        <p className="text-sm font-semibold">{title}</p>
                        <p className="text-xs leading-relaxed text-muted-foreground dark:text-white/60">{text}</p>
                      </div>
                    </li>
                  ))}
                </ul>
              </div>

              <div className="space-y-3">
                <h3 className="text-xs font-bold tracking-wider text-brand-soft uppercase">{t('login.cliTitle')}</h3>
                <Terminal origin={origin} />
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
