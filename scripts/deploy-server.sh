#!/usr/bin/env bash
# =============================================================================
# 牙科设备推荐Agent — 服务器一键部署脚本
# 用法:
#   ./scripts/deploy-server.sh --domain example.com
#   ./scripts/deploy-server.sh --domain 1.2.3.4 --service-name taglessrec
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_step() { echo -e "${BLUE}[$1]${NC} $2"; }
print_success() { echo -e "${GREEN}✓${NC} $1"; }
print_warning() { echo -e "${YELLOW}!${NC} $1"; }
print_error() { echo -e "${RED}✗${NC} $1"; exit 1; }

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_DIR="$PROJECT_DIR/backend"
FRONTEND_DIR="$PROJECT_DIR/frontend"

SERVICE_NAME="taglessrec"
DOMAIN="_"
BACKEND_PORT="8765"
APP_USER="${SUDO_USER:-$USER}"
DATA_DIR="$PROJECT_DIR/.server-data"
SKIP_NGINX="0"

usage() {
    cat <<EOF
用法:
  ./scripts/deploy-server.sh [选项]

选项:
  --domain <域名>          Nginx server_name，默认 _
  --service-name <名称>    systemd 服务名，默认 taglessrec
  --port <端口>            后端固定端口，默认 8765
  --data-dir <目录>        数据目录，默认 <项目目录>/.server-data
  --user <用户>            运行服务的 Linux 用户，默认当前用户
  --skip-nginx             跳过 Nginx 配置
  -h, --help               显示帮助

示例:
  ./scripts/deploy-server.sh --domain demo.example.com
  ./scripts/deploy-server.sh --domain 203.0.113.10 --port 9000
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --domain)
            DOMAIN="${2:-}"
            shift 2
            ;;
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
        --user)
            APP_USER="${2:-}"
            shift 2
            ;;
        --skip-nginx)
            SKIP_NGINX="1"
            shift
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

if [[ ! "$BACKEND_PORT" =~ ^[0-9]+$ ]]; then
    print_error "端口必须是数字: $BACKEND_PORT"
fi

if [[ "$(uname -s)" != "Linux" ]]; then
    print_error "该脚本仅支持 Linux 服务器部署"
fi

if [[ "$PROJECT_DIR" =~ [[:space:]] ]]; then
    print_error "服务器部署目录不能包含空格，请把项目放到类似 /srv/taglessrec 的路径下"
fi

if [[ "$DATA_DIR" =~ [[:space:]] ]]; then
    print_error "数据目录不能包含空格: $DATA_DIR"
fi

need_cmd() {
    command -v "$1" >/dev/null 2>&1 || print_error "未找到命令: $1"
}

need_cmd git
need_cmd curl
need_cmd systemctl
need_cmd node
need_cmd npm

if [[ "$SKIP_NGINX" != "1" ]]; then
    need_cmd nginx
fi

choose_python() {
    local candidates=(python3.11 python3 python)
    for cmd in "${candidates[@]}"; do
        if command -v "$cmd" >/dev/null 2>&1; then
            if "$cmd" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
            then
                echo "$cmd"
                return 0
            fi
        fi
    done
    return 1
}

PYTHON_BIN="$(choose_python)" || print_error "需要 Python 3.11+"

NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
if [[ "$NODE_MAJOR" -lt 18 ]]; then
    print_error "需要 Node.js 18+，当前为 $(node -v)"
fi

if [[ "$(id -u)" -eq 0 ]]; then
    SUDO=""
else
    need_cmd sudo
    sudo -v >/dev/null 2>&1 || print_error "需要 sudo 权限"
    SUDO="sudo"
fi

id "$APP_USER" >/dev/null 2>&1 || print_error "运行用户不存在: $APP_USER"

mkdir -p "$DATA_DIR"

echo ""
echo "=========================================="
echo " 牙科设备推荐Agent — 服务器部署"
echo "=========================================="
echo ""
echo "项目目录: $PROJECT_DIR"
echo "数据目录: $DATA_DIR"
echo "服务名称: $SERVICE_NAME"
echo "运行用户: $APP_USER"
echo "后端端口: $BACKEND_PORT"
echo "域名地址: $DOMAIN"
echo ""

