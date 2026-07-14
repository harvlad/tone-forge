-- Accounts, identities, sessions, magic links, devices.
-- gen_random_uuid() is built into Postgres 13+ (no extension needed).

CREATE TABLE users (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email        text UNIQUE,           -- lowercased in app code
    display_name text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE auth_identities (
    id         bigserial PRIMARY KEY,
    user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider   text NOT NULL CHECK (provider IN ('email', 'apple')),
    subject    text NOT NULL,           -- lowercased email | apple `sub`
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (provider, subject)
);

CREATE TABLE sessions (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash   bytea NOT NULL UNIQUE,  -- sha256(opaque token)
    device_label text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL
);
CREATE INDEX sessions_user_idx ON sessions(user_id);

CREATE TABLE magic_link_tokens (
    id           bigserial PRIMARY KEY,
    email        text NOT NULL,
    token_hash   bytea NOT NULL UNIQUE,
    requester_ip inet,
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL,
    consumed_at  timestamptz
);

CREATE TABLE devices (
    device_id     text PRIMARY KEY,     -- client-generated uuid string
    user_id       uuid REFERENCES users(id) ON DELETE SET NULL,
    label         text,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    claimed_at    timestamptz
);
