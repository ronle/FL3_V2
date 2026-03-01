# Cameron E2E Analysis Report

Generated: 2026-02-27 17:22

Date range: 2023-01-05 to 2026-02-11

Total trades (target_1): 2,278
Total trades (target_2): 2,278

---

## N-Label Legend

| Label | Threshold | Meaning |
|-------|-----------|---------|
| **INSUFFICIENT** | N < 30 | Cannot draw conclusions. Excluded from recommendations. |
| **PRELIMINARY** | 30 <= N < 100 | Directional only. Do not base decisions solely on this. |
| **RELIABLE** | N >= 100 | Sufficient sample size for decision-making. |

---

## Layer 1: Pattern-Only Analysis (Full Date Range)

### Exit Strategy: `target_1`

#### 1A: Overall
N=2278 | WR=53.6% | Avg=0.32% | Med=0.63% | Sharpe=1.03 | PF=1.17 | Stop=31.2% | Hold=23min

#### 1B: By Pattern Type

| Pattern | N | Label | WR | Avg PnL | Med PnL | Sharpe | PF | Stop% | Hold |
|---------|---|-------|-----|---------|---------|--------|-----|-------|------|
| bull_flag | 1879 | RELIABLE | 52.6% | 0.19% | 0.51% | 0.65 | 1.10 | 32.6% | 23min |
| consolidation_breakout | 301 | RELIABLE | 59.5% | 0.49% | 0.99% | 2.80 | 1.48 | 28.6% | 22min |
| vwap_reclaim | 98 | PRELIMINARY | 56.1% | 2.20% | 0.93% | 3.26 | 1.99 | 12.2% | 22min |

#### 1C: By Pattern Strength

| Strength | N | Label | WR | Avg PnL | Sharpe | PF |
|----------|---|-------|-----|---------|--------|-----|
| strong | 661 | RELIABLE | 51.4% | 0.06% | 0.17 | 1.03 |
| moderate | 1617 | RELIABLE | 54.5% | 0.42% | 1.45 | 1.25 |

#### 1D: By Year

| Year | N | Label | WR | Avg PnL | Sharpe | PF |
|------|---|-------|-----|---------|--------|-----|
| 2023 | 712 | RELIABLE | 53.2% | 0.25% | 0.91 | 1.14 |
| 2024 | 745 | RELIABLE | 54.4% | 0.33% | 0.95 | 1.16 |
| 2025 | 737 | RELIABLE | 53.6% | 0.37% | 1.23 | 1.21 |
| 2026 | 84 | PRELIMINARY | 51.2% | 0.29% | 0.99 | 1.16 |

#### 1E: Pattern x Strength

| Pattern | Strength | N | Label | WR | Avg PnL | Sharpe |
|---------|----------|---|-------|-----|---------|--------|
| bull_flag | strong | 602 | RELIABLE | 51.0% | 0.01% | 0.04 |
| bull_flag | moderate | 1277 | RELIABLE | 53.3% | 0.27% | 0.99 |
| consolidation_breakout | strong | 38 | PRELIMINARY | 60.5% | 0.13% | 1.10 |
| consolidation_breakout | moderate | 263 | RELIABLE | 59.3% | 0.54% | 2.99 |
| vwap_reclaim | strong | 21 | INSUFFICIENT | 47.6% | 1.18% | 1.35 |
| vwap_reclaim | moderate | 77 | PRELIMINARY | 58.4% | 2.48% | 4.02 |

#### 1F: Pattern x Year

