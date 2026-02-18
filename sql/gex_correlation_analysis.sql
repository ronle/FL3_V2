-- =============================================================================
-- GEX Shadow Mode Correlation Analysis
-- Run after 2+ days of shadow data (target: Monday Feb 9, 2026)
-- =============================================================================
-- Prerequisites:
--   - gex_metrics_snapshot populated (backfill + nightly)
--   - paper_trades_log has trades with exit data
--   - signal_evaluations has metadata JSONB with GEX fields
-- =============================================================================


-- =============================================================================
-- QUERY 1: GEX Regime Split
-- =============================================================================
-- Core thesis test: Do trades in negative GEX regimes (dealers chase momentum)
-- outperform trades in positive GEX regimes (dealers dampen moves)?
--
-- Expected if GEX has signal:
--   negative GEX → higher avg PnL (pump runs further before dealer resistance)
--   positive GEX → lower avg PnL (dealers cap the move)
-- =============================================================================

WITH trades_with_gex AS (
    SELECT
        pt.id,
        pt.symbol,
        pt.entry_time,
        pt.entry_price,
        pt.exit_time,
        pt.exit_price,
        pt.pnl,
        pt.pnl_pct,
        pt.exit_reason,
        pt.signal_score,
        g.net_gex,
        g.net_dex,
        g.gamma_flip_level,
        g.call_wall_strike,
        g.put_wall_strike,
        g.spot_price as gex_spot,
        g.contracts_analyzed,
        CASE
            WHEN g.net_gex < 0 THEN 'NEGATIVE'
            WHEN g.net_gex > 0 THEN 'POSITIVE'
            ELSE 'ZERO'
        END as gex_regime
    FROM paper_trades_log pt
    JOIN gex_metrics_snapshot g
        ON g.symbol = pt.symbol
        AND g.snapshot_ts::date = pt.entry_time::date
    WHERE pt.exit_price IS NOT NULL  -- only closed trades
)
SELECT
    gex_regime,
    COUNT(*) as trades,
    ROUND(AVG(pnl_pct)::numeric, 3) as avg_pnl_pct,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY pnl_pct)::numeric, 3) as median_pnl_pct,
    ROUND(STDDEV(pnl_pct)::numeric, 3) as stddev_pnl_pct,
    COUNT(*) FILTER (WHERE pnl_pct > 0) as wins,
    COUNT(*) FILTER (WHERE pnl_pct <= 0) as losses,
    ROUND(COUNT(*) FILTER (WHERE pnl_pct > 0)::numeric / NULLIF(COUNT(*), 0) * 100, 1) as win_rate_pct,
    ROUND(AVG(pnl_pct) FILTER (WHERE pnl_pct > 0)::numeric, 3) as avg_win_pct,
    ROUND(AVG(pnl_pct) FILTER (WHERE pnl_pct <= 0)::numeric, 3) as avg_loss_pct,
    ROUND(MIN(pnl_pct)::numeric, 3) as worst_trade_pct,
    ROUND(MAX(pnl_pct)::numeric, 3) as best_trade_pct,
    ROUND(SUM(pnl)::numeric, 2) as total_pnl_dollars
FROM trades_with_gex
GROUP BY gex_regime
ORDER BY avg_pnl_pct DESC;


-- =============================================================================
-- QUERY 2: Gamma Flip Distance
-- =============================================================================
-- Does proximity to gamma flip level predict trade outcomes?
--
-- Theory: Entry price below gamma flip = negative GEX zone = dealers amplify.
-- Entry price above gamma flip = positive GEX zone = dealers dampen.
--
-- Buckets by distance from gamma flip as % of spot price.
-- =============================================================================

