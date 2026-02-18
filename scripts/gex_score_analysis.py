"""GEX Score Impact Analysis: Would GEX penalties have improved outcomes?"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import psycopg2

conn = psycopg2.connect("postgresql://FR3_User:di7UtK8E1%5B%5B137%40F@127.0.0.1:5433/fl3")
cur = conn.cursor()

cur.execute("""
WITH trades_gex AS (
    SELECT
        pt.symbol,
        pt.entry_time::date as trade_date,
        pt.signal_score,
        ROUND(pt.entry_price::numeric, 2) as entry,
        ROUND(pt.exit_price::numeric, 2) as exit_px,
        ROUND(pt.pnl_pct::numeric, 3) as pnl_pct,
        ROUND(pt.pnl::numeric, 2) as pnl_dollars,
        pt.exit_reason,
        ROUND(g.net_gex::numeric, 0) as net_gex,
        ROUND(g.gamma_flip_level::numeric, 2) as flip,
        ROUND(g.call_wall_strike::numeric, 2) as cwall,
        ROUND(g.put_wall_strike::numeric, 2) as pwall,
        g.contracts_analyzed as contracts,
        CASE WHEN g.gamma_flip_level IS NOT NULL AND g.spot_price > 0
             THEN ROUND(((pt.entry_price - g.gamma_flip_level) / g.spot_price * 100)::numeric, 2)
             ELSE NULL END as flip_dist_pct,
        CASE WHEN g.call_wall_strike > 0 AND pt.entry_price > 0
             THEN ROUND(((g.call_wall_strike - pt.entry_price) / pt.entry_price * 100)::numeric, 2)
             ELSE NULL END as room_cwall_pct,
        CASE WHEN g.put_wall_strike > 0 AND pt.entry_price > 0
             THEN ROUND(((pt.entry_price - g.put_wall_strike) / pt.entry_price * 100)::numeric, 2)
             ELSE NULL END as dist_above_pwall_pct
    FROM paper_trades_log pt
    JOIN gex_metrics_snapshot g
        ON g.symbol = pt.symbol
        AND g.snapshot_ts::date = pt.entry_time::date
    WHERE pt.exit_price IS NOT NULL
      AND pt.exit_reason != 'crash_recovery'
      AND pt.signal_score >= 10
)
SELECT * FROM trades_gex
ORDER BY trade_date, signal_score DESC, pnl_pct ASC
""")

rows = cur.fetchall()

def get_gex_penalties(r):
    """Return list of (flag, penalty_points) tuples."""
    penalties = []
    flip_dist = float(r[13]) if r[13] is not None else None
    room_cwall = float(r[14]) if r[14] is not None else None
    contracts = r[12]
    dist_pwall = float(r[15]) if r[15] is not None else None
    
    # Penalty 1: At or above call wall -> -2 pts
    if room_cwall is not None and room_cwall <= 0:
        penalties.append(("ABOVE_CWALL", -2))
    # Penalty 2: Near call wall (0-2%) -> -1 pt
    elif room_cwall is not None and room_cwall < 2:
        penalties.append(("NEAR_CWALL", -1))
    
    # Penalty 3: Near gamma flip (+/- 2%) -> -1 pt
    if flip_dist is not None and abs(flip_dist) < 2:
        penalties.append(("NEAR_FLIP", -1))
    
    # Penalty 4: Low options liquidity -> -1 pt
    if contracts is not None and contracts < 50:
        penalties.append(("LOW_OI_LIQ", -1))
    
    # Penalty 5: At or below put wall -> -1 pt
    if dist_pwall is not None and dist_pwall <= 0:
        penalties.append(("BELOW_PWALL", -1))
    
    return penalties

# ============================================================
# DETAILED PER-TRADE VIEW
# ============================================================
print("=" * 140)
print("PER-TRADE GEX PENALTY ANALYSIS (score >= 10, excl. crash_recovery)")
print("=" * 140)

print(f"\n{'Date':<11} {'Symbol':<7} {'Scr':>3} {'AdjScr':>6} {'PnL%':>7} {'PnL$':>8} {'FlipD%':>7} {'RmCW%':>6} {'Ctrct':>5} {'W/L':>3}  {'GEX Penalties':<40} {'Would Filter?'}")
print("-" * 150)

all_trades = []
for r in rows:
    pnl_pct = float(r[5]) if r[5] else 0
    pnl_dollars = float(r[6]) if r[6] else 0
    score = r[2] if r[2] else 0
    wl = "W" if pnl_pct > 0 else ("L" if pnl_pct < 0 else "-")
    
    penalties = get_gex_penalties(r)
    total_penalty = sum(p[1] for p in penalties)
    adj_score = score + total_penalty
    
    penalty_str = ", ".join([f"{p[0]}({p[1]:+d})" for p in penalties]) if penalties else "none"
    
    # Would this have been filtered at threshold 10?
    would_filter = "YES - BLOCKED" if adj_score < 10 else "no"
    
    marker = " ***" if penalties and pnl_pct < 0 else ""
    
    all_trades.append({
        'symbol': r[0], 'date': r[1], 'score': score, 'adj_score': adj_score,
        'pnl_pct': pnl_pct, 'pnl_dollars': pnl_dollars, 'wl': wl,
        'penalties': penalties, 'total_penalty': total_penalty,
        'would_filter': adj_score < 10, 'flip_dist': r[13], 'room_cwall': r[14],
        'contracts': r[12]
    })
    
    print(f"{str(r[1]):<11} {r[0]:<7} {score:>3} {adj_score:>6} {r[5]:>7} {r[6]:>8} {str(r[13] or 'N/A'):>7} {str(r[14] or 'N/A'):>6} {str(r[12] or ''):>5} {wl:>3}  {penalty_str:<40} {would_filter}{marker}")

# ============================================================
# SUMMARY BY DATE
# ============================================================
print("\n" + "=" * 140)
print("SUMMARY BY DATE: Actual vs GEX-Adjusted Outcomes")
print("=" * 140)

from collections import defaultdict
daily = defaultdict(lambda: {
    'trades': 0, 'wins': 0, 'losses': 0, 'pnl': 0,
    'adj_trades': 0, 'adj_wins': 0, 'adj_losses': 0, 'adj_pnl': 0,
    'blocked_winners': 0, 'blocked_losers': 0,
    'blocked_win_pnl': 0, 'blocked_loss_pnl': 0
})

for t in all_trades:
    d = daily[t['date']]
    d['trades'] += 1
    d['pnl'] += t['pnl_dollars']
    if t['pnl_pct'] > 0: d['wins'] += 1
    elif t['pnl_pct'] < 0: d['losses'] += 1
    
    if t['would_filter']:
        if t['pnl_pct'] > 0:
            d['blocked_winners'] += 1
            d['blocked_win_pnl'] += t['pnl_dollars']
        elif t['pnl_pct'] < 0:
            d['blocked_losers'] += 1
            d['blocked_loss_pnl'] += t['pnl_dollars']
    else:
        d['adj_trades'] += 1
        d['adj_pnl'] += t['pnl_dollars']
        if t['pnl_pct'] > 0: d['adj_wins'] += 1
        elif t['pnl_pct'] < 0: d['adj_losses'] += 1

print(f"\n{'Date':<11} {'Trds':>4} {'W':>3} {'L':>3} {'PnL$':>9} {'WinR%':>6} | {'AdjTr':>5} {'AW':>3} {'AL':>3} {'AdjPnL$':>9} {'AWR%':>6} | {'BlkW':>4} {'BlkL':>4} {'BlkW$':>8} {'BlkL$':>8} | {'Delta$':>8}")
print("-" * 140)

total_actual_pnl = 0
total_adj_pnl = 0

for date in sorted(daily.keys()):
    d = daily[date]
    wr = round(d['wins']/d['trades']*100, 1) if d['trades'] > 0 else 0
    awr = round(d['adj_wins']/d['adj_trades']*100, 1) if d['adj_trades'] > 0 else 0
    delta = d['adj_pnl'] - d['pnl']
    total_actual_pnl += d['pnl']
    total_adj_pnl += d['adj_pnl']
    
    print(f"{str(date):<11} {d['trades']:>4} {d['wins']:>3} {d['losses']:>3} {d['pnl']:>9.2f} {wr:>5.1f}% | {d['adj_trades']:>5} {d['adj_wins']:>3} {d['adj_losses']:>3} {d['adj_pnl']:>9.2f} {awr:>5.1f}% | {d['blocked_winners']:>4} {d['blocked_losers']:>4} {d['blocked_win_pnl']:>8.2f} {d['blocked_loss_pnl']:>8.2f} | {delta:>+8.2f}")

print("-" * 140)
tot = {'trades': 0, 'wins': 0, 'losses': 0, 'adj_trades': 0, 'adj_wins': 0, 'adj_losses': 0,
       'blocked_winners': 0, 'blocked_losers': 0, 'blocked_win_pnl': 0, 'blocked_loss_pnl': 0}
for d in daily.values():
    for k in tot: tot[k] += d[k]

wr = round(tot['wins']/tot['trades']*100, 1) if tot['trades'] > 0 else 0
awr = round(tot['adj_wins']/tot['adj_trades']*100, 1) if tot['adj_trades'] > 0 else 0
delta_total = total_adj_pnl - total_actual_pnl
print(f"{'TOTAL':<11} {tot['trades']:>4} {tot['wins']:>3} {tot['losses']:>3} {total_actual_pnl:>9.2f} {wr:>5.1f}% | {tot['adj_trades']:>5} {tot['adj_wins']:>3} {tot['adj_losses']:>3} {total_adj_pnl:>9.2f} {awr:>5.1f}% | {tot['blocked_winners']:>4} {tot['blocked_losers']:>4} {tot['blocked_win_pnl']:>8.2f} {tot['blocked_loss_pnl']:>8.2f} | {delta_total:>+8.2f}")

# ============================================================
# WHAT WOULD HAVE BEEN BLOCKED?
# ============================================================
print("\n" + "=" * 140)
print("BLOCKED TRADES DETAIL (adj_score < 10)")
print("=" * 140)

blocked = [t for t in all_trades if t['would_filter']]
if blocked:
    print(f"\n{'Date':<11} {'Symbol':<7} {'Scr':>3} {'Adj':>3} {'PnL%':>7} {'PnL$':>8} {'W/L':>3}  {'Penalties'}")
    print("-" * 90)
    for t in blocked:
        penalty_str = ", ".join([f"{p[0]}({p[1]:+d})" for p in t['penalties']])
        print(f"{str(t['date']):<11} {t['symbol']:<7} {t['score']:>3} {t['adj_score']:>3} {t['pnl_pct']:>7.3f} {t['pnl_dollars']:>8.2f} {t['wl']:>3}  {penalty_str}")
    
    blocked_w = [t for t in blocked if t['wl'] == 'W']
    blocked_l = [t for t in blocked if t['wl'] == 'L']
    print(f"\nBlocked winners: {len(blocked_w)} (${sum(t['pnl_dollars'] for t in blocked_w):.2f} lost profit)")
    print(f"Blocked losers:  {len(blocked_l)} (${sum(t['pnl_dollars'] for t in blocked_l):.2f} avoided losses)")
    print(f"Net impact:      ${sum(t['pnl_dollars'] for t in blocked_l) - sum(t['pnl_dollars'] for t in blocked_w):.2f} improvement (negative = saved)")
else:
    print("\nNo trades would have been blocked with these penalties.")

# ============================================================
# ALTERNATIVE: What if we used GEX as bonus instead of penalty?
# Trades below flip get +1, far below flip get +2
# ============================================================
print("\n" + "=" * 140)
print("ALTERNATIVE SCORING: GEX BONUS for favorable conditions")
print("(far below flip = +2, below flip = +1, above cwall = -2, near cwall = -1)")
print("=" * 140)

cur.execute("""
WITH trades_gex AS (
    SELECT
        pt.symbol,
        pt.entry_time::date as trade_date,
        pt.signal_score,
        ROUND(pt.pnl_pct::numeric, 3) as pnl_pct,
        ROUND(pt.pnl::numeric, 2) as pnl_dollars,
        CASE WHEN g.gamma_flip_level IS NOT NULL AND g.spot_price > 0
             THEN ((pt.entry_price - g.gamma_flip_level) / g.spot_price * 100)
             ELSE NULL END as flip_dist_pct,
        CASE WHEN g.call_wall_strike > 0 AND pt.entry_price > 0
             THEN ((g.call_wall_strike - pt.entry_price) / pt.entry_price * 100)
             ELSE NULL END as room_cwall_pct,
        g.contracts_analyzed as contracts
    FROM paper_trades_log pt
    JOIN gex_metrics_snapshot g
        ON g.symbol = pt.symbol
        AND g.snapshot_ts::date = pt.entry_time::date
    WHERE pt.exit_price IS NOT NULL
      AND pt.exit_reason != 'crash_recovery'
      AND pt.signal_score >= 10
)
SELECT 
    signal_score as orig_score,
    pnl_pct,
    pnl_dollars,
    ROUND(flip_dist_pct::numeric, 2) as flip_dist,
    ROUND(room_cwall_pct::numeric, 2) as room_cwall,
    -- Compute adjusted score
    signal_score 
        + CASE WHEN flip_dist_pct IS NOT NULL AND flip_dist_pct < -2 THEN 2
               WHEN flip_dist_pct IS NOT NULL AND flip_dist_pct < 0 THEN 1
               ELSE 0 END
        + CASE WHEN room_cwall_pct IS NOT NULL AND room_cwall_pct <= 0 THEN -2
               WHEN room_cwall_pct IS NOT NULL AND room_cwall_pct < 2 THEN -1
               ELSE 0 END
        + CASE WHEN flip_dist_pct IS NOT NULL AND abs(flip_dist_pct) < 1 THEN -1
               ELSE 0 END
        + CASE WHEN contracts < 50 THEN -1 ELSE 0 END
    as adj_score,
    symbol,
    trade_date
