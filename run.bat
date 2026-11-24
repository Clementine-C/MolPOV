@echo off
rem Launch MolPOV from this folder.
cd /d "%~dp0"
python -m molpov %*
