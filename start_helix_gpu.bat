@echo off
title HELIX PAIOS - GPU Mode (RTX 3050 + CUDA 12.1)
echo ================================================
echo   HELIX PAIOS - GPU Accelerated Launch
echo   Python 3.12 + PyTorch 2.5.1 + CUDA 12.1
echo   GPU: NVIDIA GeForce RTX 3050 6GB
echo ================================================
echo.

:: Set user profile name
set HELIX_USER_NAME=Vaibhav
set HELIX_USER_DISPLAY_NAME=Vaibhav

:: Set CUDA device & VRAM ceiling for 6GB GPU
set CUDA_VISIBLE_DEVICES=0
set HELIX_MAX_ACTIVE_VRAM_MB=6144

:: ── Ollama GPU Performance Tuning ─────────────────────
:: Tuned for: RTX 3050 6GB | i5-12450HX | 16GB RAM
:: NOTE: comments must be on their own lines — inline "::" after a "set"
::       becomes part of the variable value and corrupts it.
set OLLAMA_FLASH_ATTENTION=1
::- flash attention = faster + less VRAM
set OLLAMA_NUM_PARALLEL=1
::- single user — no parallel request overhead
set OLLAMA_MAX_LOADED_MODELS=1
::- only 1 model in VRAM at a time (6GB limit)
set OLLAMA_GPU_OVERHEAD=256000000
::- reserve 256MB VRAM for OS/display
set OLLAMA_KEEP_ALIVE=300
::- keep model hot during conversation; unload 5 min after last chat
set OLLAMA_MAX_QUEUE=1
::- no request queuing needed

:: Use Python 3.12 venv
set VENV=%~dp0venv312

:: Check venv exists
if not exist "%VENV%\Scripts\python.exe" (
    echo [ERROR] venv312 not found. Run setup first.
    pause
    exit /b 1
)

:: Start Ollama silently if not running
echo [1/3] Checking Ollama...
tasklist /FI "IMAGENAME eq ollama.exe" 2>NUL | find /I "ollama.exe" >NUL
if errorlevel 1 (
    echo       Starting Ollama service...
    start /B "" ollama serve >nul 2>&1
    timeout /t 3 /nobreak >nul
) else (
    echo       Ollama ready.
)

:: Clean up any existing instances on port 8000 (backend) or 3000 (frontend)
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr ":3000 " ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)

:: Start Backend
echo [2/3] Starting HELIX Backend (port 8000)...
start "HELIX Backend" cmd /k "cd /d %~dp0 && %VENV%\Scripts\python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload"

timeout /t 3 /nobreak >nul

:: Start Frontend
echo [3/3] Starting HELIX Frontend (port 3000)...
start "HELIX Frontend" cmd /k "cd /d %~dp0frontend && npx next dev -p 3000"

echo.
echo ================================================
echo   HELIX is starting up!
echo   Backend:  http://localhost:8000
echo   Frontend: http://localhost:3000
echo ================================================
echo.
echo Press any key to open HELIX in your browser...
pause >nul
start http://localhost:3000
