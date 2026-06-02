#!/bin/bash
# Build script for Dental Recommend Agent
# Packages the Electron app with Python backend as a standalone installer.

set -e

echo "=========================================="
echo " 牙科设备推荐Agent — 构建脚本"
echo "=========================================="

# Step 1: Build React frontend
echo ""
echo "[1/4] Building React frontend..."
cd frontend
npm run build
cd ..
echo "✓ Frontend built to frontend/dist/"

# Step 2: Build Python backend with PyInstaller
echo ""
echo "[2/4] Building Python backend with PyInstaller..."
cd backend
pyinstaller backend.spec --clean --noconfirm
cd ..
echo "✓ Backend built to backend/dist/backend/"

# Step 3: Create data directory template
echo ""
echo "[3/4] Preparing data directory..."
mkdir -p backend/data
echo "✓ Data directory ready"

# Step 4: Package with electron-builder
echo ""
echo "[4/4] Packaging Electron app..."
npx electron-builder --config
echo "✓ Installer created in release/"

echo ""
echo "=========================================="
echo " 构建完成！"
echo " 安装包位置: release/"
echo "=========================================="
