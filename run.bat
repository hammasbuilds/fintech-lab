@echo off
REM Launch the control panel, or one project on its own.
REM   run.bat            the Observatory, with all three projects started
REM   run.bat panel      the Observatory, projects left alone
REM   run.bat 1..6      one project by itself (4-6 are the model arms)
setlocal
set CHOICE=%1
if "%CHOICE%"=="" set CHOICE=all
if "%CHOICE%"=="all" (
  echo Observatory   -  http://127.0.0.1:8100   ^(starting all three^)
  uv run python -m observatory --start-all
) else if "%CHOICE%"=="panel" (
  echo Observatory   -  http://127.0.0.1:8100
  uv run python -m observatory
) else if "%CHOICE%"=="1" (
  echo Switchboard   -  http://127.0.0.1:8101
  uv run python -m projects.p01_switchboard.app --port 8101
) else if "%CHOICE%"=="2" (
  echo Policy Bench  -  http://127.0.0.1:8102
  uv run python -m projects.p02_policy_bench.app --port 8102
) else if "%CHOICE%"=="3" (
  echo Guardian      -  http://127.0.0.1:8103
  uv run python -m projects.p03_guardian.app --port 8103
) else if "%CHOICE%"=="4" (
  echo Spec to wire  -  http://127.0.0.1:8104
  uv run python -m projects.p04_spec_to_wire.app --port 8104
) else if "%CHOICE%"=="5" (
  echo Policy prose  -  http://127.0.0.1:8105
  uv run python -m projects.p05_policy_from_prose.app --port 8105
) else if "%CHOICE%"=="6" (
  echo Model vs lex  -  http://127.0.0.1:8106
  uv run python -m projects.p06_model_vs_lexicon.app --port 8106
) else (
  echo Usage: run.bat [all^|panel^|1^|2^|3^|4^|5^|6]
  exit /b 1
)
