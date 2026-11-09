import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { MODULES, RESTRICTION_KEY, type ModuleId, type ModuleScopes } from '../lib/admin';
import { listCategories as listDocCategories } from '../lib/documents';
import { listFlowCategories } from '../lib/flows';
import { listContexts } from '../lib/memories';

export type ScopeValue = { modules: ModuleId[]; restrictions: Partial<Record<ModuleId, string[] | null>> };

export const emptyScope = (): ScopeValue => ({ modules: [], restrictions: {} });

export function scopeFrom(allowed: ModuleId[] | null | undefined, scopes: ModuleScopes | undefined): ScopeValue {
  const restrictions: ScopeValue['restrictions'] = {};
  for (const m of MODULES) {
    const list = scopes?.[m]?.[RESTRICTION_KEY[m]];
    if (Array.isArray(list)) restrictions[m] = list;
  }
  return { modules: allowed ?? [], restrictions };
}

/** The `module_scopes` payload: only modules that are enabled AND restricted to a list. */
export function scopesPayload(v: ScopeValue): ModuleScopes {
  const out: ModuleScopes = {};
  for (const m of v.modules) {
    const list = v.restrictions[m];
    if (list) out[m] = { [RESTRICTION_KEY[m]]: list };
  }
  return out;
}

type Option = { slug: string; name: string };

function useOptions(): Record<ModuleId, Option[] | null> {
  const [opts, setOpts] = useState<Record<ModuleId, Option[] | null>>({ memory: null, docs: null, flows: null });
  useEffect(() => {
    const set = (m: ModuleId) => (o: Option[]) => setOpts((p) => ({ ...p, [m]: o }));
    listContexts().then(set('memory'), () => set('memory')([]));
    listDocCategories().then(set('docs'), () => set('docs')([]));
    listFlowCategories().then(set('flows'), () => set('flows')([]));
  }, []);
  return opts;
}

/**
 * Which modules are enabled and, for each, whether everything in it is reachable or only some
 * contexts/categories. Used for a group's scopes and when creating a token.
 */
export function ScopeEditor({ value, onChange, disabled }: { value: ScopeValue; onChange: (v: ScopeValue) => void; disabled?: boolean }) {
  const { t } = useTranslation();
  const options = useOptions();
  const toggleModule = (m: ModuleId) => {
    const on = value.modules.includes(m);
    onChange({ ...value, modules: on ? value.modules.filter((x) => x !== m) : [...value.modules, m] });
  };
  const setRestriction = (m: ModuleId, list: string[] | null) => onChange({ ...value, restrictions: { ...value.restrictions, [m]: list } });

  return (
    <div className="space-y-3">
      {MODULES.map((m) => {
        const on = value.modules.includes(m);
        const restricted = value.restrictions[m] != null;
        const list = value.restrictions[m] ?? [];
        const opt = options[m];
        const known = new Set((opt ?? []).map((o) => o.slug));
        const extras = list.filter((s) => !known.has(s)); // already granted but not visible here (e.g. hidden categories)
        return (
          <div key={m} className="rounded-xl border p-3">
            <label className="flex items-center gap-2 text-sm font-medium">
              <input type="checkbox" checked={on} disabled={disabled} onChange={() => toggleModule(m)} />
              {t(`admin.modules.${m}`)}
            </label>
            {on && (
              <div className="mt-3 space-y-2 pl-6 text-sm">
                <label className="flex items-center gap-2">
                  <input type="radio" name={`r-${m}`} checked={!restricted} disabled={disabled} onChange={() => setRestriction(m, null)} />
                  {t(`admin.scope.all.${m}`)}
                </label>
                <label className="flex items-center gap-2">
                  <input type="radio" name={`r-${m}`} checked={restricted} disabled={disabled} onChange={() => setRestriction(m, list)} />
                  {t(`admin.scope.only.${m}`)}
                </label>
                {restricted && (
                  <div className="ml-6 space-y-1.5 rounded-lg bg-muted/50 p-2.5">
                    {opt === null && <p className="text-xs text-muted-foreground">…</p>}
                    {opt !== null && opt.length + extras.length === 0 && <p className="text-xs text-muted-foreground">{t('admin.scope.none')}</p>}
                    {[...(opt ?? []), ...extras.map((s) => ({ slug: s, name: s }))].map((o) => (
                      <label key={o.slug} className="flex items-center gap-2 text-xs">
                        <input
                          type="checkbox"
                          disabled={disabled}
                          checked={list.includes(o.slug)}
                          onChange={(e) => setRestriction(m, e.target.checked ? [...list, o.slug] : list.filter((s) => s !== o.slug))}
                        />
                        <span>{o.name}</span>
                        <span className="font-mono text-muted-foreground">{o.slug}</span>
                      </label>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** True when a module is enabled but restricted to an empty list (that would grant nothing). */
export const hasEmptyRestriction = (v: ScopeValue) => v.modules.some((m) => Array.isArray(v.restrictions[m]) && v.restrictions[m]!.length === 0);
