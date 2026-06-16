@echo off
cd /d "%~dp0"
echo === Staging backend changes ===
git add backend/app/api/routes.py
git add backend/app/services/prospect_onboarding_service.py
echo === Committing ===
git commit -m "Background subscription flow: parallel place ID resolution + async activation"
echo === Pushing ===
git push origin main
echo === Done ===
pause
