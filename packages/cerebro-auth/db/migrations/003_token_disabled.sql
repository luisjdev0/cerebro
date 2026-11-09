-- cerebro-auth v3 - switch a token off without revoking it.
--
-- `revoked_at` stays what it was: permanent, practically a delete. `disabled_at` is the
-- reversible state: while it is set the token is rejected, clearing it brings the token back
-- with the same secret. Every service that reads `cerebro_auth.api_tokens` must treat a
-- disabled token like a revoked one (`AND disabled_at IS NULL`).

ALTER TABLE cerebro_auth.api_tokens ADD COLUMN disabled_at TIMESTAMPTZ;
