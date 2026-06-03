#!/usr/bin/env bash
# =============================================================================
# 牙科设备推荐Agent — 服务器更新脚本
# 用法:
#   ./scripts/update-server.sh
#   ./scripts/update-server.sh --service-name taglessrec --port 8765
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_step() { echo -e "${BLUE}[$1]${NC} $2"; }
print_success() { echo -e "${GREEN}✓${NC} $1"; }
print_error() { echo -e "${RED}✗${NC} $1"; exit 1; }

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_DIR="$PROJECT_DIR/backend"
FRONTEND_DIR="$PROJECT_DIR/frontend"

SERVICE_NAME="taglessrec"
BACKEND_PORT="8765"
DATA_DIR="$PROJECT_DIR/.server-data"

usage() {
    cat <<EOF
用法:
  ./scripts/update-server.sh [选项]

选项:
  --service-name <名称>    systemd 服务名，默认 taglessrec
  --port <端口>            后端固定端口，默认 8765
  --data-dir <目录>        数据目录，默认 <项目目录>/.server-data
  -h, --help               显示帮助
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --service-name)
            SERVICE_NAME="${2:-}"
            shift 2
            ;;
        --port)
            BACKEND_PORT="${2:-}"
            shift 2
            ;;
        --data-dir)
            DATA_DIR="${2:-}"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            print_error "未知参数: $1"
            ;;
    esac
done

if [[ "$PROJECT_DIR" =~ [[:space:]] ]]; then
    print_error "服务器部署目录不能包含空格，请把项目放到类似 /srv/taglessrec 的路径下"
fi

if [[ "$DATA_DIR" =~ [[:space:]] ]]; then
    print_error "数据目录不能包含空格: $DATA_DIR"
fi

if [[ ! -d "$PROJECT_DIR/.git" ]]; then
    print_error "当前目录不是 Git 仓库根目录"
fi

if [[ "$(id -u)" -eq 0 ]]; then
    SUDO=""
else
    command -v sudo >/dev/null 2>&1 || print_error "需要 sudo 权限"
    sudo -v >/dev/null 2>&1 || print_error "需要 sudo 权限"
    SUDO="sudo"
fi

if [[ ! -x "$BACKEND_DIR/.venv/bin/python" ]]; then
    print_error "未找到后端虚拟环境，请先运行 deploy-server.sh"
fi

echo ""
echo "=========================================="
echo " 牙科设备推荐Agent — 服务器更新"
echo "=========================================="
echo ""

print_step "1/5" "拉取最新代码..."
git -C "$PROJECT_DIR" pull --ff-only
print_success "代码已更新"

print_step "2/5" "更新后端依赖..."
source "$BACKEND_DIR/.venv/bin/activate"
pip install -U pip >/dev/null
pip install -r "$BACKEND_DIR/requirements.txt"
print_success "后端依赖已更新"

print_step "3/5" "重建前端..."
cd "$FRONTEND_DIR"
npm ci
npm run build
print_success "前端构建完成"

print_step "4/5" "同步端口配置..."
mkdir -p "$DATA_DIR"
CONFIG_PATH="$DATA_DIR/config.yaml"
if [[ ! -f "$CONFIG_PATH" ]]; then
    cp "$BACKEND_DIR/config.yaml.example" "$CONFIG_PATH"
fi
"$BACKEND_DIR/.venv/bin/python" - <<PY
from pathlib import Path
import yaml

config_path = Path(r"""$CONFIG_PATH""")
cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
cfg.setdefault("server", {})
cfg["server"]["host"] = "127.0.0.1"
cfg["server"]["port"] = int("$BACKEND_PORT")
config_path.write_text(
    yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
    encoding="utf-8",
)
PY
print_success "配置已同步"

print_step "5/5" "重启服务并验证..."
$SUDO systemctl restart "$SERVICE_NAME"
HEALTH_URL="http://127.0.0.1:${BACKEND_PORT}/api/health"
READY="0"
for _ in $(seq 1 60); do
    if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
        READY="1"
        break
    fi
    sleep 2
done

if [[ "$READY" != "1" ]]; then
    journalctl -u "$SERVICE_NAME" -n 50 --no-pager || true
    print_error "健康检查失败: $HEALTH_URL"
fi

print_success "服务更新完成"
echo ""
echo "健康检查: $HEALTH_URL"
echo "服务状态: sudo systemctl status $SERVICE_NAME"
echo ""
