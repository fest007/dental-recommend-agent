#!/bin/bash
# =============================================================================
# 牙科设备推荐Agent — 打包 Windows EXE (macOS 交叉编译)
# 需要安装 Wine: brew install --cask wine-stable
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
echo " 牙科设备推荐Agent — 打包 Windows EXE"
echo "=========================================="
echo ""

# 检查依赖
print_step "检查" "检查构建依赖..."
command -v node &> /dev/null || print_error "未找到 Node.js"
command -v npm &> /dev/null || print_error "未找到 npm"
command -v python3 &> /dev/null || command -v python &> /dev/null || print_error "未找到 Python"
python3 -c "import PyInstaller" &> /dev/null 2>&1 || python -c "import PyInstaller" &> /dev/null 2>&1 || print_error "未找到 PyInstaller"

# 检查 Wine (交叉编译需要)
if [[ "$(uname -s)" == "Darwin" ]]; then
    if ! command -v wine &> /dev/null && ! command -v wine64 &> /dev/null; then
        print_warning "未找到 Wine，交叉编译可能失败"
        print_warning "安装 Wine: brew install --cask wine-stable"
        echo ""
        read -p "是否继续？(y/N) " -n 1 -r
        echo ""
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
fi

print_success "依赖检查通过"

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

# 构建后端 (使用 PyInstaller 交叉编译或原生编译)
print_step "2/3" "打包 Python 后端..."
cd backend

if [[ "$(uname -s)" == "Darwin" ]]; then
    # macOS 上构建 macOS 版本的后端（用于测试）
    # 实际的 Windows 后端需要在 Windows 上构建
    print_warning "注意: macOS 上构建的后端是 macOS 版本"
    print_warning "Windows EXE 包中的后端需要在 Windows 上单独构建"
    pyinstaller backend.spec --clean --noconfirm
else
    pyinstaller backend.spec --clean --noconfirm
fi

[ ! -d "dist/backend" ] && print_error "后端打包失败"
cd ..
print_success "后端打包完成"

# 打包 Windows EXE
print_step "3/3" "打包 Windows EXE..."
[ ! -d "node_modules" ] && npm install

# 设置环境变量使用国内镜像（可选）
# export ELECTRON_MIRROR="https://npmmirror.com/mirrors/electron/"

npx electron-builder --win --config
print_success "Windows EXE 打包完成"

# 显示结果
echo ""
echo "=========================================="
echo -e "${GREEN} 构建完成！${NC}"
echo "=========================================="
echo ""
echo "输出目录: release/"
ls -lh release/*.exe 2>/dev/null || print_warning "未找到 EXE 文件"
echo ""
print_warning "注意: Windows EXE 中的后端需要在 Windows 上构建后替换"
echo ""
