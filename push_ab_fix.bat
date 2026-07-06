@echo off
cd /d C:\Users\201397\local-competitor-intelligence

echo Commit already created. Pushing to main...
git push origin main

echo.
if %ERRORLEVEL%==0 (
  echo SUCCESS - check Render dashboard for deploy status.
) else (
  echo ERROR - push failed. Check output above.
)
pause
