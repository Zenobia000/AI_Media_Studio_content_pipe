CREATE TABLE newsletter_mentions (issue_article_id text NOT NULL REFERENCES articles (id) ON DELETE CASCADE, identity_key text NOT NULL, PRIMARY KEY (issue_article_id, identity_key))
