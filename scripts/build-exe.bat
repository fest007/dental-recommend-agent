@echo off
REM =============================================================================
REM 牙科设备推荐Agent — Windows 打包脚本
REM =============================================================================

setlocal enabledelayedexpansion

echo.
echo ==========================================
echo  牙科设备推荐Agent — 打包 Windows EXE
echo ==========================================
echo.

REM 检查依赖
echo [检查] 检查构建依赖...

where node >nul 2>nul
if %errorlevel% neq 0 (
    echo [错误] 未找到 Node.js
    exit /b 1
)

where python >nul 2>nul
if %errorlevel% neq 0 (
    where py >nul 2>nul
    if %errorlevel% neq 0 (
        echo [错误] 未找到 Python
        exit /b 1
    )
)

python -c "import PyInstaller" >nul 2>nul
if %errorlevel% neq 0 (
    echo [!] 安装 PyInstaller...
    pip install pyinstaller -q
)

echo [√] 依赖检查通过
echo.

REM 清理
echo [清理] 清理旧的构建产物...
if exist frontend\dist rmdir /s /q frontend\dist
if exist backend\dist rmdir /s /q backend\dist
if exist backend\build rmdir /s /q backend\build
if exist release rmdir /s /q release
echo [√] 清理完成
echo.

REM 构建前端
echo [1/3] 构建 React 前端...
cd frontend
call npm ci
call npm run build
if not exist dist (
    echo [错误] 前端构建失败
    exit /b 1
)
cd ..
echo [√] 前端构建完成
echo.

REM 构建后端
echo [2/5] 打包 Python 后端...
cd backend
call pyinstaller backend.spec --clean --noconfirm
if not exist dist\backend (
    echo [错误] 后端打包失败
    exit /b 1
)
cd ..
echo [√] 后端打包完成
echo.

REM Smoke test — 基础存活检查
echo [3/5] 验证后端可执行文件...
cd backend\dist\backend
start /B backend.exe
timeout /t 5 /nobreak >nul
tasklist /FI "IMAGENAME eq backend.exe" | find /I "backend.exe" >nul
if %errorlevel% equ 0 (
    echo [√] 后端基础 smoke test 通过
    taskkill /F /IM backend.exe >nul 2>&1
) else (
    echo [错误] 后端 smoke test 失败 - 进程异常退出
    exit /b 1
)
cd ..\..\..
echo.

REM Smoke test — 端口碰撞
echo [3.5/5] 验证端口碰撞场景...
powershell -ExecutionPolicy Bypass -File scripts\smoke-test-backend.ps1
if %errorlevel% neq 0 (
    echo [错误] 端口碰撞 smoke test 失败
    exit /b 1
)
echo [√] 端口碰撞 smoke test 通过
echo.

REM 打包 Electron
echo [4/5] 打包 Windows EXE...
call npm ci
call npx electron-builder --win --config --publish never
if %errorlevel% neq 0 (
    echo [错误] Electron 打包失败
    exit /b 1
)
echo [√] 打包完成
echo.

REM Smoke test — Electron 集成
echo [5/5] 验证 Electron 完整启动链路...
powershell -ExecutionPolicy Bypass -File scripts\smoke-test-electron.ps1
if %errorlevel% neq 0 (
    echo [错误] Electron 集成 smoke test 失败
    exit /b 1
)
echo [√] Electron 集成 smoke test 通过
echo.

REM 显示结果
echo.
echo ==========================================
echo  构建完成！
echo ==========================================
echo.
echo 输出目录: release\
dir /b release\*.exe 2>nul
echo.

endlocal
pause
