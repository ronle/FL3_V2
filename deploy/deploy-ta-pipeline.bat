@echo off
echo Creating TA Pipeline Cloud Run Job...

gcloud run jobs create fl3-v2-ta-pipeline ^
    --image us-west1-docker.pkg.dev/fl3-v2-prod/fl3-v2-images/fl3-v2:v2 ^
    --region us-west1 ^
    --service-account fl3-v2-cloudrun@fl3-v2-prod.iam.gserviceaccount.com ^
    --set-secrets "DATABASE_URL=DATABASE_URL:latest,ALPACA_API_KEY=ALPACA_API_KEY:latest,ALPACA_SECRET_KEY=ALPACA_SECRET_KEY:latest" ^
    --memory 1Gi ^
    --cpu 1 ^
    --task-timeout 300 ^
    --max-retries 1 ^
    --command python,-m,scripts.ta_pipeline_v2,--once

if %ERRORLEVEL% NEQ 0 (
    echo Job exists, updating...
    gcloud run jobs update fl3-v2-ta-pipeline ^
        --image us-west1-docker.pkg.dev/fl3-v2-prod/fl3-v2-images/fl3-v2:v2 ^
        --region us-west1
)

echo.
echo Creating Cloud Scheduler for TA Pipeline...
echo Schedule: Every 5 min, 9:00-16:00 ET, Mon-Fri

gcloud scheduler jobs create http fl3-v2-ta-pipeline-scheduler ^
    --location us-west1 ^
    --schedule "*/5 9-16 * * 1-5" ^
    --time-zone "America/New_York" ^
    --uri "https://us-west1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/fl3-v2-prod/jobs/fl3-v2-ta-pipeline:run" ^
    --http-method POST ^
    --oauth-service-account-email fl3-v2-scheduler@fl3-v2-prod.iam.gserviceaccount.com

if %ERRORLEVEL% NEQ 0 (
    echo Scheduler exists, updating...
    gcloud scheduler jobs update http fl3-v2-ta-pipeline-scheduler ^
        --location us-west1 ^
        --schedule "*/5 9-16 * * 1-5" ^
        --time-zone "America/New_York"
)

echo.
echo Done!
