"""User accounts: magic-link email + Sign in with Apple + device claim.

Optional sign-in layered on top of the anonymous flow. Anonymous use
keeps working everywhere; signing in "claims" the device's analyses so
a user can pick up their work on another device.

Design notes:
- Sessions are opaque bearer tokens (SHA-256 hash stored server-side),
  not JWTs — instant revocation, no signing-key management.
- Postgres via asyncpg when DATABASE_URL is set; otherwise an
  in-memory store so dev and tests run with zero infra (same
  optional-config pattern as R2).
"""