FROM trades_gex
ORDER BY adj_score DESC, pnl_pct DESC
""")

rows2 = cur.fetchall()
print(f"\n{'Date':<11} {'Symbol':<7} {'Orig':>4} {'Adj':>4} {'PnL%':>7} {'PnL$':>8} {'FlipD%':>7} {'RmCW%':>7} {'W/L':>3}")
print("-" * 80)
for r in rows2:
    pnl = float(r[1]) if r[1] else 0
    wl = "W" if pnl > 0 else ("L" if pnl < 0 else "-")
    print(f"{str(r[7]):<11} {r[6]:<7} {r[0]:>4} {r[5]:>4} {r[1]:>7} {r[2]:>8} {str(r[3] or 'N/A'):>7} {str(r[4] or 'N/A'):>7} {wl:>3}")

# Correlation: adjusted score vs PnL
print("\n--- Adjusted Score vs PnL Correlation ---")
cur.execute("""
WITH trades_gex AS (
    SELECT
        pt.signal_score,
        pt.pnl_pct,
        pt.pnl,
        CASE WHEN g.gamma_flip_level IS NOT NULL AND g.spot_price > 0
             THEN ((pt.entry_price - g.gamma_flip_level) / g.spot_price * 100)
             ELSE NULL END as flip_dist_pct,
        CASE WHEN g.call_wall_strike > 0 AND pt.entry_price > 0
             THEN ((g.call_wall_strike - pt.entry_price) / pt.entry_price * 100)
             ELSE NULL END as room_cwall_pct,
        g.contracts_analyzed as contracts
    FROM paper_trades_log pt
    JOIN gex_metrics_snapshot g
        ON g.symbol = pt.symbol
        AND g.snapshot_ts::date = pt.entry_time::date
    WHERE pt.exit_price IS NOT NULL
      AND pt.exit_reason != 'crash_recovery'
      AND pt.signal_score >= 10
),
scored AS (
    SELECT 
        signal_score as orig,
        signal_score 
            + CASE WHEN flip_dist_pct < -2 THEN 2 WHEN flip_dist_pct < 0 THEN 1 ELSE 0 END
            + CASE WHEN room_cwall_pct <= 0 THEN -2 WHEN room_cwall_pct < 2 THEN -1 ELSE 0 END
            + CASE WHEN abs(flip_dist_pct) < 1 THEN -1 ELSE 0 END
            + CASE WHEN contracts < 50 THEN -1 ELSE 0 END
        as adj,
        pnl_pct, pnl
    FROM trades_gex
    WHERE flip_dist_pct IS NOT NULL
)
SELECT 
    adj as adj_score,
    COUNT(*) as trades,
    ROUND(AVG(pnl_pct)::numeric, 3) as avg_pnl_pct,
    COUNT(*) FILTER (WHERE pnl_pct > 0) as wins,
    COUNT(*) FILTER (WHERE pnl_pct <= 0) as losses,
    ROUND(COUNT(*) FILTER (WHERE pnl_pct > 0)::numeric / NULLIF(COUNT(*), 0) * 100, 1) as win_rate,
    ROUND(SUM(pnl)::numeric, 2) as total_pnl
