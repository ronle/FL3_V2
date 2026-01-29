# FL3_V2 Deployment Script
# Deploys services to GCP Cloud Run
#
# Usage:
#   .\deploy\deploy.ps1 -Service firehose
#   .\deploy\deploy.ps1 -Service ta-pipeline
#   .\deploy\deploy.ps1 -Service baseline-refresh
#   .\deploy\deploy.ps1 -All

param(
    [Parameter(Mandatory=$false)]
    [ValidateSet("firehose", "ta-pipeline", "baseline-refresh")]
    [string]$Service,

    [Parameter(Mandatory=$false)]
    [switch]$All,

    [Parameter(Mandatory=$false)]
    [string]$Tag = "latest"
)

# Configuration
$PROJECT = "fl3-v2-prod"
$REGION = "us-west1"
$REGISTRY = "us-west1-docker.pkg.dev/$PROJECT/fl3-v2-images"
$IMAGE = "$REGISTRY/fl3-v2"

# Colors for output
function Write-Step { param($msg) Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Success { param($msg) Write-Host "[OK] $msg" -ForegroundColor Green }
function Write-Error { param($msg) Write-Host "[ERROR] $msg" -ForegroundColor Red }

# Set project
Write-Step "Setting GCP project to $PROJECT"
gcloud config set project $PROJECT

# Build and push Docker image
function Build-Image {
    Write-Step "Building Docker image..."

    # Navigate to project root
    Push-Location $PSScriptRoot\..

    try {
        # Build
        docker build -t "${IMAGE}:${Tag}" .
        if ($LASTEXITCODE -ne 0) { throw "Docker build failed" }

        # Push
        Write-Step "Pushing image to Artifact Registry..."
        docker push "${IMAGE}:${Tag}"
        if ($LASTEXITCODE -ne 0) { throw "Docker push failed" }

        Write-Success "Image pushed: ${IMAGE}:${Tag}"
    }
    finally {
        Pop-Location
    }
}

# Deploy Firehose Service (always-on)
function Deploy-Firehose {
    Write-Step "Deploying Firehose service..."

    gcloud run deploy fl3-v2-firehose `
        --image "${IMAGE}:${Tag}" `
        --region $REGION `
        --platform managed `
        --no-allow-unauthenticated `
        --service-account "fl3-v2-cloudrun@${PROJECT}.iam.gserviceaccount.com" `
        --set-secrets "DATABASE_URL=DATABASE_URL:latest,POLYGON_API_KEY=POLYGON_API_KEY:latest" `
        --memory 2Gi `
        --cpu 1 `
        --timeout 3600 `
        --min-instances 1 `
        --max-instances 1 `
        --command "python" `
        --args "-m,scripts.firehose_main"

    if ($LASTEXITCODE -eq 0) {
        Write-Success "Firehose deployed"
    } else {
        Write-Error "Firehose deployment failed"
    }
}

# Deploy TA Pipeline Job
function Deploy-TAPipeline {
    Write-Step "Deploying TA Pipeline job..."

    # Create Cloud Run Job
    gcloud run jobs create fl3-v2-ta-pipeline `
        --image "${IMAGE}:${Tag}" `
        --region $REGION `
        --service-account "fl3-v2-cloudrun@${PROJECT}.iam.gserviceaccount.com" `
        --set-secrets "DATABASE_URL=DATABASE_URL:latest,ALPACA_API_KEY=ALPACA_API_KEY:latest,ALPACA_SECRET_KEY=ALPACA_SECRET_KEY:latest" `
        --memory 1Gi `
        --cpu 1 `
        --task-timeout 300 `
        --max-retries 1 `
        --command "python" `
        --args "-m,scripts.ta_pipeline_v2,--once" `
        2>$null

    # If job exists, update it
    if ($LASTEXITCODE -ne 0) {
        gcloud run jobs update fl3-v2-ta-pipeline `
            --image "${IMAGE}:${Tag}" `
            --region $REGION
    }

    # Create Cloud Scheduler (every 5 min during market hours ET)
    Write-Step "Creating Cloud Scheduler for TA Pipeline..."

    gcloud scheduler jobs create http fl3-v2-ta-pipeline-scheduler `
        --location $REGION `
        --schedule "*/5 9-16 * * 1-5" `
        --time-zone "America/New_York" `
        --uri "https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT}/jobs/fl3-v2-ta-pipeline:run" `
        --http-method POST `
        --oauth-service-account-email "fl3-v2-scheduler@${PROJECT}.iam.gserviceaccount.com" `
        2>$null

    # If exists, update
    if ($LASTEXITCODE -ne 0) {
        gcloud scheduler jobs update http fl3-v2-ta-pipeline-scheduler `
            --location $REGION `
            --schedule "*/5 9-16 * * 1-5" `
            --time-zone "America/New_York"
    }

    Write-Success "TA Pipeline deployed with scheduler"
}