| Pattern | Year | N | Label | WR | Avg PnL | Sharpe |
|---------|------|---|-------|-----|---------|--------|
| bull_flag | 2023 | 561 | RELIABLE | 52.2% | 0.16% | 0.56 |
| bull_flag | 2024 | 622 | RELIABLE | 53.4% | 0.09% | 0.29 |
| bull_flag | 2025 | 624 | RELIABLE | 52.4% | 0.31% | 1.06 |
| bull_flag | 2026 | 72 | PRELIMINARY | 50.0% | 0.28% | 0.94 |
| consolidation_breakout | 2023 | 114 | RELIABLE | 57.0% | 0.28% | 1.52 |
| consolidation_breakout | 2024 | 90 | PRELIMINARY | 61.1% | 0.54% | 3.07 |
| consolidation_breakout | 2025 | 87 | PRELIMINARY | 59.8% | 0.55% | 3.29 |
| consolidation_breakout | 2026 | 10 | INSUFFICIENT | 70.0% | 1.97% | 14.40 |
| vwap_reclaim | 2023 | 37 | PRELIMINARY | 56.8% | 1.51% | 4.13 |
| vwap_reclaim | 2024 | 33 | PRELIMINARY | 54.5% | 4.21% | 4.70 |
| vwap_reclaim | 2025 | 26 | INSUFFICIENT | 61.5% | 1.38% | 1.98 |
| vwap_reclaim | 2026 | 2 | INSUFFICIENT | 0.0% | -7.59% | -17.19 |

---

### Exit Strategy: `target_2`

#### 1A: Overall
N=2278 | WR=45.3% | Avg=0.49% | Med=-0.72% | Sharpe=1.27 | PF=1.24 | Stop=35.8% | Hold=30min

#### 1B: By Pattern Type

| Pattern | N | Label | WR | Avg PnL | Med PnL | Sharpe | PF | Stop% | Hold |
|---------|---|-------|-----|---------|---------|--------|-----|-------|------|
| bull_flag | 1879 | RELIABLE | 44.2% | 0.40% | -0.98% | 1.06 | 1.18 | 37.3% | 30min |
| consolidation_breakout | 301 | RELIABLE | 49.8% | 0.47% | -0.00% | 2.09 | 1.39 | 33.6% | 31min |
| vwap_reclaim | 98 | PRELIMINARY | 54.1% | 2.28% | 0.26% | 3.04 | 2.00 | 13.3% | 29min |

#### 1C: By Pattern Strength

| Strength | N | Label | WR | Avg PnL | Sharpe | PF |
|----------|---|-------|-----|---------|--------|-----|
| strong | 661 | RELIABLE | 42.8% | 0.17% | 0.41 | 1.07 |
| moderate | 1617 | RELIABLE | 46.4% | 0.62% | 1.67 | 1.33 |

#### 1D: By Year

| Year | N | Label | WR | Avg PnL | Sharpe | PF |
|------|---|-------|-----|---------|--------|-----|
| 2023 | 712 | RELIABLE | 45.2% | 0.44% | 1.26 | 1.23 |
| 2024 | 745 | RELIABLE | 45.8% | 0.52% | 1.23 | 1.23 |
| 2025 | 737 | RELIABLE | 45.3% | 0.51% | 1.31 | 1.24 |
| 2026 | 84 | PRELIMINARY | 42.9% | 0.60% | 1.54 | 1.29 |

#### 1E: Pattern x Strength

| Pattern | Strength | N | Label | WR | Avg PnL | Sharpe |
|---------|----------|---|-------|-----|---------|--------|
| bull_flag | strong | 602 | RELIABLE | 42.9% | 0.22% | 0.52 |
| bull_flag | moderate | 1277 | RELIABLE | 44.8% | 0.49% | 1.36 |
| consolidation_breakout | strong | 38 | PRELIMINARY | 44.7% | 0.08% | 0.49 |
| consolidation_breakout | moderate | 263 | RELIABLE | 50.6% | 0.53% | 2.28 |
| vwap_reclaim | strong | 21 | INSUFFICIENT | 38.1% | -0.97% | -1.41 |
| vwap_reclaim | moderate | 77 | PRELIMINARY | 58.4% | 3.17% | 4.15 |

#### 1F: Pattern x Year

