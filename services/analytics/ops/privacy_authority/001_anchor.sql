-- Provision once on the independent PostgreSQL primary, by a schema owner.
-- This artifact is NOT executed by the authority. Existing objects fail closed.
BEGIN;
CREATE TABLE public.privacy_authority_anchor (
    authority_id text PRIMARY KEY,
    version integer NOT NULL CHECK (version = 1),
    high_watermark bigint NOT NULL CHECK (high_watermark >= 0),
    digest text NOT NULL CHECK (digest ~ '^[0-9a-f]{64}$')
);

CREATE FUNCTION public.privacy_authority_anchor_guard_v1() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'privacy authority anchor deletion forbidden';
    END IF;
    IF NEW.authority_id IS DISTINCT FROM OLD.authority_id
       OR NEW.version IS DISTINCT FROM OLD.version
       OR NEW.high_watermark < OLD.high_watermark
       OR NEW.high_watermark - OLD.high_watermark > 1
       OR (NEW.high_watermark = OLD.high_watermark AND NEW.digest IS DISTINCT FROM OLD.digest) THEN
        RAISE EXCEPTION 'invalid privacy authority anchor transition';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER privacy_authority_anchor_guard_v1
BEFORE UPDATE OR DELETE ON public.privacy_authority_anchor
FOR EACH ROW EXECUTE FUNCTION public.privacy_authority_anchor_guard_v1();
REVOKE ALL ON public.privacy_authority_anchor FROM PUBLIC;
COMMIT;

-- Provision a single row through a privileged, separately reviewed procedure:
-- INSERT INTO public.privacy_authority_anchor(authority_id,version,high_watermark,digest)
-- VALUES ('<authority-id>', 1, 0, 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
-- Grant a dedicated runtime role only SELECT and UPDATE(high_watermark,digest).
-- Do not grant INSERT, DELETE, TRUNCATE, table ownership, DDL, trigger alteration,
-- or membership in privileged roles. Restrict access to the independently managed primary.
-- A privileged restore or a rollback of this entire database is outside trigger protection.
