-- cerebro-auth - OAuth 2.1 for MCP connectors (authorization code + PKCE, refresh tokens).
--
-- Claude's connectors cannot be given a header: adding the connector must open a sign-in page
-- and register the token afterwards. These tables back that flow. Nothing here replaces
-- `api_tokens`: the access token an OAuth login yields is an ordinary session token.

-- Clients registered dynamically (RFC 7591). Public clients: no secret, PKCE instead.
CREATE TABLE cerebro_auth.oauth_clients (
    client_id     TEXT PRIMARY KEY,
    client_name   TEXT,
    redirect_uris JSONB NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One-shot authorization codes (stored hashed, valid for about a minute).
CREATE TABLE cerebro_auth.oauth_codes (
    code_hash      TEXT PRIMARY KEY,
    client_id      TEXT NOT NULL REFERENCES cerebro_auth.oauth_clients(client_id) ON DELETE CASCADE,
    redirect_uri   TEXT NOT NULL,
    code_challenge TEXT NOT NULL,
    user_name      TEXT NOT NULL,
    is_master      BOOLEAN NOT NULL DEFAULT false,
    expires_at     TIMESTAMPTZ NOT NULL
);

-- Refresh tokens (stored hashed), rotated on every use.
CREATE TABLE cerebro_auth.oauth_refresh_tokens (
    token_hash TEXT PRIMARY KEY,
    client_id  TEXT NOT NULL REFERENCES cerebro_auth.oauth_clients(client_id) ON DELETE CASCADE,
    user_name  TEXT NOT NULL,
    is_master  BOOLEAN NOT NULL DEFAULT false,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX oauth_refresh_user_idx ON cerebro_auth.oauth_refresh_tokens (user_name);
