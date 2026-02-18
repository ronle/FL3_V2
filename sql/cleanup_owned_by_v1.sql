-- Cleanup tables owned by V1 service account
-- Run this as postgres superuser via Cloud SQL console or psql

-- Tables that failed ownership check in Phase 2
-- These need to be dropped by the owner or superuser

-- First, transfer ownership to postgres (or drop directly as superuser)
DO $$
DECLARE
    tables_to_drop TEXT[] := ARRAY[
        'uoa_underlying_agg_5m',
        'spot_returns',
        'signal_regime_map',
        'impulse_response_curves',
        'cascade_policy',
        'pump_dump_metrics_raw',
        'pump_dump_labels_v3',
        'pump_dump_labels_v2',
        'wave_symbol_exclusions',
        'wave_pillar_snapshot',
        'wave_ml_scores_daily',
        'wave_intraday_features_5m_latest'
    ];
    tbl TEXT;
BEGIN
    FOREACH tbl IN ARRAY tables_to_drop
    LOOP
        -- Check if table exists
        IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename = tbl) THEN
            -- Move to v1_backup schema first (preserves data temporarily)
            EXECUTE format('ALTER TABLE %I SET SCHEMA v1_backup', tbl);
            RAISE NOTICE 'Moved % to v1_backup', tbl;

            -- Then drop from v1_backup
            EXECUTE format('DROP TABLE IF EXISTS v1_backup.%I CASCADE', tbl);
            RAISE NOTICE 'Dropped v1_backup.%', tbl;
        ELSE
            RAISE NOTICE 'Table % not found in public schema', tbl;
        END IF;
    END LOOP;
END $$;

-- Verify cleanup
SELECT 'Remaining in public:' as status, count(*) as cnt FROM pg_tables WHERE schemaname = 'public'
UNION ALL
SELECT 'Remaining in v1_backup:' as status, count(*) as cnt FROM pg_tables WHERE schemaname = 'v1_backup';
