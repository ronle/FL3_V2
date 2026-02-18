# Session: 2026-01-28 Session 0b — Database Gap Analysis

## Summary

Comprehensive gap analysis mapping existing FL3 PostgreSQL database schema against the pump-and-dump detection framework requirements. Identified what data exists, what's missing, and implementation priorities.

## Current Data Sources

| Source | Table | Key Data | Rows |
|--------|-------|----------|------|
| ORATS Daily | `orats_daily` | Aggregate options metrics, IV, HV | 2.7M |
| Polygon | `option_trades` | Individual option trades | 100M+ |
| Polygon | `option_greeks_latest` | Per-contract Greeks (Δ, Γ, θ, ν) | 65K |
| Polygon | `option_oi_daily` | Per-contract OI | 395K |
| Alpaca | `spot_prices`, `ta_snapshots_*` | Spot prices, TA indicators | 24K |

## What We HAVE

### Phase 1 (Setup)
- ✅ RelVol: `orats_daily.total_volume / avg_daily_volume`
- ✅ NormATR: `ta_snapshots.atr14 / orats_daily.stock_price`
- ✅ Call/Put Ratio: `orats_daily.put_call_ratio`
- ✅ OI Change (aggregate): `orats_daily.delta_call_oi / delta_put_oi`

### Phase 2 (Acceleration)
- ✅ Price > 3x ATR: Computable
- ✅ RSI: `ta_snapshots.rsi14`

### Phase 3 (Reversal)
- ✅ RSI Divergence: Computable from ta_snapshots
- ✅ Volume Climax: Computable

## Critical GAPS

| # | Missing Element | Required For | Implementation |
|---|----------------|--------------|----------------|
| 1 | **GEX/DEX Aggregation** | Phase 1 & 2 | Build from greeks + OI |
| 2 | **Vanna per contract** | Phase 3 | Calculate via BS model |
| 3 | **Charm per contract** | Phase 3 | Calculate via BS model |
| 4 | **Strike-level OI** | Wall detection | Aggregate from option_oi_daily |
| 5 | **IV by Expiration** | Term structure | Expand ORATS ingestion |
| 6 | **Gamma Flip Level** | Threshold detection | Calculate from GEX profile |

## Proposed New Table

```sql
CREATE TABLE gex_metrics_daily (
    underlying TEXT NOT NULL,
    asof_date DATE NOT NULL,
    
    -- Core GEX/DEX
    net_gex NUMERIC,
    net_dex NUMERIC,
    gex_change_1d NUMERIC,
    dex_change_1d NUMERIC,
    
    -- Gamma Profile
    gamma_flip_level NUMERIC,
    call_wall_strike NUMERIC,
    put_wall_strike NUMERIC,
    
    -- Second-order Greeks
    net_vex NUMERIC,
    net_charm NUMERIC,
    
    -- Derived signals
    hedge_pressure_ratio NUMERIC,
    gex_regime TEXT,
    
    PRIMARY KEY (underlying, asof_date)
);
```

## SQL Examples (Buildable Today)

### Strike-level OI aggregation
```sql
SELECT 
    oc.underlying,
    oc.expiry::date,
    oc.strike,
    oc.right,
    SUM(oid.oi) as total_oi
FROM option_oi_daily oid
JOIN option_contracts oc ON oid.contract_id = oc.id
WHERE oid.asof_date = CURRENT_DATE
GROUP BY oc.underlying, oc.expiry::date, oc.strike, oc.right;
```

### GEX per underlying
```sql
SELECT 
    oc.underlying,
    SUM(ogl.gamma * oid.oi * 100 * sp.underlying * sp.underlying * 0.01) as net_gex
FROM option_greeks_latest ogl
JOIN option_contracts oc ON ogl.contract_id = oc.id
JOIN option_oi_daily oid ON oid.contract_id = oc.id
JOIN spot_prices sp ON sp.ticker = oc.underlying
WHERE oid.asof_date = CURRENT_DATE
GROUP BY oc.underlying;
```

## Implementation Priority

| Priority | Component | Effort |
|----------|-----------|--------|
| P0 | GEX/DEX daily aggregate | Medium |
| P0 | Strike-level OI aggregation | Low |
| P1 | Vanna/Charm calculation | Medium |
| P1 | Call/Put Wall identification | Low |
| P1 | Gamma Flip Level | Medium |
| P2 | IV Term Structure | Low |

## Key Finding

**Most data exists** — the gap is aggregation and second-order Greek calculations, not raw data collection. The path forward is:
1. Build aggregation views/tables from existing data
2. Add Vanna/Charm calculation to Greeks pipeline
3. Create phase scoring engine

---
*Session end: 2026-01-28*