# Deploy Baseline Refresh Job
function Deploy-BaselineRefresh {
    Write-Step "Deploying Baseline Refresh job..."

    # Create Cloud Run Job
    gcloud run jobs create fl3-v2-baseline-refresh `
        --image "${IMAGE}:${Tag}" `
        --region $REGION `
        --service-account "fl3-v2-cloudrun@${PROJECT}.iam.gserviceaccount.com" `
        --set-secrets "DATABASE_URL=DATABASE_URL:latest" `
        --memory 512Mi `
        --cpu 1 `
        --task-timeout 600 `
        --max-retries 1 `
        --command "python" `
        --args "-m,scripts.refresh_baselines" `
        2>$null

    # If job exists, update it
    if ($LASTEXITCODE -ne 0) {
        gcloud run jobs update fl3-v2-baseline-refresh `
            --image "${IMAGE}:${Tag}" `
            --region $REGION
    }

    # Create Cloud Scheduler (4:30 PM ET daily)
    Write-Step "Creating Cloud Scheduler for Baseline Refresh..."

    gcloud scheduler jobs create http fl3-v2-baseline-refresh-scheduler `
        --location $REGION `
        --schedule "30 16 * * 1-5" `
        --time-zone "America/New_York" `
        --uri "https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT}/jobs/fl3-v2-baseline-refresh:run" `
        --http-method POST `
        --oauth-service-account-email "fl3-v2-scheduler@${PROJECT}.iam.gserviceaccount.com" `
        2>$null

    # If exists, update
    if ($LASTEXITCODE -ne 0) {
        gcloud scheduler jobs update http fl3-v2-baseline-refresh-scheduler `
            --location $REGION `
            --schedule "30 16 * * 1-5" `
            --time-zone "America/New_York"
    }

    Write-Success "Baseline Refresh deployed with scheduler"
}

# Main execution
Write-Host "`n"
Write-Host "========================================" -ForegroundColor Yellow
Write-Host "  FL3_V2 Deployment Script" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
Write-Host "Project: $PROJECT"
Write-Host "Region:  $REGION"
Write-Host "Image:   ${IMAGE}:${Tag}"
Write-Host ""

# Build image first
Build-Image

# Deploy requested service(s)
if ($All) {
    Deploy-Firehose
    Deploy-TAPipeline
    Deploy-BaselineRefresh
} elseif ($Service) {
    switch ($Service) {
        "firehose" { Deploy-Firehose }
        "ta-pipeline" { Deploy-TAPipeline }
        "baseline-refresh" { Deploy-BaselineRefresh }
    }
} else {
    Write-Host "`nUsage:"
    Write-Host "  .\deploy\deploy.ps1 -Service firehose"
    Write-Host "  .\deploy\deploy.ps1 -Service ta-pipeline"
    Write-Host "  .\deploy\deploy.ps1 -Service baseline-refresh"
    Write-Host "  .\deploy\deploy.ps1 -All"
}

Write-Host "`n========================================" -ForegroundColor Yellow
Write-Host "  Deployment Complete" -ForegroundColor Yellow
Write-Host "========================================`n" -ForegroundColor Yellow
