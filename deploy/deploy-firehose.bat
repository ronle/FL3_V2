@echo off
echo Deploying Firehose to Cloud Run...

gcloud run deploy fl3-v2-firehose ^
    --image us-west1-docker.pkg.dev/fl3-v2-prod/fl3-v2-images/fl3-v2:v2 ^
    --region us-west1 ^
    --platform managed ^
    --no-allow-unauthenticated ^
    --service-account fl3-v2-cloudrun@fl3-v2-prod.iam.gserviceaccount.com ^
    --set-secrets "DATABASE_URL=DATABASE_URL:latest,POLYGON_API_KEY=POLYGON_API_KEY:latest" ^
    --memory 2Gi ^
    --cpu 1 ^
    --timeout 3600 ^
    --min-instances 1 ^
    --max-instances 1 ^
    --no-cpu-throttling ^
    --execution-environment gen2

echo.
echo Done!
