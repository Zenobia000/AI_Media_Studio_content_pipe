CREATE TABLE signal_points (identity_key text NOT NULL, metric text NOT NULL, value double precision NOT NULL, observed_at timestamptz NOT NULL, PRIMARY KEY (identity_key, metric, observed_at))
