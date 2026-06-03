#!/bin/bash
# =============================================================================
# Electron 集成 Smoke Test (macOS)
# 验证：在端口被占用的情况下，打包后的应用能自动换端口并正常启动
# 流程：占 8765 → 启动 .app → 读 port.json → 健康检查
# 用法：从项目根目录运行，需要先完成 electron-builder --mac
# =============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

print_ok()   { echo -e "${GREEN}✓${NC} $1"; }
print_fail() { echo -e "${RED}✗${NC} $1"; exit 1; }
print_info() { echo -e "${YELLOW}→${NC} $1"; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_NAME="牙科设备推荐Agent"
# Electron userData 目录用 package.json 的 name 字段，不是 productName
APP_PKG_NAME="dental-recommend-agent"
OCCUPIED_PORT=8765

# ---------- 定位打包产物 ----------
if [ -d "$PROJECT_ROOT/release/mac-arm64" ]; then
    APP_BUNDLE="$PROJECT_ROOT/release/mac-arm64/$APP_NAME.app"
elif [ -d "$PROJECT_ROOT/release/mac" ]; then
    APP_BUNDLE="$PROJECT_ROOT/release/mac/$APP_NAME.app"
else
    print_fail "未找到 macOS 打包产物（请先运行 electron-builder --mac）"
fi

[ -d "$APP_BUNDLE" ] || print_fail "App bundle 不存在: $APP_BUNDLE"
print_ok "找到 App bundle: $APP_BUNDLE"

# ---------- 准备 ----------
USER_DATA_DIR="$HOME/Library/Application Support/$APP_PKG_NAME"
BACKEND_DATA_DIR="$USER_DATA_DIR/backend-data"
PORT_JSON="$BACKEND_DATA_DIR/port.json"

mkdir -p "$BACKEND_DATA_DIR"
rm -f "$PORT_JSON"

APP_PID=""
PORT_BLOCKER_PID=""

cleanup() {
    [ -n "$APP_PID" ] && kill "$APP_PID" 2>/dev/null || true
    [ -n "$PORT_BLOCKER_PID" ] && kill "$PORT_BLOCKER_PID" 2>/dev/null || true
}
trap cleanup EXIT

# ---------- 1. 占住首选端口 ----------
print_info "占用端口 $OCCUPIED_PORT ..."

python3 -c "
import socket, sys, time
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
s.bind(('127.0.0.1', $OCCUPIED_PORT))
s.listen(1)
sys.stdout.write('ready\n')
sys.stdout.flush()
time.sleep(300)
" &
PORT_BLOCKER_PID=$!

for i in $(seq 1 10); do
    if python3 -c "import socket; s=socket.socket(); s.settimeout(1); s.connect(('127.0.0.1',$OCCUPIED_PORT)); s.close()" 2>/dev/null; then
        break
    fi
    sleep 0.3
done
print_ok "端口 $OCCUPIED_PORT 已被占用 (PID $PORT_BLOCKER_PID)"

# ---------- 2. 启动 Electron 应用 ----------
print_info "启动 Electron 应用 ..."

"$APP_BUNDLE/Contents/MacOS/$(basename "$APP_NAME")" --no-sandbox &
APP_PID=$!
print_ok "应用已启动 (PID $APP_PID)"

# ---------- 3. 等待 port.json ----------
print_info "等待 port.json 出现 (最多 45 秒) ..."

for i in $(seq 1 90); do
    if [ -f "$PORT_JSON" ]; then
        break
    fi
    if ! kill -0 "$APP_PID" 2>/dev/null; then
        print_fail "应用已退出，port.json 未生成"
    fi
    sleep 0.5
done

[ -f "$PORT_JSON" ] || print_fail "port.json 未在 45 秒内出现"
print_ok "port.json 已生成"

# ---------- 4. 验证端口已切换 ----------
ACTUAL_PORT=$(python3 -c "import json; print(json.load(open('$PORT_JSON'))['port'])")
print_info "后端实际端口: $ACTUAL_PORT"

if [ "$ACTUAL_PORT" = "$OCCUPIED_PORT" ]; then
    print_fail "端口未切换: port.json 仍为 $OCCUPIED_PORT，应为其他端口"
fi
print_ok "端口已切换: $OCCUPIED_PORT → $ACTUAL_PORT"

# ---------- 5. 健康检查 ----------
HEALTH_URL="http://127.0.0.1:$ACTUAL_PORT/api/health"
print_info "健康检查: $HEALTH_URL ..."

for i in $(seq 1 10); do
    HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' "$HEALTH_URL" 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" = "200" ]; then
        break
    fi
    sleep 1
done

if [ "$HTTP_CODE" != "200" ]; then
    print_fail "健康检查失败: HTTP $HTTP_CODE"
fi

BODY=$(curl -s "$HEALTH_URL" 2>/dev/null)
echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('status')=='ok'" 2>/dev/null \
    || print_fail "健康检查返回体异常: $BODY"

print_ok "健康检查通过: $BODY"

# ---------- 6. 验证渲染层加载 ----------
RENDERER_MARKER="$BACKEND_DATA_DIR/renderer-ready"
print_info "等待渲染层加载标记 (最多 45 秒) ..."

for i in $(seq 1 90); do
    if [ -f "$RENDERER_MARKER" ]; then
        break
    fi
    if ! kill -0 "$APP_PID" 2>/dev/null; then
        print_fail "应用已退出，renderer-ready 未生成"
    fi
    sleep 0.5
done

[ -f "$RENDERER_MARKER" ] || print_fail "renderer-ready 未在 45 秒内出现"
print_ok "渲染层已加载 (marker: $(cat "$RENDERER_MARKER"))"

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN} Electron 集成 smoke test 全部通过${NC}"
echo -e "${GREEN}（端口碰撞 + 后端健康 + 渲染层加载）${NC}"
echo -e "${GREEN}========================================${NC}"
