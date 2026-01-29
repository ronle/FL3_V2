@echo off
echo ============================================================
echo FL3_V2 SMOKE TEST
echo ============================================================
echo.

echo === 1. FIREHOSE HEALTH CHECK ===
for /f "tokens=*" %%i in ('gcloud auth print-identity-token') do set TOKEN=%%i
curl -s -H "Authorization: Bearer %TOKEN%" https://fl3-v2-firehose-660675366661.us-west1.run.app/health
echo.
echo.

echo === 2. EXECUTE TA PIPELINE JOB ===
gcloud run jobs execute fl3-v2-ta-pipeline --region us-west1 --wait
echo.

echo === 3. EXECUTE BASELINE REFRESH JOB ===
gcloud run jobs execute fl3-v2-baseline-refresh --region us-west1 --wait
echo.

echo === 4. CHECK RECENT FIREHOSE LOGS ===
gcloud logging read "resource.labels.service_name=fl3-v2-firehose" --limit 5 --format="value(textPayload)"
echo.

echo ============================================================
echo SMOKE TEST COMPLETE
echo ============================================================