print_step "1/6" "安装后端依赖..."
cd "$BACKEND_DIR"
if [[ ! -d ".venv" ]]; then
    "$PYTHON_BIN" -m venv .venv
fi
source .venv/bin/activate
pip install -U pip >/dev/null
pip install -r requirements.txt
print_success "后端依赖安装完成"

print_step "2/6" "构建前端..."
cd "$FRONTEND_DIR"
npm ci
npm run build
print_success "前端构建完成"

print_step "3/6" "初始化配置与数据目录..."
mkdir -p "$DATA_DIR/data" "$DATA_DIR/logs"
CONFIG_PATH="$DATA_DIR/config.yaml"
if [[ ! -f "$CONFIG_PATH" ]]; then
    cp "$BACKEND_DIR/config.yaml.example" "$CONFIG_PATH"
    print_warning "已创建配置文件: $CONFIG_PATH"
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
print_success "配置文件已同步到 127.0.0.1:$BACKEND_PORT"

print_step "4/6" "写入 systemd 服务..."
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
$SUDO tee "$SERVICE_FILE" >/dev/null <<EOF
[Unit]
Description=TaglessRec Backend
After=network.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$BACKEND_DIR
Environment=DENTAL_AGENT_DATA_DIR=$DATA_DIR
Environment=PYTHONUNBUFFERED=1
ExecStart=$BACKEND_DIR/.venv/bin/python $BACKEND_DIR/main.py
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

$SUDO systemctl daemon-reload
$SUDO systemctl enable "$SERVICE_NAME" >/dev/null
$SUDO systemctl restart "$SERVICE_NAME"
print_success "systemd 服务已启动"

if [[ "$SKIP_NGINX" != "1" ]]; then
    print_step "5/6" "写入 Nginx 配置..."
    NGINX_CONTENT=$(cat <<EOF
server {
    listen 80;
    server_name $DOMAIN;

    root $FRONTEND_DIR/dist;
    index index.html;

    location / {
        try_files \$uri \$uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:$BACKEND_PORT/api/;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
)

    if [[ -d /etc/nginx/sites-available && -d /etc/nginx/sites-enabled ]]; then
        NGINX_AVAILABLE="/etc/nginx/sites-available/${SERVICE_NAME}"
        NGINX_ENABLED="/etc/nginx/sites-enabled/${SERVICE_NAME}"
        $SUDO tee "$NGINX_AVAILABLE" >/dev/null <<<"$NGINX_CONTENT"
        $SUDO ln -sf "$NGINX_AVAILABLE" "$NGINX_ENABLED"
    else
        NGINX_CONF="/etc/nginx/conf.d/${SERVICE_NAME}.conf"
        $SUDO tee "$NGINX_CONF" >/dev/null <<<"$NGINX_CONTENT"
    fi

    $SUDO nginx -t
    $SUDO systemctl enable nginx >/dev/null 2>&1 || true
    $SUDO systemctl reload nginx
    print_success "Nginx 配置已生效"
else
    print_step "5/6" "跳过 Nginx 配置"
fi

print_step "6/6" "健康检查..."
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
    echo ""
    journalctl -u "$SERVICE_NAME" -n 50 --no-pager || true
    print_error "健康检查失败: $HEALTH_URL"
fi

print_success "健康检查通过"

echo ""
echo "=========================================="
echo -e "${GREEN} 部署完成${NC}"
echo "=========================================="
echo ""
echo "后端健康检查: $HEALTH_URL"
if [[ "$SKIP_NGINX" != "1" ]]; then
    echo "前端访问地址: http://$DOMAIN"
fi
echo "配置文件: $CONFIG_PATH"
echo "数据目录: $DATA_DIR"
echo ""
echo "常用命令:"
echo "  sudo systemctl status $SERVICE_NAME"
echo "  journalctl -u $SERVICE_NAME -f"
echo "  bash $PROJECT_DIR/scripts/update-server.sh --service-name $SERVICE_NAME --data-dir $DATA_DIR --port $BACKEND_PORT"
echo ""
