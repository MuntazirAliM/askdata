

\set ON_ERROR_STOP on
SELECT set_config('askdata.pw', :'agent_password', false);

DO $$
DECLARE
    pw       text := current_setting('askdata.pw');
    r        record;
    rolename text;
    tbl      text;
BEGIN
    -- 1. Roles: one owner plus one manager per location. Read-only, time-limited.
    FOR r IN SELECT NULL::int AS loc UNION ALL SELECT id FROM locations LOOP
        rolename := CASE WHEN r.loc IS NULL THEN 'askdata_owner' ELSE 'askdata_mgr_' || r.loc END;

        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = rolename) THEN
            EXECUTE format('CREATE ROLE %I LOGIN', rolename);
        END IF;

        EXECUTE format('ALTER ROLE %I PASSWORD %L', rolename, pw);
        EXECUTE format('ALTER ROLE %I SET statement_timeout = %L', rolename, '5s');
        EXECUTE format('ALTER ROLE %I SET default_transaction_read_only = on', rolename);
        EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), rolename);
        EXECUTE format('GRANT USAGE ON SCHEMA public TO %I', rolename);
        EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM %I', rolename);
        -- app_users is deliberately not granted.
        EXECUTE format(
            'GRANT SELECT ON locations, products, orders, order_items, daily_sales, product_sales TO %I',
            rolename);
    END LOOP;

    -- 2. Row-level security policies.
    FOREACH tbl IN ARRAY ARRAY['orders', 'order_items', 'locations'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tbl);

        FOR r IN SELECT policyname FROM pg_policies WHERE schemaname = 'public' AND tablename = tbl LOOP
            EXECUTE format('DROP POLICY %I ON %I', r.policyname, tbl);
        END LOOP;

        EXECUTE format('CREATE POLICY owner_all ON %I FOR SELECT TO askdata_owner USING (true)', tbl);

        FOR r IN SELECT id FROM locations LOOP
            EXECUTE format(
                'CREATE POLICY mgr_%s ON %I FOR SELECT TO %I USING (%I = %s)',
                r.id, tbl, 'askdata_mgr_' || r.id,
                CASE WHEN tbl = 'locations' THEN 'id' ELSE 'location_id' END,
                r.id);
        END LOOP;
    END LOOP;
END
$$;

SELECT set_config('askdata.pw', '', false);
