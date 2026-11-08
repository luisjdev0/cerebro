# cerebro-auth (`auth_*`)

Unlike memory/docs/flows, `cerebro-auth` is not a source of knowledge about
the user — it manages **access to the ecosystem itself**: who exists
(`users`), how they are grouped (`groups`), what each group can see
(`group_scopes`) and which credentials exist (`api_tokens`). Use these tools
**only when the user explicitly asks** to create/manage a user, a group or a
token — never as part of the normal conversation-start protocol (rule zero in
`SKILL.md`), and never on your own initiative: this is a security surface, not
a context-retrieval one.

All of them require the token you are calling with to be `access_level:
admin` — if it is not, the tool returns `error` and there is nothing to retry
with different arguments; it is a server-side rejection, not a problem with
the call.

Passwords are never handled through these tools: a secret must not pass
through a model. Setting or changing a password is done by the user with the
CLI (`cerebro user create --password`, `cerebro user set-password`).

## Tools

| Tool | Use it to |
|---|---|
| `auth_create_user` | Create a new identity (person or service). It grants no access by itself — that requires adding it to a group. |
| `auth_list_users` | See existing users and their `access_level`, before `auth_add_group_member` or `auth_create_token(user=...)`. |
| `auth_create_group` | Create a new group — it is born without permissions, it is just a named bucket. |
| `auth_set_group_scopes` | Define which modules/contexts a group can see. REPLACES the group's current scopes, it is not additive. |
| `auth_add_group_member` | Add an existing user to a group, so they inherit its scopes. |
| `auth_create_token` | Issue a real credential. See "Two independent axes" below — it is the most delicate of the eight tools. |
| `auth_list_tokens` | Audit which tokens exist (metadata, never the secret value) before revoking something obsolete. |
| `auth_revoke_token` | Revoke a token — immediate and irreversible, whoever was using it loses access right away. |

## Two independent axes in `auth_create_token` / `auth_set_group_scopes`

1. **Level** (`access_level` vs. `user`, only in `auth_create_token`) — exactly
   one of the two, never both: pass `access_level` directly for a service
   token with no associated user, or pass `user` so the token inherits that
   user's level. Passing both at once is an error.
2. **Scope** (`allowed_modules`/`module_scopes`, in both tools) — two layers:
   `allowed_modules` is the coarse lock (which modules it touches at all);
   `module_scopes` is the fine lock inside each allowed module (e.g.
   `{"memory": {"contexts": ["project-x"]}}`). A module outside
   `allowed_modules` is unreachable no matter what `module_scopes` says.

For a token tied to an `owner` user, `allowed_modules`/`module_scopes` can be
omitted entirely to fully inherit what their groups already grant — this is
the common "this token simply IS that user" case. If they are passed
explicitly in that case, they can only **narrow** what is inherited, never
widen it — the server rejects an attempt to widen instead of silently
trimming it.

## The token value is shown only ONCE

`auth_create_token` returns the plain-text secret only in that response.
Nobody — not even an admin — can retrieve it again later. If the token is for
another person or service, hand it over as part of finishing the task; do not
assume you or anyone else will be able to look it up later. If it is lost,
the only way out is `auth_revoke_token` + creating a new one.

## Errors and honesty

Same criterion as the rest of cerebro (see `SKILL.md`): an `error` is
reported as is, with no blind retries — especially a rejection for lacking
`access_level: admin`, which is not fixed by changing arguments. Before
revoking a token, confirm with the user if there is any doubt that it might
break something in use, unless they already asked specifically to revoke that
token.
