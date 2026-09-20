-- +goose Up
CREATE TABLE events (
	device_assignment_id TEXT NOT NULL,
	sequence_group        INTEGER NOT NULL,
	sequence_number       INTEGER NOT NULL,
	event_code            INTEGER NOT NULL,
	event_name            TEXT NOT NULL,
	pump_date_time        TEXT NOT NULL,
	estimated_date_time   TEXT NOT NULL,
	event_properties      JSONB NOT NULL,
	is_clock_change       BOOLEAN NOT NULL DEFAULT FALSE,
	PRIMARY KEY (device_assignment_id, sequence_group, sequence_number, is_clock_change)
);

CREATE INDEX idx_events_code_time ON events (event_code, pump_date_time);
CREATE INDEX idx_events_time ON events (pump_date_time);
CREATE INDEX idx_events_properties ON events USING GIN (event_properties);

-- +goose Down
DROP TABLE events;
