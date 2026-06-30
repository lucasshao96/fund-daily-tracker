#!/bin/bash
cd "d:/AI/fund-daily-tracker"
git remote set-url origin https://github.com/LucasShao96/fund-daily-tracker.git
git -c "credential.helper=!f() { echo username=token; echo \"password=${GITHUB_TOKEN}\"; }; f" push -u origin master
