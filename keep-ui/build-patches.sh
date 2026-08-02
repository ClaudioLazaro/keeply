#!/bin/sh
# Patches applied at docker build time so the keep-ui works inside
# the keeply cluster:
#   - no remote auth (NO_AUTH mode)
#   - no external API key (server-side proxy uses in-cluster secret)
#   - aiops proxy must bypass the auth middleware
set -e

# 1. Drop the `output: "standalone"` from next.config.js — the build
#    runs `next start` which refuses to start against a standalone
#    build output.
sed -i '/^  output: "standalone",$/d' next.config.js

# 2. Add /api/aiops to the middleware skip list. The middleware regex
#    has a `|`-separated negative lookahead like `api/aws-marketplace$|api/auth`
#    and the function body has a `!pathname.startsWith("/api/healthcheck")`
#    check. Append `api/aiops` to both.
#    The `&&` MUST be escaped as `\&\&` — an unescaped `&` in a sed
#    replacement means "the whole match", so `&&` expands to the matched
#    text twice and corrupts the file into a syntax error.
sed -i 's#api/auth#api/auth|api/aiops#g' middleware.ts
sed -i 's#!pathname.startsWith("/api/healthcheck")#!pathname.startsWith("/api/healthcheck") \&\& !pathname.startsWith("/api/aiops")#' middleware.ts

# NOTE: there used to be a step 3 here that short-circuited
# `isAuthenticated` in NO_AUTH mode so the middleware would not redirect
# to /signin. It was removed — /signin is where the NoAuth Credentials
# provider (auth.config.ts:185) mints the session and its accessToken.
# Skipping it left the app with no session at all: every client-side call
# to /backend/* went out unauthenticated, keep-backend returned 401, and
# ApiClient.handleResponse (shared/api/ApiClient.ts:85) called next-auth's
# signOutClient(), whose browser client falls back to a hardcoded
# `http://localhost:3000/api/auth` — bouncing the user to localhost.
# Let the real signin flow run; SignInForm auto-submits the provider.
