CREATE TABLE newsletter_issues (article_id text PRIMARY KEY REFERENCES articles (id) ON DELETE CASCADE, processed_at timestamptz NOT NULL DEFAULT now(), links integer NOT NULL)