FROM scored
GROUP BY adj
ORDER BY adj
""")

rows3 = cur.fetchall()
print(f"\n{'AdjScore':>8} {'Trades':>6} {'AvgPnL%':>8} {'Wins':>5} {'Loss':>5} {'WinR%':>6} {'TotalPnL$':>10}")
print("-" * 60)
for r in rows3:
    print(f"{r[0]:>8} {r[1]:>6} {r[2]:>8} {r[3]:>5} {r[4]:>5} {r[5]:>6} {r[6]:>10}")

# Same for original score
print("\n--- Original Score vs PnL (for comparison) ---")
cur.execute("""
SELECT 
    pt.signal_score,
    COUNT(*) as trades,
    ROUND(AVG(pt.pnl_pct)::numeric, 3) as avg_pnl_pct,
    COUNT(*) FILTER (WHERE pt.pnl_pct > 0) as wins,
    COUNT(*) FILTER (WHERE pt.pnl_pct <= 0) as losses,
    ROUND(COUNT(*) FILTER (WHERE pt.pnl_pct > 0)::numeric / NULLIF(COUNT(*), 0) * 100, 1) as win_rate,
    ROUND(SUM(pt.pnl)::numeric, 2) as total_pnl
FROM paper_trades_log pt
WHERE pt.exit_price IS NOT NULL
  AND pt.exit_reason != 'crash_recovery'
  AND pt.signal_score >= 10
GROUP BY pt.signal_score
ORDER BY pt.signal_score
""")

rows4 = cur.fetchall()
print(f"\n{'OrigScore':>9} {'Trades':>6} {'AvgPnL%':>8} {'Wins':>5} {'Loss':>5} {'WinR%':>6} {'TotalPnL$':>10}")
print("-" * 60)
for r in rows4:
    print(f"{r[0]:>9} {r[1]:>6} {r[2]:>8} {r[3]:>5} {r[4]:>5} {r[5]:>6} {r[6]:>10}")

conn.close()
