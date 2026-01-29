# FL3_V2 Deployment Guide

## Quick Deploy (All Services)

```powershell
cd C:\Users\levir\Documents\FL3_V2
.\deploy\deploy.ps1 -All
```

## Individual Services

### 1. Firehose (Always-on websocket service)
```powershell
.\deploy\deploy.ps1 -Service firehose
```
- Runs continuously during market hours
- Connects to Polygon T.* websocket
- Detects UOA and stores triggers

### 2. TA Pipeline (Scheduled every 5 min)
```powershell
.\deploy\deploy.ps1 -Service ta-pipeline
```
- Runs every 5 minutes: 9:00 AM - 4:00 PM ET
- Fetches bars from Alpaca
- Calculates RSI, ATR, VWAP, SMA, EMA

### 3. Baseline Refresh (Daily after close)
```powershell
.\deploy\deploy.ps1 -Service baseline-refresh
```
- Runs daily at 4:30 PM ET
- Cleans up old data (>30 days)
- Generates health report

---

## Manual Deployment Steps

If the script fails, follow these manual steps:

### Step 1: Build Docker Image

```powershell
cd C:\Users\levir\Documents\FL3_V2

# Build
docker build -t us-west1-docker.pkg.dev/fl3-v2-prod/fl3-v2-images/fl3-v2:latest .

# Push
docker push us-west1-docker.pkg.dev/fl3-v2-prod/fl3-v2-images/fl3-v2:latest
```

### Step 2: Deploy Firehose Service

```powershell
gcloud run deploy fl3-v2-firehose `
    --image us-west1-docker.pkg.dev/fl3-v2-prod/fl3-v2-images/fl3-v2:latest `
    --region us-west1 `
    --platform managed `
    --no-allow-unauthenticated `
    --service-account fl3-v2-cloudrun@fl3-v2-prod.iam.gserviceaccount.com `
    --set-secrets "DATABASE_URL=DATABASE_URL:latest,POLYGON_API_KEY=POLYGON_API_KEY:latest" `
    --memory 2Gi `
    --cpu 1 `
    --timeout 3600 `
    --min-instances 1 `
    --max-instances 1 `
    --command "python" `
    --args "-m,scripts.firehose_main"
```

### Step 3: Create TA Pipeline Job

```powershell
# Create job
gcloud run jobs create fl3-v2-ta-pipeline `
    --image us-west1-docker.pkg.dev/fl3-v2-prod/fl3-v2-images/fl3-v2:latest `
    --region us-west1 `
    --service-account fl3-v2-cloudrun@fl3-v2-prod.iam.gserviceaccount.com `
    --set-secrets "DATABASE_URL=DATABASE_URL:latest,ALPACA_API_KEY=ALPACA_API_KEY:latest,ALPACA_SECRET_KEY=ALPACA_SECRET_KEY:latest" `
    --memory 1Gi `
    --task-timeout 300 `
    --command "python" `
    --args "-m,scripts.ta_pipeline_v2,--once"

# Create scheduler
gcloud scheduler jobs create http fl3-v2-ta-pipeline-scheduler `
    --location us-west1 `
    --schedule "*/5 9-16 * * 1-5" `
    --time-zone "America/New_York" `
    --uri "https://us-west1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/fl3-v2-prod/jobs/fl3-v2-ta-pipeline:run" `
    --http-method POST `
    --oauth-service-account-email fl3-v2-scheduler@fl3-v2-prod.iam.gserviceaccount.com
```

### Step 4: Create Baseline Refresh Job

```powershell
# Create job
gcloud run jobs create fl3-v2-baseline-refresh `
    --image us-west1-docker.pkg.dev/fl3-v2-prod/fl3-v2-images/fl3-v2:latest `
    --region us-west1 `
    --service-account fl3-v2-cloudrun@fl3-v2-prod.iam.gserviceaccount.com `
    --set-secrets "DATABASE_URL=DATABASE_URL:latest" `
    --memory 512Mi `
    --task-timeout 600 `
    --command "python" `
    --args "-m,scripts.refresh_baselines"

# Create scheduler
gcloud scheduler jobs create http fl3-v2-baseline-refresh-scheduler `
    --location us-west1 `
    --schedule "30 16 * * 1-5" `
    --time-zone "America/New_York" `
    --uri "https://us-west1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/fl3-v2-prod/jobs/fl3-v2-baseline-refresh:run" `
    --http-method POST `
    --oauth-service-account-email fl3-v2-scheduler@fl3-v2-prod.iam.gserviceaccount.com
```

---

## Timezone Schedule Reference

All schedules use `America/New_York` (ET):

| Job | Schedule (ET) | Schedule (PST) | Cron |
|-----|---------------|----------------|------|
| TA Pipeline | 9:00 AM - 4:00 PM every 5 min | 6:00 AM - 1:00 PM | `*/5 9-16 * * 1-5` |
| Baseline Refresh | 4:30 PM daily | 1:30 PM | `30 16 * * 1-5` |
| Firehose | Always-on (min-instances=1) | - | N/A |

---

## Verification Commands

```powershell
# Check deployed services
gcloud run services list --region us-west1

# Check jobs
gcloud run jobs list --region us-west1

# Check schedulers
gcloud scheduler jobs list --location us-west1

# View firehose logs
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=fl3-v2-firehose" --limit 50

# Manually trigger TA pipeline
gcloud run jobs execute fl3-v2-ta-pipeline --region us-west1
```

---

## Troubleshooting

### Docker build fails
```powershell
# Ensure Docker is running
docker info

# Clear Docker cache if needed
docker builder prune
```

### Authentication issues
```powershell
# Re-authenticate
gcloud auth login
gcloud auth configure-docker us-west1-docker.pkg.dev
```

### Service account missing
```powershell
# Check service accounts
gcloud iam service-accounts list

# If missing, recreate
gcloud iam service-accounts create fl3-v2-cloudrun --display-name="FL3 V2 Cloud Run"
```