| Pattern | Year | N | Label | WR | Avg PnL | Sharpe |
|---------|------|---|-------|-----|---------|--------|
| bull_flag | 2023 | 561 | RELIABLE | 44.2% | 0.38% | 1.07 |
| bull_flag | 2024 | 622 | RELIABLE | 45.0% | 0.36% | 0.90 |
| bull_flag | 2025 | 624 | RELIABLE | 43.8% | 0.47% | 1.23 |
| bull_flag | 2026 | 72 | PRELIMINARY | 40.3% | 0.42% | 1.06 |
| consolidation_breakout | 2023 | 114 | RELIABLE | 47.4% | 0.14% | 0.60 |
| consolidation_breakout | 2024 | 90 | PRELIMINARY | 47.8% | 0.42% | 1.85 |
| consolidation_breakout | 2025 | 87 | PRELIMINARY | 52.9% | 0.61% | 2.96 |
| consolidation_breakout | 2026 | 10 | INSUFFICIENT | 70.0% | 3.53% | 15.18 |
| vwap_reclaim | 2023 | 37 | PRELIMINARY | 54.1% | 2.22% | 4.83 |
| vwap_reclaim | 2024 | 33 | PRELIMINARY | 54.5% | 3.88% | 3.97 |
| vwap_reclaim | 2025 | 26 | INSUFFICIENT | 57.7% | 1.09% | 1.40 |
| vwap_reclaim | 2026 | 2 | INSUFFICIENT | 0.0% | -7.59% | -17.19 |

---

## Layer 2: Article Coverage (2025+ Trades Only)

> Article data (FMP + Reddit backfill) only covers 2025-01-01 onward. 2023-2024 trades are excluded from this section to avoid biasing the 'no article' group.

### Exit Strategy: `target_1`

**Article coverage**: 340/821 trades (41.4%)

#### bull_flag

**2A: Has any article**

**has_article**: N=310 | WR=51.9% | Avg=0.31% | Med=0.33% | Sharpe=1.06 | PF=1.16 | Stop=31.0% | Hold=25min
**no_article**: N=386 | WR=52.3% | Avg=0.30% | Med=0.61% | Sharpe=1.03 | PF=1.16 | Stop=33.4% | Hold=23min
t-stat=0.035, p=0.9722
WR diff: -0.4% (95% CI: [-7.9%, +7.1%])

**2B: Pre-market news**

**has_premarket**: N=211 | WR=51.7% | Avg=0.33% | Med=0.26% | Sharpe=1.13 | PF=1.18 | Stop=29.4% | Hold=26min
**no_premarket**: N=485 | WR=52.4% | Avg=0.30% | Med=0.47% | Sharpe=1.01 | PF=1.15 | Stop=33.6% | Hold=22min
t-stat=0.084, p=0.9328
WR diff: -0.7% (95% CI: [-8.8%, +7.4%])

**2C: Source type breakdown**

| Source | N | Label | WR | Avg PnL | Sharpe |
|--------|---|-------|-----|---------|--------|
| news_only | 26 | INSUFFICIENT | 76.9% | 2.23% | 10.14 |
| social_only | 241 | RELIABLE | 51.0% | 0.29% | 0.98 |
| both | 35 | PRELIMINARY | 42.9% | -0.71% | -2.40 |
| neither | 394 | RELIABLE | 52.0% | 0.28% | 0.95 |

#### consolidation_breakout

**2A: Has any article**

**has_article**: N=25 **[INSUFFICIENT]** | WR=64.0% | Avg=0.90% | Med=1.72% | Sharpe=5.68 | PF=2.20 | Stop=28.0% | Hold=22min
**no_article**: N=72 **[PRELIMINARY]** | WR=59.7% | Avg=0.62% | Med=1.37% | Sharpe=3.70 | PF=1.68 | Stop=25.0% | Hold=23min
t-stat=0.462, p=0.6464 (one or both groups INSUFFICIENT — treat as anecdotal)
WR diff: +4.3% (95% CI: [-17.7%, +26.2%])

**2B: Pre-market news**