WITH trades_with_flip AS (
    SELECT
        pt.id,
        pt.symbol,
        pt.entry_price,
        pt.pnl_pct,
        pt.exit_reason,
        g.gamma_flip_level,
        g.spot_price as gex_spot,
        g.net_gex,
        -- Distance from gamma flip as % of spot
        CASE
            WHEN g.gamma_flip_level IS NOT NULL AND g.spot_price > 0
            THEN ((pt.entry_price - g.gamma_flip_level) / g.spot_price * 100)
            ELSE NULL
        END as flip_distance_pct,
        -- Are we above or below the flip?
        CASE
            WHEN g.gamma_flip_level IS NOT NULL AND pt.entry_price > g.gamma_flip_level THEN 'ABOVE_FLIP'
            WHEN g.gamma_flip_level IS NOT NULL AND pt.entry_price <= g.gamma_flip_level THEN 'BELOW_FLIP'
            ELSE 'NO_FLIP'
        END as flip_position
    FROM paper_trades_log pt
    JOIN gex_metrics_snapshot g
        ON g.symbol = pt.symbol
        AND g.snapshot_ts::date = pt.entry_time::date
    WHERE pt.exit_price IS NOT NULL
)
SELECT
    flip_position,
    COUNT(*) as trades,
    ROUND(AVG(pnl_pct)::numeric, 3) as avg_pnl_pct,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY pnl_pct)::numeric, 3) as median_pnl_pct,
    COUNT(*) FILTER (WHERE pnl_pct > 0) as wins,
    COUNT(*) FILTER (WHERE pnl_pct <= 0) as losses,
    ROUND(COUNT(*) FILTER (WHERE pnl_pct > 0)::numeric / NULLIF(COUNT(*), 0) * 100, 1) as win_rate_pct,
    ROUND(AVG(flip_distance_pct)::numeric, 2) as avg_flip_distance_pct,
    ROUND(SUM(pnl_pct)::numeric, 3) as cumulative_pnl_pct
FROM trades_with_flip
GROUP BY flip_position
ORDER BY avg_pnl_pct DESC;

-- Detailed: bucket by flip distance quartiles
SELECT
    CASE
        WHEN flip_distance_pct IS NULL THEN '5_NO_FLIP'
        WHEN flip_distance_pct < -2 THEN '1_FAR_BELOW (-2%+)'
        WHEN flip_distance_pct < 0 THEN '2_NEAR_BELOW (0 to -2%)'
        WHEN flip_distance_pct < 2 THEN '3_NEAR_ABOVE (0 to +2%)'
        ELSE '4_FAR_ABOVE (+2%+)'
    END as flip_bucket,
    COUNT(*) as trades,
    ROUND(AVG(pnl_pct)::numeric, 3) as avg_pnl_pct,
    ROUND(COUNT(*) FILTER (WHERE pnl_pct > 0)::numeric / NULLIF(COUNT(*), 0) * 100, 1) as win_rate_pct,
    ROUND(SUM(pnl_pct)::numeric, 3) as cumulative_pnl_pct
FROM (
    SELECT
        pt.pnl_pct,
        CASE
            WHEN g.gamma_flip_level IS NOT NULL AND g.spot_price > 0
            THEN ((pt.entry_price - g.gamma_flip_level) / g.spot_price * 100)
            ELSE NULL
        END as flip_distance_pct
    FROM paper_trades_log pt
    JOIN gex_metrics_snapshot g
        ON g.symbol = pt.symbol
        AND g.snapshot_ts::date = pt.entry_time::date
    WHERE pt.exit_price IS NOT NULL
) sub
GROUP BY flip_bucket
ORDER BY flip_bucket;


-- =============================================================================
-- QUERY 3: Wall Proximity
-- =============================================================================
-- Do trades near call walls (dealer hedging resistance) underperform?
--
-- Theory: Call wall = strike with max call OI. Dealers are short these calls,
-- so they sell stock as price approaches → creates resistance.
-- Trades with lots of room between entry and call wall should run further.
-- =============================================================================

