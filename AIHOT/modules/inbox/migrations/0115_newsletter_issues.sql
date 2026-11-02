CREATE TABLE newsletter_issues (article_id text PRIMARY KEY REFERENCES articles (id) ON DELETE CASCADE, links integer NOT NULL)
