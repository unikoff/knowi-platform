-- MANUAL ARTIFACT ONLY. Not an Alembic revision; runtime never runs this file.
-- Apply solely to a freshly provisioned, explicitly authorized synthetic Core DB.
-- Do not run on retained/user data. Review ownership, privileges and indexes first.
CREATE TABLE analytics_transfer_outbox (
    event_id VARCHAR(36) PRIMARY KEY,
    subject_ref VARCHAR(36) NOT NULL,
    kind VARCHAR(32) NOT NULL,
    entity_key VARCHAR(64) NOT NULL,
    revision INTEGER NOT NULL,
    payload TEXT NOT NULL,
    fingerprint VARCHAR(64) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    status VARCHAR(16) NOT NULL,
    attempts INTEGER NOT NULL,
    uncertain BOOLEAN NOT NULL,
    next_attempt_at TIMESTAMP WITH TIME ZONE NOT NULL,
    claim_token VARCHAR(36),
    lease_until TIMESTAMP WITH TIME ZONE,
    CONSTRAINT uq_analytics_transfer_revision UNIQUE(entity_key, revision)
);
CREATE INDEX ix_analytics_transfer_due ON analytics_transfer_outbox (status, next_attempt_at);
CREATE INDEX ix_analytics_transfer_subject ON analytics_transfer_outbox (subject_ref);
CREATE TABLE analytics_transfer_checkpoints (
    stream VARCHAR(80) PRIMARY KEY,
    high_watermark INTEGER NOT NULL,
    cursor INTEGER NOT NULL
);
CREATE TABLE analytics_transfer_revisions (
    subject_ref VARCHAR(36) PRIMARY KEY,
    revision INTEGER NOT NULL
);
CREATE TABLE analytics_transfer_activity_state (
    subject_ref VARCHAR(36) NOT NULL,
    activity_date DATE NOT NULL,
    visited_at TIMESTAMP WITH TIME ZONE,
    needs_core_replay BOOLEAN NOT NULL,
    PRIMARY KEY(subject_ref, activity_date)
);
CREATE INDEX ix_analytics_transfer_activity_replay ON analytics_transfer_activity_state (needs_core_replay, subject_ref, activity_date);
CREATE TABLE analytics_transfer_write_gate (
    singleton INTEGER PRIMARY KEY,
    mode VARCHAR(16) NOT NULL,
    CONSTRAINT ck_analytics_write_gate_singleton CHECK (singleton = 1),
    CONSTRAINT ck_analytics_write_gate_mode CHECK (mode IN ('mirror', 'outbox_only', 'replaying'))
);
INSERT INTO analytics_transfer_write_gate(singleton, mode) VALUES (1, 'mirror');
