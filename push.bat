@echo off
cd /d d:\AI\fund-daily-tracker
git -c "credential.helper=!f() { echo username=token; echo password=%GITHUB_TOKEN%; }; f" push -u origin master
