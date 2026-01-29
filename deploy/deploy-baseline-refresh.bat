@echo off
echo Creating Baseline Refresh Cloud Run Job...

gcloud run jobs create fl3-v2-baseline-refresh ^
    --image us-west1-docker.pkg.dev/fl3-v2-prod/fl3-v2-images/fl3-v2:v2 ^
    --region us-west1 ^
    --service-account fl3-v2-cloudrun@fl3-v2-prod.iam.gserviceaccount.com ^
    --set-secrets "DATABASE_URL=DATABASE_URL:latest" ^
    --memory 512Mi ^
    --cpu 1 ^
    --task-timeout 600 ^
    --max-retries 1 ^
    --command python,-m,scripts.refresh_baselines

echo.
echo Done!
