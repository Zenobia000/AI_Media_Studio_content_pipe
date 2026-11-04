-- When a candidate reaching S was told to the owner (COL-FR-018): once per candidate, never again on later rounds.
ALTER TABLE candidate_state ADD COLUMN s_notified_at timestamptz
