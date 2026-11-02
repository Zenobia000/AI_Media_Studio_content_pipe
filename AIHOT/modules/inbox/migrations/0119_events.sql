CREATE TABLE events (event_id text PRIMARY KEY, title text NOT NULL, starts_at timestamptz NOT NULL, ends_at timestamptz NOT NULL, url text, keywords text[] NOT NULL DEFAULT '{}')
