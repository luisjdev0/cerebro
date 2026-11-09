-- A total order for run events. `created_at` is `now()` of the transaction, so the events written
-- together (e.g. `aborted` + `completed`) share a timestamp and cannot be ordered or paginated by it.
-- `seq` follows insertion order; it is what the portal's log view orders and pages by.

ALTER TABLE flow_run_events ADD COLUMN seq BIGSERIAL;
CREATE INDEX flow_run_events_seq_idx ON flow_run_events (seq DESC);
