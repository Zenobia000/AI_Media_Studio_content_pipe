-- The sources (src_ + the first 12 hex of the SHA-1 of the normalized address) behind a topic.
CREATE TABLE topic_sources (topic_id text NOT NULL REFERENCES topics (topic_id) ON DELETE CASCADE, source_id text NOT NULL, PRIMARY KEY (topic_id, source_id))