**has_premarket**: N=18 **[INSUFFICIENT]** | WR=55.6% | Avg=0.79% | Med=1.43% | Sharpe=4.72 | PF=1.92 | Stop=33.3% | Hold=21min
**no_premarket**: N=79 **[PRELIMINARY]** | WR=62.0% | Avg=0.67% | Med=1.39% | Sharpe=4.05 | PF=1.77 | Stop=24.1% | Hold=23min
t-stat=0.166, p=0.8696 (one or both groups INSUFFICIENT — treat as anecdotal)
WR diff: -6.5% (95% CI: [-31.8%, +18.9%])

**2C: Source type breakdown**

| Source | N | Label | WR | Avg PnL | Sharpe |
|--------|---|-------|-----|---------|--------|
| news_only | 8 | INSUFFICIENT | 25.0% | -0.37% | -2.63 |
| social_only | 14 | INSUFFICIENT | 85.7% | 1.84% | 13.14 |
| both | 3 | INSUFFICIENT | 66.7% | -0.11% | -0.49 |
| neither | 72 | PRELIMINARY | 59.7% | 0.62% | 3.70 |

#### vwap_reclaim

**2A: Has any article**

**has_article**: N=5 **[INSUFFICIENT]** | WR=60.0% | Avg=2.51% | Med=4.32% | Sharpe=3.24 | PF=1.74 | Stop=0.0% | Hold=25min
**no_article**: N=23 **[INSUFFICIENT]** | WR=56.5% | Avg=0.35% | Med=0.93% | Sharpe=0.51 | PF=1.14 | Stop=17.4% | Hold=18min
t-stat=0.362, p=0.7309 (one or both groups INSUFFICIENT — treat as anecdotal)
WR diff: +3.5% (95% CI: [-44.0%, +51.0%])

**2B: Pre-market news**

**has_premarket**: N=3 **[INSUFFICIENT]** | WR=66.7% | Avg=1.54% | Med=4.32% | Sharpe=4.63 | PF=2.02 | Stop=0.0% | Hold=22min
**no_premarket**: N=25 **[INSUFFICIENT]** | WR=56.0% | Avg=0.64% | Med=0.93% | Sharpe=0.88 | PF=1.22 | Stop=16.0% | Hold=19min
t-stat=0.235, p=0.8239 (one or both groups INSUFFICIENT — treat as anecdotal)
WR diff: +10.7% (95% CI: [-46.1%, +67.5%])

**2C: Source type breakdown**

| Source | N | Label | WR | Avg PnL | Sharpe |
|--------|---|-------|-----|---------|--------|
| news_only | 0 | INSUFFICIENT | 0.0% | 0.00% | 0.00 |
| social_only | 4 | INSUFFICIENT | 50.0% | 1.93% | 2.16 |
| both | 1 | INSUFFICIENT | 100.0% | 4.86% | 0.00 |
| neither | 23 | INSUFFICIENT | 56.5% | 0.35% | 0.51 |

---

### Exit Strategy: `target_2`

**Article coverage**: 340/821 trades (41.4%)

#### bull_flag

**2A: Has any article**

**has_article**: N=310 | WR=46.5% | Avg=0.73% | Med=-0.70% | Sharpe=1.87 | PF=1.35 | Stop=33.5% | Hold=32min
**no_article**: N=386 | WR=40.9% | Avg=0.25% | Med=-1.65% | Sharpe=0.65 | PF=1.11 | Stop=39.9% | Hold=31min
t-stat=1.047, p=0.2955
WR diff: +5.5% (95% CI: [-1.9%, +12.9%])

**2B: Pre-market news**

**has_premarket**: N=211 | WR=45.0% | Avg=0.52% | Med=-0.88% | Sharpe=1.37 | PF=1.25 | Stop=32.7% | Hold=34min
**no_premarket**: N=485 | WR=42.7% | Avg=0.44% | Med=-1.51% | Sharpe=1.14 | PF=1.19 | Stop=39.0% | Hold=30min
t-stat=0.176, p=0.8605
WR diff: +2.3% (95% CI: [-5.7%, +10.4%])

**2C: Source type breakdown**

