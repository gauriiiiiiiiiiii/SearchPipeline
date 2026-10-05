@echo off
REM Runs the pipeline. Arguments pass through, e.g.:
REM   run.bat                 all stages
REM   run.bat extract         only visit links and pull event details
REM   run.bat search --cities "Pune,Mumbai"
cd /d "%~dp0"
python run.py %*
pause
