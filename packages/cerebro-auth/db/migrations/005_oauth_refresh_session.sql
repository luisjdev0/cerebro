-- cerebro-auth - tie each OAuth refresh token to the session token issued with it.
--
-- Revoking (or switching off) the access token of a connector in the portal must end the
-- connection, not merely until its next refresh. A refresh is refused when the session token
-- it was issued with has been revoked or disabled; plain expiry (12 h) still refreshes.
ALTER TABLE cerebro_auth.oauth_refresh_tokens ADD COLUMN session_token_hash TEXT;