| Source | N | Label | WR | Avg PnL | Sharpe |
|--------|---|-------|-----|---------|--------|
| news_only | 26 | INSUFFICIENT | 61.5% | 2.00% | 5.71 |
| social_only | 241 | RELIABLE | 46.1% | 0.75% | 1.89 |
| both | 35 | PRELIMINARY | 40.0% | -0.10% | -0.26 |
| neither | 394 | RELIABLE | 40.9% | 0.24% | 0.63 |

#### consolidation_breakout

**2A: Has any article**

**has_article**: N=25 **[INSUFFICIENT]** | WR=60.0% | Avg=1.55% | Med=1.14% | Sharpe=6.59 | PF=2.81 | Stop=32.0% | Hold=30min
**no_article**: N=72 **[PRELIMINARY]** | WR=52.8% | Avg=0.69% | Med=0.46% | Sharpe=3.32 | PF=1.68 | Stop=26.4% | Hold=33min
t-stat=1.02, p=0.3144 (one or both groups INSUFFICIENT — treat as anecdotal)
WR diff: +7.2% (95% CI: [-15.2%, +29.6%])

**2B: Pre-market news**

**has_premarket**: N=18 **[INSUFFICIENT]** | WR=55.6% | Avg=1.84% | Med=1.87% | Sharpe=7.35 | PF=3.14 | Stop=33.3% | Hold=27min
**no_premarket**: N=79 **[PRELIMINARY]** | WR=54.4% | Avg=0.70% | Med=0.54% | Sharpe=3.40 | PF=1.70 | Stop=26.6% | Hold=33min
t-stat=1.13, p=0.2703 (one or both groups INSUFFICIENT — treat as anecdotal)
WR diff: +1.1% (95% CI: [-24.3%, +26.6%])

**2C: Source type breakdown**

| Source | N | Label | WR | Avg PnL | Sharpe |
|--------|---|-------|-----|---------|--------|
| news_only | 8 | INSUFFICIENT | 25.0% | -0.47% | -3.63 |
| social_only | 14 | INSUFFICIENT | 78.6% | 2.87% | 11.49 |
| both | 3 | INSUFFICIENT | 66.7% | 0.79% | 2.74 |
| neither | 72 | PRELIMINARY | 52.8% | 0.69% | 3.32 |

#### vwap_reclaim

**2A: Has any article**

**has_article**: N=5 **[INSUFFICIENT]** | WR=60.0% | Avg=6.77% | Med=4.32% | Sharpe=5.23 | PF=2.98 | Stop=0.0% | Hold=38min
**no_article**: N=23 **[INSUFFICIENT]** | WR=52.2% | Avg=-0.90% | Med=0.23% | Sharpe=-1.49 | PF=0.67 | Stop=21.7% | Hold=27min
t-stat=0.815, p=0.457 (one or both groups INSUFFICIENT — treat as anecdotal)
WR diff: +7.8% (95% CI: [-39.7%, +55.4%])

**2B: Pre-market news**

**has_premarket**: N=3 **[INSUFFICIENT]** | WR=66.7% | Avg=1.74% | Med=4.32% | Sharpe=5.05 | PF=2.15 | Stop=0.0% | Hold=39min
**no_premarket**: N=25 **[INSUFFICIENT]** | WR=52.0% | Avg=0.31% | Med=0.23% | Sharpe=0.39 | PF=1.10 | Stop=20.0% | Hold=28min
t-stat=0.351, p=0.7391 (one or both groups INSUFFICIENT — treat as anecdotal)
WR diff: +14.7% (95% CI: [-42.2%, +71.5%])

**2C: Source type breakdown**

| Source | N | Label | WR | Avg PnL | Sharpe |
|--------|---|-------|-----|---------|--------|
| news_only | 0 | INSUFFICIENT | 0.0% | 0.00% | 0.00 |
| social_only | 4 | INSUFFICIENT | 50.0% | 7.10% | 4.75 |
| both | 1 | INSUFFICIENT | 100.0% | 5.47% | 0.00 |
| neither | 23 | INSUFFICIENT | 52.2% | -0.90% | -1.49 |

