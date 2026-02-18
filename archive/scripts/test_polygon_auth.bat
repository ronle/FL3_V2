@echo off
for /f "tokens=*" %%i in ('gcloud secrets versions access latest --secret=POLYGON_API_KEY --project=spartan-buckeye-474319-q8') do set POLYGON_API_KEY=%%i
python C:\Users\levir\Documents\FL3_V2\test_polygon_auth.py
