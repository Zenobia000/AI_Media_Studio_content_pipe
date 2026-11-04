-- Whether the Chinese-speaking side has covered a candidate yet (COL-FR-016): covered, gap (nobody has), or unchecked with why.
CREATE TABLE local_heat (candidate_id text PRIMARY KEY, status text NOT NULL CHECK (status IN ('covered', 'gap', 'unchecked')), matches integer, reason text, checked_at timestamptz NOT NULL DEFAULT now())
