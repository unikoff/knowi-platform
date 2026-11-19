-- MANUAL a009 ARTIFACT ONLY. Apply solely to a fresh authorized synthetic Core DB.
-- Apply after 001 in the same synthetic DB; no runtime startup applies this file.
-- Widen values already stored by 001 without changing the active c528 schema.
ALTER TABLE analytics_transfer_outbox ALTER COLUMN revision TYPE BIGINT;
ALTER TABLE analytics_transfer_revisions ALTER COLUMN revision TYPE BIGINT;
ALTER TABLE analytics_transfer_checkpoints ALTER COLUMN high_watermark TYPE BIGINT;
ALTER TABLE analytics_transfer_checkpoints ALTER COLUMN cursor TYPE BIGINT;
CREATE TABLE analytics_transfer_snapshot_proofs (
    proof_id VARCHAR(36) PRIMARY KEY,
    subject_ref VARCHAR(36) NOT NULL,
    cut_revision BIGINT NOT NULL,
    payload TEXT NOT NULL,
    fingerprint VARCHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL,
    accepted BOOLEAN NOT NULL,
    uncertain BOOLEAN NOT NULL,
    attempts INTEGER NOT NULL,
    next_attempt_at TIMESTAMP WITH TIME ZONE NOT NULL,
    claim_token VARCHAR(36),
    lease_until TIMESTAMP WITH TIME ZONE,
    CONSTRAINT uq_analytics_proof_cut UNIQUE(subject_ref, cut_revision)
);
CREATE INDEX ix_analytics_proof_due ON analytics_transfer_snapshot_proofs(status, next_attempt_at);
CREATE TABLE analytics_transfer_snapshot_barriers (
    stream VARCHAR(16) PRIMARY KEY,
    high_watermark BIGINT NOT NULL,
    sealed_at TIMESTAMP WITH TIME ZONE NOT NULL
);
CREATE TABLE analytics_transfer_activity_preparation (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    high_watermark BIGINT NOT NULL,
    cursor BIGINT NOT NULL,
    sealed BOOLEAN NOT NULL
);
