#!/bin/bash
# =============================================================================
# 后端端口碰撞 Smoke Test
# 验证：首选端口被占用时，后端切到新端口、写出 port.json、健康检查通过
# 用法：从项目根目录运行，需要先完成 PyInstaller 打包
# =============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

print_ok()   { echo -e "${GREEN}✓${NC} $1"; }
print_fail() { echo -e "${RED}✗${NC} $1"; exit 1; }
print_info() { echo -e "${YELLOW}→${NC} $1"; }

# 定位后端可执行文件
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_BIN="$PROJECT_ROOT/backend/dist/backend/backend"

[ -f "$BACKEND_BIN" ] || print_fail "后端可执行文件不存在: $BACKEND_BIN（请先运行 pyinstaller）"

OCCUPIED_PORT=8765
TMPDIR="$(mktemp -d)"
BACKEND_PID=""

cleanup() {
    [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null || true
    [ -n "$PORT_BLOCKER_PID" ] && kill "$PORT_BLOCKER_PID" 2>/dev/null || true
    rm -rf "$TMPDIR"
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
time.sleep(120)
" &
PORT_BLOCKER_PID=$!

# 等待端口占用者就绪
for i in $(seq 1 10); do
    if python3 -c "import socket; s=socket.socket(); s.settimeout(1); s.connect(('127.0.0.1',$OCCUPIED_PORT)); s.close()" 2>/dev/null; then
        break
    fi
    sleep 0.3
done
print_ok "端口 $OCCUPIED_PORT 已被占用 (PID $PORT_BLOCKER_PID)"

# ---------- 2. 启动后端 ----------
print_info "启动后端 (DENTAL_AGENT_DATA_DIR=$TMPDIR) ..."

DENTAL_AGENT_DATA_DIR="$TMPDIR" "$BACKEND_BIN" &
BACKEND_PID=$!
print_ok "后端已启动 (PID $BACKEND_PID)"

# ---------- 3. 等待 port.json ----------
PORT_JSON="$TMPDIR/port.json"
print_info "等待 port.json 出现 ..."

for i in $(seq 1 30); do
    if [ -f "$PORT_JSON" ]; then
        break
    fi
    sleep 0.5
done

[ -f "$PORT_JSON" ] || print_fail "port.json 未在 15 秒内出现"
print_ok "port.json 已生成"

# ---------- 4. 验证端口 ----------
ACTUAL_PORT=$(python3 -c "import json; print(json.load(open('$PORT_JSON'))['port'])")
print_info "port.json 中的端口: $ACTUAL_PORT"

if [ "$ACTUAL_PORT" = "$OCCUPIED_PORT" ]; then
    print_fail "端口未切换: port.json 仍为 $OCCUPIED_PORT，应为其他端口"
fi
print_ok "端口已切换: $OCCUPIED_PORT → $ACTUAL_PORT"

# ---------- 5. 健康检查 ----------
HEALTH_URL="http://127.0.0.1:$ACTUAL_PORT/api/health"
print_info "健康检查: $HEALTH_URL ..."

for i in $(seq 1 20); do
    HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' "$HEALTH_URL" 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" = "200" ]; then
        break
    fi
    sleep 1
done

if [ "$HTTP_CODE" != "200" ]; then
    print_fail "健康检查失败: HTTP $HTTP_CODE (URL: $HEALTH_URL)"
fi

BODY=$(curl -s "$HEALTH_URL" 2>/dev/null)
echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('status')=='ok', f'unexpected body: {d}'" 2>/dev/null \
    || print_fail "健康检查返回体异常: $BODY"

print_ok "健康检查通过: $HEALTH_URL → $BODY"

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN} 后端端口碰撞 smoke test 全部通过${NC}"
echo -e "${GREEN}========================================${NC}"
