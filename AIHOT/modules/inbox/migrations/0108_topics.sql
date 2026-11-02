-- A topic opened from ticked inbox candidates (IF-002): its folder is topics/<topic_id>/ in the workspace.
CREATE TABLE topics (topic_id text PRIMARY KEY, title text NOT NULL, program text NOT NULL, tier text, created_at timestamptz NOT NULL DEFAULT now())
