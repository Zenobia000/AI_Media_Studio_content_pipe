-- The job queue that replaces pg-boss: one table of jobs and one of schedule state. Behaviour follows pg-boss 10 as the engine used it:
-- the "short" policy keeps one queued job per queue and singleton key; the "singleton" policy one queued and one active; a failed
-- attempt waits retry_delay seconds (doubling with backoff) in state 'retry' until the retries are used up.
CREATE TABLE jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  policy text NOT NULL DEFAULT 'short' CHECK (policy IN ('short', 'singleton', 'standard')),
  data jsonb NOT NULL DEFAULT '{}'::jsonb,
  state text NOT NULL DEFAULT 'created' CHECK (state IN ('created', 'retry', 'active', 'completed', 'failed', 'cancelled')),
  priority integer NOT NULL DEFAULT 0,
  singleton_key text,
  start_after timestamptz NOT NULL DEFAULT now(),
  created_on timestamptz NOT NULL DEFAULT now(),
  started_on timestamptz,
  completed_on timestamptz,
  retry_count integer NOT NULL DEFAULT 0,
  retry_limit integer NOT NULL DEFAULT 0,
  retry_delay integer NOT NULL DEFAULT 0,
  retry_backoff boolean NOT NULL DEFAULT false,
  expire_seconds integer NOT NULL DEFAULT 900,
  output jsonb
);
CREATE INDEX jobs_fetch_idx ON jobs (name, priority DESC, created_on, id) WHERE state IN ('created', 'retry');
CREATE INDEX jobs_active_idx ON jobs (started_on) WHERE state = 'active';
CREATE INDEX jobs_done_idx ON jobs (completed_on) WHERE state IN ('completed', 'failed', 'cancelled');
CREATE INDEX jobs_data_article_idx ON jobs ((data->>'articleId')) WHERE data ? 'articleId';
CREATE UNIQUE INDEX jobs_short_uniq ON jobs (name, coalesce(singleton_key, '')) WHERE state = 'created' AND policy = 'short';
CREATE UNIQUE INDEX jobs_singleton_uniq ON jobs (name, state, coalesce(singleton_key, '')) WHERE state IN ('created', 'active') AND policy = 'singleton';

CREATE TABLE job_schedules (
  name text PRIMARY KEY,
  cron text NOT NULL,
  timezone text NOT NULL DEFAULT 'Asia/Shanghai',
  missed text NOT NULL DEFAULT 'skip' CHECK (missed IN ('skip', 'once')),
  last_slot timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now()
);
