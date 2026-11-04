-- Candidates a person added by hand (add-source): the article, their note, the program and formats they asked for.
CREATE TABLE manual_candidates (article_id text PRIMARY KEY REFERENCES articles (id) ON DELETE CASCADE, notes text NOT NULL DEFAULT '', program text, formats text[] NOT NULL DEFAULT '{}', added_at timestamptz NOT NULL DEFAULT now())
