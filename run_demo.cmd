@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  set PY=py
) else (
  set PY=python
)
%PY% -m unittest discover -s tests -v || exit /b 1
%PY% -m sos_mvp examples/error_report.sos --dry-run --emit-ir output/language_operator_graph.json || exit /b 1
%PY% -m sos_mvp examples/error_report.sos --yes --json
pause
