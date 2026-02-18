# PROD-1: Stock WebSocket Deployment Plan

## Overview
Deploy real-time stock price WebSocket with graceful fallback to REST polling if WebSocket fails.

## Deployment Date
**Monday, February 2, 2026**

---

## ✅ FULLY AUTOMATED

The deployment is now fully automated via Cloud Scheduler:

| Component | Schedule | Description |
|-----------|----------|-------------|
| `polygon-ws-premarket-test` | 9:00 AM ET, Mon-Fri | Triggers orchestrator |
| `premarket-orchestrator` | On trigger | Tests WebSocket + auto-deploys |

### What Happens Automatically

```
9:00 AM ET ─► Cloud Scheduler triggers
                    │
                    ▼
            premarket-orchestrator runs
                    │
                    ▼
            1. Test WebSocket connectivity
            2. Subscribe to SPY, AAPL, TSLA
            3. Wait 20s for data
                    │
            ┌───────┴───────┐
            │               │
        ≥5 messages     <5 messages
            │               │
            ▼               ▼
    Deploy v5           Keep current
    (WebSocket)         (REST fallback)
            │               │
            ▼               ▼
        Log success     Log warning
```

### No Manual Action Required

The system will:
- **If WebSocket works**: Deploy `paper-trading:v5` with real-time prices
- **If WebSocket fails**: Keep current deployment, graceful degradation kicks in

---

## Manual Commands (if needed)

### Check Orchestrator Results
```bash
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=premarket-orchestrator" \
  --project=fl3-v2-prod --limit=50 --freshness=30m --format="value(textPayload)"
```

### Manual Test Run
```bash
gcloud run jobs execute premarket-orchestrator --region=us-west1 --project=fl3-v2-prod --wait
```

### Force Deploy v5 (WebSocket)
```bash
gcloud run jobs update paper-trading-prod \
  --image=us-west1-docker.pkg.dev/fl3-v2-prod/fl3-v2-images/paper-trading:v5 \
  --region=us-west1 --project=fl3-v2-prod
```

### Rollback to v1 (REST only)
```bash
gcloud run jobs update paper-trading-prod \
  --image=us-west1-docker.pkg.dev/fl3-v2-prod/fl3-v2-images/paper-trading:v1 \
  --region=us-west1 --project=fl3-v2-prod
```

---

## Graceful Degradation Design

### How It Works

```
Startup
   │
   ▼
Start Stock WebSocket ──► Success? ──► YES ──► Use WebSocket for prices
   │                                              │
   │                                              ▼
   │                                    WebSocket disconnects?
   │                                              │
   │                                              ▼
   │                                    Auto-reconnect (3 attempts)
   │                                              │
   │                                              ▼
   NO ◄─────────────────────────────── Still failing?
   │
   ▼
Fall back to REST polling (existing logic)
   │
   ▼
Log warning: "WebSocket unavailable, using REST fallback"
```

### Code Changes Required

1. **Config flag** to enable/disable WebSocket
2. **Fallback detection** in position manager
3. **Dual-mode price fetching** - try WebSocket first, REST if unavailable

---

## Implementation Checklist

- [x] StockPriceMonitor class created
- [x] Integration in paper_trading/main.py
- [x] Test script created
- [ ] Add USE_STOCK_WEBSOCKET config flag
- [ ] Add graceful fallback in position_manager
- [ ] Add pre-market health check
- [ ] Cloud Scheduler job for Monday 9:00 AM test

---

## Rollback Plan

If issues occur during market hours:

1. **Immediate**: WebSocket auto-disables, REST takes over
2. **Manual rollback** (if needed):
```bash
gcloud run jobs update paper-trading-prod \
  --image=us-west1-docker.pkg.dev/fl3-v2-prod/fl3-v2-images/paper-trading:v1 \
  --region=us-west1 --project=fl3-v2-prod
```

---

## Monitoring

### Key Metrics to Watch
- `stock_monitor.metrics.trades_received` - Should increase during market hours
- `stock_monitor.metrics.reconnect_count` - Should stay low (<3)
- Hard stop execution time - Should be <1s with WebSocket vs ~30s with REST

### Log Patterns

**Good:**
```
Stock price WebSocket connected
Subscribed to 3 symbols: ['AAPL', 'TSLA', 'SPY']
```

**Warning (fallback active):**
```
Stock price WebSocket failed to connect - using REST fallback
```

**Error (needs investigation):**
```
WebSocket authentication failed
Connection refused
```
