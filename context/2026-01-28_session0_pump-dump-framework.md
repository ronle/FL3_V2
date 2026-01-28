# Session: 2026-01-28 Session 0 — Pump & Dump Detection Framework

## Summary

Fresh analysis of pump-and-dump detection patterns, ignoring prior FL3 context. Established research-backed indicators, then evolved into options-driven gamma squeeze mechanics with complete formula set for Greeks calculations.

## Key Indicators Established

### Volume Anomalies (Primary)
- Volume spike ≥60% of 30-day highest (academic research)
- At least 1/3 of monthly volume at pump event
- Pullback volume > upswing volume = dump signal

### Price Behavior
- RSI > 70 = overbought/manipulation risk
- Short-term MA diverging rapidly from long-term MA
- Price > 3x ATR(14) without news = anomaly

### Stock Profile Vulnerability
- Microcap/small float
- OTC-traded preferred targets
- Limited public information

## Three-Phase Gamma Squeeze Lifecycle

### Phase 1: Setup (Accumulation)
```
RelVol = V_t / ADV_20 > 1.8
NormATR = ATR / Price, expansion > 1.5x
Call/Put Ratio > 2
NetGEX = Σ(OI × Γ × 100 × S²) — MUST include dollar normalization
```

### Phase 2: Acceleration (Pump)
```
Positive NetGEX + Rising short-dated call OI
DEX tracking: Net_DEX = Σ[Δ_call × CallOI] - Σ[|Δ_put| × PutOI] × 100
Hedge Pressure = ΔHedge / ADV_20 > 0.10
Price > 3x ATR without catalyst
VWAP deviation > 2σ
```

### Phase 3: Reversal (Dump)
```
Gamma Flip: Net gamma transitions positive → negative
Vanna Effect: IV drop → dealer hedge reduction → selling
Charm Effect: Delta decay → selling pressure into OpEx
Put Wall Breach: Cascade acceleration
```

## Critical Formulas

### Black-Scholes Variables
```
d1 = [ln(S/K) + (r - q + σ²/2)T] / (σ√T)
d2 = d1 - σ√T
```

### Core Greeks
- **Delta (Δ)**: e^(-qT) × N(d1)
- **Gamma (Γ)**: [e^(-qT) × n(d1)] / [S × σ × √T]
- **Vanna**: −e^(-qT) × n(d1) × (d2/σ)
- **Charm**: −e^(-qT) × n(d1) × [q + (d2 × σ)/(2√T)]

### Aggregate Exposures
```python
# GEX (Gamma Exposure)
Net_GEX = Σ[(CallOI × Γ_call) - (PutOI × Γ_put)] × 100 × S² × 0.01

# DEX (Delta Exposure)
Net_DEX = Σ[Δ_call × CallOI] - Σ[|Δ_put| × PutOI] × 100

# VEX (Vanna Exposure)
Net_VEX = Σ[Vanna(K) × OI(K) × 100]
```

## Data Sources Recommended

| Priority | Source | Cost | Purpose |
|----------|--------|------|---------|
| Primary | Polygon.io Options | $199/mo | Full OPRA, real-time chains |
| Greeks | ORATS | $100-300/mo | Pre-calculated IV, Greeks |
| Validation | SpotGamma | $300+/mo | GEX levels for top 200 |
| News | Benzinga | $50-100/mo | Catalyst detection |

## Key Insight

Traditional P&D rules (OTC/microcap) adapted for NYSE/NASDAQ requires:
1. Options flow as primary signal (not price alone)
2. Gamma/Vanna/Charm mechanics as drivers
3. Dealer hedging quantification
4. Multi-timeframe analysis

## Files/Artifacts

- Original transcript: `/mnt/transcripts/2026-01-28-19-58-11-pump-dump-detection-framework.txt`

---
*Session end: 2026-01-28*
