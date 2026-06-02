#!/bin/bash
# =============================================================================
# 牙科设备推荐Agent — 同时打包 macOS DMG 和 Windows EXE
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
echo " 牙科设备推荐Agent — 打包所有平台"
echo "=========================================="
echo ""

# 检查依赖
print_step "检查" "检查构建依赖..."
command -v node &> /dev/null || print_error "未找到 Node.js"
command -v npm &> /dev/null || print_error "未找到 npm"
command -v python3 &> /dev/null || command -v python &> /dev/null || print_error "未找到 Python"
python3 -c "import PyInstaller" &> /dev/null 2>&1 || python -c "import PyInstaller" &> /dev/null 2>&1 || print_error "未找到 PyInstaller"
print_success "依赖检查通过"

# 检查 Wine
HAS_WINE=false
if command -v wine &> /dev/null || command -v wine64 &> /dev/null; then
    HAS_WINE=true
    print_success "找到 Wine，支持交叉编译"
else
    print_warning "未找到 Wine，将只打包 macOS DMG"
    print_warning "安装 Wine: brew install --cask wine-stable"
fi

# 清理
print_step "清理" "清理旧的构建产物..."
rm -rf frontend/dist backend/dist backend/build release
print_success "清理完成"

# 构建前端
print_step "1/3" "构建 React 前端..."
cd frontend
[ ! -d "node_modules" ] && npm install
npm run build
[ ! -d "dist" ] && print_error "前端构建失败"
cd ..
print_success "前端构建完成"

# 构建后端
print_step "2/3" "打包 Python 后端..."
cd backend
pyinstaller backend.spec --clean --noconfirm
[ ! -d "dist/backend" ] && print_error "后端打包失败"
cd ..
print_success "后端打包完成"

# 打包 Electron 应用
print_step "3/3" "打包 Electron 应用..."
[ ! -d "node_modules" ] && npm install

if [ "$HAS_WINE" = true ]; then
    print_warning "打包 macOS DMG + Windows EXE..."
    npx electron-builder --mac --win --config
else
    print_warning "打包 macOS DMG..."
    npx electron-builder --mac --config
fi

print_success "打包完成"

# 显示结果
echo ""
echo "=========================================="
echo -e "${GREEN} 构建完成！${NC}"
echo "=========================================="
echo ""
echo "输出目录: release/"
echo ""

if ls release/*.dmg &>/dev/null; then
    echo "macOS:"
    ls -lh release/*.dmg 2>/dev/null
    echo ""
fi

if ls release/*.exe &>/dev/null; then
    echo "Windows:"
    ls -lh release/*.exe 2>/dev/null
    echo ""
fi

if [ "$HAS_WINE" = false ]; then
    echo ""
    print_warning "如需打包 Windows EXE，请安装 Wine 后重新运行:"
    echo "  brew install --cask wine-stable"
    echo "  ./build-all.sh"
fi
