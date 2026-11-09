import { Brain, ChevronsUpDown, FileText, KeyRound, LayoutDashboard, Moon, ScrollText, DatabaseBackup, Sun, Users, Workflow } from 'lucide-react';
import { Link, Outlet, useLocation } from 'react-router-dom';
import { useEffect, useState } from 'react';
import {
  Avatar, AvatarFallback, Button, Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupContent,
  SidebarGroupLabel, SidebarHeader, SidebarInset, SidebarMenu, SidebarMenuButton, SidebarMenuItem,
  SidebarProvider, SidebarRail, SidebarTrigger,
} from '@alquimias/ixchel';
import { useAuth } from '../lib/auth';
import { useTranslation } from 'react-i18next';
import { Credits } from './Credits';
import { LanguageSwitcher } from './LanguageSwitcher';

function useTheme() {
  const [dark, setDark] = useState(() => {
    try {
      const saved = localStorage.getItem('cerebro.theme');
      if (saved) return saved === 'dark';
    } catch {
      /* ignore */
    }
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false;
  });
  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark);
    try {
      localStorage.setItem('cerebro.theme', dark ? 'dark' : 'light');
    } catch {
      /* ignore */
    }
  }, [dark]);
  return { dark, toggle: () => setDark((d) => !d) };
}

export function Shell() {
  const { t } = useTranslation();
  const { session, logout, canSee, isAdmin } = useAuth();
  const { pathname } = useLocation();
  const { dark, toggle } = useTheme();

  const items = [
    { to: '/', label: t('nav.overview'), icon: LayoutDashboard, show: true },
    { to: '/memories', label: t('nav.memory'), icon: Brain, show: canSee('memory') },
    { to: '/documents', label: t('nav.documents'), icon: FileText, show: canSee('docs') },
    { to: '/workflows', label: t('nav.workflows'), icon: Workflow, show: canSee('flows') },
  ].filter((i) => i.show);
  const active = (to: string) => (to === '/' ? pathname === '/' : pathname.startsWith(to));
  const initials = (session?.name ?? '?').slice(0, 2).toUpperCase();

  return (
    <SidebarProvider persist={false} className="min-h-svh">
      <Sidebar variant="inset" collapsible="icon" className="!top-0 h-svh">
        <SidebarHeader>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton size="lg" tooltip={t('appName')} className="cursor-default hover:bg-transparent">
                <img src="/logo.svg" alt="" className="size-9 shrink-0" />
                <div className="grid flex-1 text-left leading-tight">
                  <span className="truncate font-display text-[15px] font-bold tracking-tight">{t('appName')}</span>
                  <span className="truncate text-[11px] font-semibold tracking-wide text-brand-soft uppercase">{t('portal')}</span>
                </div>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarHeader>
        <SidebarContent>
          <SidebarGroup>
            <SidebarGroupLabel>{t('nav.modules')}</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {items.map((i) => (
                  <SidebarMenuItem key={i.to}>
                    <SidebarMenuButton icon={i.icon} tooltip={i.label} isActive={active(i.to)} render={<Link to={i.to} />}>
                      {i.label}
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
          {isAdmin && (
            <SidebarGroup>
              <SidebarGroupLabel>{t('nav.administration')}</SidebarGroupLabel>
              <SidebarGroupContent>
                <SidebarMenu>
                  <SidebarMenuItem>
                    <SidebarMenuButton icon={Users} tooltip={t('nav.admin')} isActive={pathname === '/admin'} render={<Link to="/admin" />}>
                      {t('nav.admin')}
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                  <SidebarMenuItem>
                    <SidebarMenuButton icon={KeyRound} tooltip={t('nav.tokens')} isActive={active('/admin/tokens')} render={<Link to="/admin/tokens" />}>
                      {t('nav.tokens')}
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                  <SidebarMenuItem>
                    <SidebarMenuButton icon={ScrollText} tooltip={t('nav.logs')} isActive={active('/admin/logs')} render={<Link to="/admin/logs" />}>
                      {t('nav.logs')}
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                  <SidebarMenuItem>
                    <SidebarMenuButton icon={DatabaseBackup} tooltip={t('nav.backup')} isActive={active('/admin/backup')} render={<Link to="/admin/backup" />}>
                      {t('nav.backup')}
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                </SidebarMenu>
              </SidebarGroupContent>
            </SidebarGroup>
          )}
        </SidebarContent>
        <SidebarFooter>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton size="lg" tooltip={t('shell.signOut')} onClick={() => void logout()}>
                <Avatar className="h-8 w-8 rounded-lg">
                  <AvatarFallback className="rounded-lg">{initials}</AvatarFallback>
                </Avatar>
                <div className="grid flex-1 text-left text-sm leading-tight">
                  <span className="truncate font-semibold">{session?.name}</span>
                  <span className="truncate text-xs">{session?.access_level} · {t('shell.signOutLine')}</span>
                </div>
                <ChevronsUpDown className="ml-auto size-4" />
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarFooter>
        <SidebarRail />
      </Sidebar>
      <SidebarInset>
        <div className="flex items-center gap-2 p-4 pb-0">
          <SidebarTrigger />
          <div className="ml-auto flex items-center gap-2">
            <LanguageSwitcher />
            <Button variant="ghost" size="sm" onClick={toggle} aria-label={t('shell.toggleTheme')}>
              {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
            </Button>
          </div>
        </div>
        <div className="flex min-h-[calc(100svh-4.5rem)] flex-col">
          <main className="flex-1 space-y-6 p-4 md:p-6">
            <Outlet />
          </main>
          <Credits className="px-4 pb-4" />
        </div>
      </SidebarInset>
    </SidebarProvider>
  );
}
