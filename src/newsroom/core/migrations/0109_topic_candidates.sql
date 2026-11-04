-- Which topic a candidate was opened into: a candidate belongs to one topic, a topic can take several candidates.
CREATE TABLE topic_candidates (candidate_id text PRIMARY KEY, topic_id text NOT NULL REFERENCES topics (topic_id) ON DELETE CASCADE)
