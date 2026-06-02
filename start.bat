@echo off
chcp 65001 >nul
title 牙科设备推荐Agent

echo ========================================
echo   牙科设备推荐Agent — 一键启动
echo ========================================
echo.

:: 检查 Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Python，请先安装 Python 3.11+
    pause
    exit /b 1
)
echo [✓] Python

:: 检查 Node.js
where node >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Node.js，请先安装 Node.js 18+
    pause
    exit /b 1
)
echo [✓] Node.js

:: 检查端口
netstat -an | findstr ":8765.*LISTENING" >nul 2>&1
if %errorlevel% equ 0 (
    echo [错误] 端口 8765 已被占用
    pause
    exit /b 1
)
netstat -an | findstr ":5173.*LISTENING" >nul 2>&1
if %errorlevel% equ 0 (
    echo [错误] 端口 5173 已被占用
    pause
    exit /b 1
)
echo [✓] 端口可用

echo.
echo [1/2] 检查依赖...
cd backend
python -c "import fastapi, langgraph, langchain_openai" 2>nul || (
    echo   → 安装 Python 依赖...
    pip install -r requirements.txt
)
cd ..

cd frontend
if not exist node_modules (
    echo   → 安装前端依赖...
    call npm install
)
cd ..
echo [✓] 依赖就绪

echo.
echo [2/2] 启动服务...

:: 创建日志目录
if not exist logs mkdir logs

:: 启动后端
echo [启动] 后端服务 (FastAPI :8765)...
cd backend
set DENTAL_AGENT_DEV=1
start /b python main.py > ..\logs\backend.log 2>&1
cd ..

:: 等待后端
echo   等待后端就绪...
set /a attempts=0
:wait_backend
timeout /t 1 /nobreak >nul
set /a attempts+=1
if %attempts% gtr 30 (
    echo [错误] 后端启动超时，请检查 logs\backend.log
    pause
    exit /b 1
)
curl -s http://localhost:8765/api/health >nul 2>&1
if %errorlevel% neq 0 goto wait_backend
echo [✓] 后端就绪

:: 启动前端
echo [启动] 前端服务 (Vite :5173)...
cd frontend
start /b npm run dev > ..\logs\frontend.log 2>&1
cd ..

:: 等待前端
echo   等待前端就绪...
set /a attempts=0
:wait_frontend
timeout /t 1 /nobreak >nul
set /a attempts+=1
if %attempts% gtr 30 (
    echo [错误] 前端启动超时，请检查 logs\frontend.log
    pause
    exit /b 1
)
curl -s http://localhost:5173 >nul 2>&1
if %errorlevel% neq 0 goto wait_frontend
echo [✓] 前端就绪

echo.
echo ========================================
echo   ✓ 全部就绪！
echo ========================================
echo.
echo   前端:      http://localhost:5173
echo   后端 API:  http://localhost:8765
echo.
echo   按 Ctrl+C 停止所有服务
echo.

:: 打开浏览器
start http://localhost:5173

:: 保持窗口
pause
