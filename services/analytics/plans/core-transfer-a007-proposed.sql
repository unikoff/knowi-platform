-- DESIGN AND ISOLATED TEST FIXTURE ONLY; NOT AN ACTIVE ALEMBIC REVISION.
-- Apply only in a disposable database already at a006 and after explicit GO.
-- The eventual migration must separately grant least privilege per runtime role.
ALTER TABLE analytics.activity_facts ADD COLUMN source_kind text NOT NULL DEFAULT 'browser_v2';
ALTER TABLE analytics.activity_facts ALTER COLUMN course_id DROP NOT NULL;
ALTER TABLE analytics.activity_facts ALTER COLUMN catalog_version DROP NOT NULL;
ALTER TABLE analytics.activity_facts ALTER COLUMN started_at DROP NOT NULL;
ALTER TABLE analytics.activity_facts ADD CONSTRAINT activity_fact_source_shape CHECK (
  (source_kind = 'browser_v2' AND course_id IS NOT NULL AND catalog_version IS NOT NULL AND started_at IS NOT NULL)
  OR (source_kind = 'core_transfer_v1' AND course_id IS NULL AND catalog_version IS NULL AND lesson_id IS NULL AND started_at IS NULL)
);
CREATE INDEX activity_browser_time_subject ON analytics.activity_facts (started_at,subject_ref) WHERE source_kind='browser_v2';

CREATE TABLE analytics.core_transfer_entities (
  producer_namespace varchar(128) NOT NULL,
  event_id uuid NOT NULL,
  subject_ref uuid NOT NULL,
  entity_key char(64) NOT NULL CHECK (entity_key ~ '^[0-9a-f]{64}$'),
  revision bigint NOT NULL CHECK (revision >= 0),
  canonical_hash char(64) NOT NULL CHECK (canonical_hash ~ '^[0-9a-f]{64}$'),
  PRIMARY KEY (producer_namespace, subject_ref, entity_key, revision),
  UNIQUE (producer_namespace, event_id, subject_ref),
  UNIQUE (producer_namespace,subject_ref,entity_key,revision,event_id),
  FOREIGN KEY (producer_namespace,event_id,subject_ref)
    REFERENCES analytics.activity_facts(producer_namespace,event_id,subject_ref) ON DELETE CASCADE
);
CREATE INDEX core_transfer_entities_subject ON analytics.core_transfer_entities(subject_ref);

CREATE TABLE analytics.core_transfer_projection (
  producer_namespace varchar(128) NOT NULL,
  subject_ref uuid NOT NULL,
  entity_key char(64) NOT NULL CHECK (entity_key ~ '^[0-9a-f]{64}$'),
  revision bigint NOT NULL CHECK (revision >= 0),
  event_id uuid NOT NULL,
  PRIMARY KEY (producer_namespace,subject_ref,entity_key),
  FOREIGN KEY (producer_namespace,event_id,subject_ref)
    REFERENCES analytics.activity_facts(producer_namespace,event_id,subject_ref) ON DELETE CASCADE,
  FOREIGN KEY (producer_namespace,subject_ref,entity_key,revision,event_id)
    REFERENCES analytics.core_transfer_entities(producer_namespace,subject_ref,entity_key,revision,event_id) ON DELETE CASCADE
);
CREATE INDEX core_transfer_projection_subject ON analytics.core_transfer_projection(subject_ref);
