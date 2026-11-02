CREATE TABLE candidate_titles (candidate_id text PRIMARY KEY, title_zh text NOT NULL, set_at timestamptz NOT NULL DEFAULT now())
