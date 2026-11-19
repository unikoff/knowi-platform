-- Disposable developer stack. Fresh legacy tables need their local runtime
-- permissions before the first smoke test. Production uses existing ACLs.
CREATE ROLE core_local LOGIN PASSWORD 'local-disposable';
GRANT USAGE ON SCHEMA public TO core_local;
ALTER DEFAULT PRIVILEGES FOR ROLE core_local_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO core_local;
ALTER DEFAULT PRIVILEGES FOR ROLE core_local_owner IN SCHEMA public
    GRANT USAGE ON SEQUENCES TO core_local;
