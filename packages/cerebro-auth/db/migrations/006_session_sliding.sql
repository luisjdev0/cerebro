-- cerebro-auth - sliding sessions.
--
-- A session minted with "remember" (the CLI, and so the stdio MCP that reuses its saved login)
-- keeps `slide_days`: every service that validates the token pushes `expires_at` forward to
-- now() + slide_days when it is used, so it only ends after that many days without use.
-- NULL (every existing token) = fixed expiry, as before.
ALTER TABLE cerebro_auth.api_tokens
    ADD COLUMN slide_days INTEGER CHECK (slide_days IS NULL OR slide_days > 0);
