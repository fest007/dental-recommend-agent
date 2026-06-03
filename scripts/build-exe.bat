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
if not exist node_modules call npm install
call npm run build
if not exist dist (
    echo [错误] 前端构建失败
    exit /b 1
)
cd ..
echo [√] 前端构建完成
echo.

REM 构建后端
echo [2/3] 打包 Python 后端...
cd backend
call pyinstaller backend.spec --clean --noconfirm
if not exist dist\backend (
    echo [错误] 后端打包失败
    exit /b 1
)
cd ..
echo [√] 后端打包完成
echo.

REM 打包 Electron
echo [3/3] 打包 Windows EXE...
if not exist node_modules call npm install
call npx electron-builder --win --config --publish never
if %errorlevel% neq 0 (
    echo [错误] Electron 打包失败
    exit /b 1
)
echo [√] 打包完成
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