---

## Layer 3: sentiment_daily Analysis (2025+ Trades Only)

### Exit Strategy: `target_1`

**Sentiment coverage**: 18/821 trades (2.2%)

> **Note**: Coverage is below 5%. All results in this section should be treated as anecdotal at best. The Cameron universe (micro-cap gappers) is largely below the media radar.

#### 3A: Has catalyst (mentions > 0)

**mentions>0**: N=18 **[INSUFFICIENT]** | WR=38.9% | Avg=-0.38% | Med=-1.88% | Sharpe=-1.30 | PF=0.83 | Stop=33.3% | Hold=27min
**no_mentions**: N=0

#### 3B: Sentiment polarity

| Polarity | N | Label | WR | Avg PnL | Sharpe |
|----------|---|-------|-----|---------|--------|
| negative | 0 | INSUFFICIENT | 0.0% | 0.00% | 0.00 |
| neutral | 3 | INSUFFICIENT | 66.7% | 1.59% | 5.16 |
| positive | 15 | INSUFFICIENT | 33.3% | -0.77% | -2.63 |
| no_data | 803 | RELIABLE | 53.7% | 0.38% | 1.27 |

---

### Exit Strategy: `target_2`

**Sentiment coverage**: 18/821 trades (2.2%)

> **Note**: Coverage is below 5%. All results in this section should be treated as anecdotal at best. The Cameron universe (micro-cap gappers) is largely below the media radar.

#### 3A: Has catalyst (mentions > 0)

**mentions>0**: N=18 **[INSUFFICIENT]** | WR=38.9% | Avg=0.41% | Med=-1.88% | Sharpe=1.09 | PF=1.18 | Stop=33.3% | Hold=34min
**no_mentions**: N=0

#### 3B: Sentiment polarity

| Polarity | N | Label | WR | Avg PnL | Sharpe |
|----------|---|-------|-----|---------|--------|
| negative | 0 | INSUFFICIENT | 0.0% | 0.00% | 0.00 |
| neutral | 3 | INSUFFICIENT | 66.7% | 2.27% | 6.22 |
| positive | 15 | INSUFFICIENT | 33.3% | 0.04% | 0.10 |
| no_data | 803 | RELIABLE | 45.2% | 0.52% | 1.34 |

---

## Layer 4: Summary & Recommendations

### Target 1 vs Target 2 — Head-to-Head

Same trades, different exit rules:

| Metric | target_1 | target_2 |
|--------|----------|----------|
| N | 2278 | 2278 |
| wr | 53.6% | 45.3% |
| avg_pnl | 0.32% | 0.49% |
| median_pnl | 0.63% | -0.72% |
| sharpe | 1.03 | 1.27 |
| pf | 1.17 | 1.24 |
| stop_pct | 31.2% | 35.8% |
| avg_hold | 23 | 30 |

### By Pattern: target_1 vs target_2

| Pattern | Exit | N | WR | Avg PnL | Sharpe | PF |
|---------|------|---|-----|---------|--------|-----|
| bull_flag | t1 | 1879 | 52.6% | 0.19% | 0.65 | 1.10 |
| bull_flag | t2 | 1879 | 44.2% | 0.40% | 1.06 | 1.18 |
| consolidation_breakout | t1 | 301 | 59.5% | 0.49% | 2.80 | 1.48 |
| consolidation_breakout | t2 | 301 | 49.8% | 0.47% | 2.09 | 1.39 |
| vwap_reclaim | t1 | 98 | 56.1% | 2.20% | 3.26 | 1.99 |
| vwap_reclaim | t2 | 98 | 54.1% | 2.28% | 3.04 | 2.00 |

### Statistically Significant Findings (p < 0.05, N >= 30)

None found.


### Directional Findings (p < 0.20, N >= 30, worth monitoring)

None found.


### Recommendations

**Best combination**: `vwap_reclaim` + `target_1` (N=98, Sharpe=3.26, WR=56.1%, Avg=2.20%)

### What Does NOT Work

