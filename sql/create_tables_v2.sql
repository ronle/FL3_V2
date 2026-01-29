-- FL3_V2 Database Schema
-- Created: 2026-01-28
-- Phase 1: All V2 tables

-- ============================================================
-- 1.1: Intraday Baselines (30-minute buckets)
-- Purpose: Time-of-day volume calibration for UOA detection
-- Est. rows/day: ~13,000
-- ============================================================
CREATE TABLE IF NOT EXISTS intraday_baselines_30m (
    symbol TEXT NOT NULL,
    trade_date DATE NOT NULL,
    bucket_start TIME NOT NULL,  -- 09:30, 10:00, 10:30, etc.
    prints INTEGER NOT NULL DEFAULT 0,
    notional NUMERIC NOT NULL DEFAULT 0,
    contracts_unique INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (symbol, trade_date, bucket_start)
);

CREATE INDEX IF NOT EXISTS idx_baselines_date
    ON intraday_baselines_30m(trade_date);
CREATE INDEX IF NOT EXISTS idx_baselines_symbol_date
    ON intraday_baselines_30m(symbol, trade_date DESC);

COMMENT ON TABLE intraday_baselines_30m IS 'V2: 30-minute bucket aggregates for time-of-day baseline calibration';

-- ============================================================
-- 1.2: GEX Metrics Snapshot
-- Purpose: GEX/DEX/Vanna/Charm on UOA trigger
-- Est. rows/day: 50-500
-- ============================================================
CREATE TABLE IF NOT EXISTS gex_metrics_snapshot (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    snapshot_ts TIMESTAMPTZ NOT NULL,
    spot_price NUMERIC,
    net_gex NUMERIC,           -- Net gamma exposure
    net_dex NUMERIC,           -- Net delta exposure
    call_wall_strike NUMERIC,  -- Strike with max call OI
    put_wall_strike NUMERIC,   -- Strike with max put OI
    gamma_flip_level NUMERIC,  -- Price where Net GEX crosses zero
    net_vex NUMERIC,           -- Vanna exposure
    net_charm NUMERIC,         -- Charm exposure
    contracts_analyzed INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gex_symbol_ts
    ON gex_metrics_snapshot(symbol, snapshot_ts DESC);
CREATE INDEX IF NOT EXISTS idx_gex_ts
    ON gex_metrics_snapshot(snapshot_ts DESC);

COMMENT ON TABLE gex_metrics_snapshot IS 'V2: Greeks exposure snapshots captured on UOA trigger';

-- ============================================================
-- 1.3: UOA Triggers V2
-- Purpose: Triggered UOA events with full context
-- Est. rows/day: 50-500
-- ============================================================
CREATE TABLE IF NOT EXISTS uoa_triggers_v2 (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    trigger_ts TIMESTAMPTZ NOT NULL,
    trigger_type TEXT NOT NULL,        -- 'volume', 'notional', 'contracts'
    volume_ratio NUMERIC,              -- Actual / baseline
    notional NUMERIC,                  -- Dollar value of trades
    baseline_notional NUMERIC,         -- Expected baseline
    contracts INTEGER,                 -- Number of contracts
    prints INTEGER,                    -- Number of trade prints
    bucket_start TIME,                 -- Which 30-min bucket
    meta_json JSONB,                   -- Additional context
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_uoa_v2_symbol_ts
    ON uoa_triggers_v2(symbol, trigger_ts DESC);
CREATE INDEX IF NOT EXISTS idx_uoa_v2_ts
    ON uoa_triggers_v2(trigger_ts DESC);
CREATE INDEX IF NOT EXISTS idx_uoa_v2_type
    ON uoa_triggers_v2(trigger_type, trigger_ts DESC);

COMMENT ON TABLE uoa_triggers_v2 IS 'V2: Unusual options activity trigger events';

-- ============================================================
-- 1.4: P&D Phase Signals
-- Purpose: Phase transition signals (Setup/Acceleration/Reversal)
-- Est. rows/day: 10-100
-- ============================================================
CREATE TABLE IF NOT EXISTS pd_phase_signals (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    signal_ts TIMESTAMPTZ NOT NULL,
    phase TEXT NOT NULL,               -- 'setup', 'acceleration', 'reversal'
    score NUMERIC,                     -- Confidence score (0-100)
    contributing_factors JSONB,        -- Which signals contributed
    previous_phase TEXT,               -- Phase before transition
    meta_json JSONB,                   -- Additional context
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_phase_symbol_ts
    ON pd_phase_signals(symbol, signal_ts DESC);
CREATE INDEX IF NOT EXISTS idx_phase_ts
    ON pd_phase_signals(signal_ts DESC);
CREATE INDEX IF NOT EXISTS idx_phase_phase
    ON pd_phase_signals(phase, signal_ts DESC);

COMMENT ON TABLE pd_phase_signals IS 'V2: Pump-and-dump phase transition signals';

-- ============================================================
-- 1.5: Tracked Tickers V2
-- Purpose: Permanent tracking list (never removed once triggered)
-- Est. rows: ~1,000 (grows over time)
-- ============================================================
CREATE TABLE IF NOT EXISTS tracked_tickers_v2 (
    symbol TEXT PRIMARY KEY,
    first_trigger_ts TIMESTAMPTZ,
    trigger_count INTEGER DEFAULT 1,
    last_trigger_ts TIMESTAMPTZ,
    ta_enabled BOOLEAN DEFAULT TRUE,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tracked_v2_ta_enabled
    ON tracked_tickers_v2(ta_enabled) WHERE ta_enabled = TRUE;

COMMENT ON TABLE tracked_tickers_v2 IS 'V2: Permanent symbol tracking list for TA pipeline';

-- ============================================================
-- 1.6: TA Snapshots V2
-- Purpose: 5-minute interval TA data for tracked symbols
-- Est. rows/day: ~78,000 (1000 symbols × 78 intervals)
-- ============================================================
CREATE TABLE IF NOT EXISTS ta_snapshots_v2 (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    snapshot_ts TIMESTAMPTZ NOT NULL,
    price NUMERIC,
    volume BIGINT,
    rsi_14 NUMERIC,
    atr_14 NUMERIC,
    vwap NUMERIC,
    sma_20 NUMERIC,
    ema_9 NUMERIC,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, snapshot_ts)
);

CREATE INDEX IF NOT EXISTS idx_ta_v2_symbol_ts
    ON ta_snapshots_v2(symbol, snapshot_ts DESC);
CREATE INDEX IF NOT EXISTS idx_ta_v2_ts
    ON ta_snapshots_v2(snapshot_ts DESC);

COMMENT ON TABLE ta_snapshots_v2 IS 'V2: Technical analysis snapshots at 5-minute intervals';

-- ============================================================
-- Summary view for monitoring
-- ============================================================
CREATE OR REPLACE VIEW v2_table_stats AS
SELECT
    'intraday_baselines_30m' as table_name,
    COUNT(*) as row_count,
    pg_size_pretty(pg_relation_size('intraday_baselines_30m')) as size
FROM intraday_baselines_30m
UNION ALL
SELECT 'gex_metrics_snapshot', COUNT(*), pg_size_pretty(pg_relation_size('gex_metrics_snapshot'))
FROM gex_metrics_snapshot
UNION ALL
SELECT 'uoa_triggers_v2', COUNT(*), pg_size_pretty(pg_relation_size('uoa_triggers_v2'))
FROM uoa_triggers_v2
UNION ALL
SELECT 'pd_phase_signals', COUNT(*), pg_size_pretty(pg_relation_size('pd_phase_signals'))
FROM pd_phase_signals
UNION ALL
SELECT 'tracked_tickers_v2', COUNT(*), pg_size_pretty(pg_relation_size('tracked_tickers_v2'))
FROM tracked_tickers_v2
UNION ALL
SELECT 'ta_snapshots_v2', COUNT(*), pg_size_pretty(pg_relation_size('ta_snapshots_v2'))
FROM ta_snapshots_v2;

COMMENT ON VIEW v2_table_stats IS 'V2: Quick stats view for all V2 tables';
