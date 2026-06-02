#!/bin/bash
# =============================================================================
# 牙科设备推荐Agent — 快速发布脚本
# 用法: ./release.sh <版本号>
# 示例: ./release.sh 1.0.1
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
echo " 牙科设备推荐Agent — 发布脚本"
echo "=========================================="
echo ""

# 检查参数
if [ -z "$1" ]; then
    echo "用法: ./release.sh <版本号>"
    echo ""
    echo "示例:"
    echo "  ./release.sh 1.0.1"
    echo "  ./release.sh 2.0.0"
    echo ""
    exit 1
fi

VERSION=$1
TAG="v${VERSION}"

# 检查是否在 git 仓库中
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    print_error "当前目录不是 Git 仓库"
fi

# 检查工作目录是否干净
if [ -n "$(git status --porcelain)" ]; then
    print_warning "工作目录有未提交的更改:"
    git status --short
    echo ""
    read -p "是否继续？(y/N) " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# 检查 tag 是否已存在
if git tag -l | grep -q "^${TAG}$"; then
    print_error "Tag ${TAG} 已存在"
fi

# 更新 package.json 版本号
print_step "1/4" "更新版本号为 ${VERSION}..."
sed -i '' "s/\"version\": \"[^\"]*\"/\"version\": \"${VERSION}\"/" package.json
print_success "package.json 已更新"

# 提交版本更新
print_step "2/4" "提交版本更新..."
git add package.json
git commit -m "chore: bump version to ${VERSION}"
print_success "版本更新已提交"

# 创建 tag
print_step "3/4" "创建 tag ${TAG}..."
git tag -a "${TAG}" -m "Release ${TAG}"
print_success "Tag ${TAG} 已创建"

# 推送
print_step "4/4" "推送到 GitHub..."
git push origin main
git push origin "${TAG}"
print_success "已推送到 GitHub"

echo ""
echo "=========================================="
echo -e "${GREEN} 发布流程已启动！${NC}"
echo "=========================================="
echo ""
echo "接下来:"
echo "1. 访问 GitHub Actions 查看构建进度"
echo "2. 构建完成后，在 Releases 页面编辑并发布"
echo ""
echo "链接:"
echo "  Actions: https://github.com/$(git remote get-url origin | sed 's/.*github.com[:/]\(.*\)\.git/\1/')/actions"
echo "  Releases: https://github.com/$(git remote get-url origin | sed 's/.*github.com[:/]\(.*\)\.git/\1/')/releases"
echo ""
