#!/bin/bash
# =============================================================================
# 牙科设备推荐Agent — 打包 macOS DMG
# =============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_step() { echo -e "${BLUE}[$1]${NC} $2"; }
print_success() { echo -e "${GREEN}✓${NC} $1"; }
print_error() { echo -e "${RED}✗${NC} $1"; exit 1; }
print_warning() { echo -e "${YELLOW}!${NC} $1"; }

echo ""
echo "=========================================="
echo " 牙科设备推荐Agent — 打包 macOS DMG"
echo "=========================================="
echo ""

# 检查平台
if [[ "$(uname -s)" != "Darwin" ]]; then
    print_error "此脚本只能在 macOS 上运行"
fi

# 检查依赖
print_step "检查" "检查构建依赖..."
command -v node &> /dev/null || print_error "未找到 Node.js"
command -v npm &> /dev/null || print_error "未找到 npm"
command -v python3 &> /dev/null || command -v python &> /dev/null || print_error "未找到 Python"
python3 -c "import PyInstaller" &> /dev/null 2>&1 || python -c "import PyInstaller" &> /dev/null 2>&1 || print_error "未找到 PyInstaller (pip install pyinstaller)"
print_success "依赖检查通过"

# 清理
print_step "清理" "清理旧的构建产物..."
rm -rf frontend/dist backend/dist backend/build release
print_success "清理完成"

# 构建前端
print_step "1/5" "构建 React 前端..."
cd frontend
npm ci
npm run build
[ ! -d "dist" ] && print_error "前端构建失败"
cd ..
print_success "前端构建完成"

# 构建后端
print_step "2/5" "打包 Python 后端..."
cd backend
pyinstaller backend.spec --clean --noconfirm
[ ! -d "dist/backend" ] && print_error "后端打包失败"
cd ..
print_success "后端打包完成"

# Smoke test — 基础存活检查
print_step "3/5" "验证后端可执行文件..."
cd backend/dist/backend
./backend &
BACKEND_PID=$!
sleep 5
if kill -0 $BACKEND_PID 2>/dev/null; then
    print_success "后端基础 smoke test 通过"
    kill $BACKEND_PID
    wait $BACKEND_PID 2>/dev/null
else
    print_error "后端 smoke test 失败 - 进程异常退出"
fi
cd ../../..

# Smoke test — 端口碰撞
print_step "3.5/5" "验证端口碰撞场景..."
bash scripts/smoke-test-backend.sh
print_success "端口碰撞 smoke test 通过"

# 打包 DMG
print_step "4/5" "打包 macOS DMG..."
npm ci
npx electron-builder --mac --config --publish never
print_success "DMG 打包完成"

# Smoke test — Electron 集成
print_step "5/5" "验证 Electron 完整启动链路..."
bash scripts/smoke-test-electron.sh
print_success "Electron 集成 smoke test 通过"

# 显示结果
echo ""
echo "=========================================="
echo -e "${GREEN} 构建完成！${NC}"
echo "=========================================="
echo ""
echo "输出目录: release/"
ls -lh release/*.dmg 2>/dev/null || print_warning "未找到 DMG 文件"
echo ""