WITH trades_with_walls AS (
    SELECT
        pt.id,
        pt.symbol,
        pt.entry_price,
        pt.pnl_pct,
        pt.exit_reason,
        g.call_wall_strike,
        g.put_wall_strike,
        g.spot_price as gex_spot,
        -- Room to run: distance from entry to call wall as % of entry price
        CASE
            WHEN g.call_wall_strike > 0 AND pt.entry_price > 0
            THEN ((g.call_wall_strike - pt.entry_price) / pt.entry_price * 100)
            ELSE NULL
        END as room_to_call_wall_pct,
        -- Distance above put wall (support) as % of entry price
        CASE
            WHEN g.put_wall_strike > 0 AND pt.entry_price > 0
            THEN ((pt.entry_price - g.put_wall_strike) / pt.entry_price * 100)
            ELSE NULL
        END as distance_above_put_wall_pct,
        -- Entry position relative to walls
        CASE
            WHEN g.call_wall_strike IS NOT NULL AND pt.entry_price >= g.call_wall_strike THEN 'AT_OR_ABOVE_CALL_WALL'
            WHEN g.call_wall_strike IS NOT NULL AND g.put_wall_strike IS NOT NULL
                 AND pt.entry_price BETWEEN g.put_wall_strike AND g.call_wall_strike THEN 'BETWEEN_WALLS'
            WHEN g.put_wall_strike IS NOT NULL AND pt.entry_price <= g.put_wall_strike THEN 'AT_OR_BELOW_PUT_WALL'
            ELSE 'WALLS_UNKNOWN'
        END as wall_position
    FROM paper_trades_log pt
    JOIN gex_metrics_snapshot g
        ON g.symbol = pt.symbol
        AND g.snapshot_ts::date = pt.entry_time::date
    WHERE pt.exit_price IS NOT NULL
)
SELECT
    wall_position,
    COUNT(*) as trades,
    ROUND(AVG(pnl_pct)::numeric, 3) as avg_pnl_pct,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY pnl_pct)::numeric, 3) as median_pnl_pct,
    COUNT(*) FILTER (WHERE pnl_pct > 0) as wins,
    COUNT(*) FILTER (WHERE pnl_pct <= 0) as losses,
    ROUND(COUNT(*) FILTER (WHERE pnl_pct > 0)::numeric / NULLIF(COUNT(*), 0) * 100, 1) as win_rate_pct,
    ROUND(AVG(room_to_call_wall_pct)::numeric, 2) as avg_room_to_call_wall_pct,
    ROUND(SUM(pnl_pct)::numeric, 3) as cumulative_pnl_pct
FROM trades_with_walls
GROUP BY wall_position
ORDER BY avg_pnl_pct DESC;

-- Detailed: bucket by room-to-call-wall
SELECT
    CASE
        WHEN room_to_call_wall_pct IS NULL THEN '5_NO_WALL_DATA'
        WHEN room_to_call_wall_pct <= 0 THEN '1_ABOVE_CALL_WALL'
        WHEN room_to_call_wall_pct < 2 THEN '2_NEAR_WALL (<2%)'
        WHEN room_to_call_wall_pct < 5 THEN '3_MODERATE (2-5%)'
        ELSE '4_LOTS_OF_ROOM (5%+)'
    END as wall_bucket,
    COUNT(*) as trades,
    ROUND(AVG(pnl_pct)::numeric, 3) as avg_pnl_pct,
    ROUND(COUNT(*) FILTER (WHERE pnl_pct > 0)::numeric / NULLIF(COUNT(*), 0) * 100, 1) as win_rate_pct,
    ROUND(SUM(pnl_pct)::numeric, 3) as cumulative_pnl_pct
FROM trades_with_walls
GROUP BY wall_bucket
ORDER BY wall_bucket;


-- =============================================================================
-- BONUS: Per-trade detail dump for manual inspection
-- =============================================================================
-- Export for deeper analysis in Python/Excel if needed

SELECT
    pt.symbol,
    pt.entry_time,
    pt.entry_price,
    pt.exit_price,
    pt.pnl_pct,
    pt.exit_reason,
    pt.signal_score,
    g.net_gex,
    g.net_dex,
    g.gamma_flip_level,
    g.call_wall_strike,
    g.put_wall_strike,
    g.spot_price as gex_spot,
    g.contracts_analyzed,
    CASE WHEN g.net_gex < 0 THEN 'NEG' ELSE 'POS' END as gex_regime,
    CASE
        WHEN g.gamma_flip_level IS NOT NULL AND g.spot_price > 0
        THEN ROUND(((pt.entry_price - g.gamma_flip_level) / g.spot_price * 100)::numeric, 2)
        ELSE NULL
    END as flip_distance_pct,
    CASE
        WHEN g.call_wall_strike > 0 AND pt.entry_price > 0
        THEN ROUND(((g.call_wall_strike - pt.entry_price) / pt.entry_price * 100)::numeric, 2)
        ELSE NULL
    END as room_to_call_wall_pct
FROM paper_trades_log pt
JOIN gex_metrics_snapshot g
    ON g.symbol = pt.symbol
    AND g.snapshot_ts::date = pt.entry_time::date
WHERE pt.exit_price IS NOT NULL
ORDER BY pt.entry_time DESC;
