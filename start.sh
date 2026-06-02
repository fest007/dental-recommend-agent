#!/bin/bash
# ============================================================
#  牙科设备推荐Agent — 一键启动脚本
#  用法: ./start.sh
# ============================================================

set -e

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$PROJECT_DIR/backend"
FRONTEND_DIR="$PROJECT_DIR/frontend"
LOG_DIR="$PROJECT_DIR/logs"
BACKEND_PORT=8765
FRONTEND_PORT=5173

mkdir -p "$LOG_DIR"

# 清理上次残留的进程
lsof -ti :"$BACKEND_PORT" 2>/dev/null | xargs kill 2>/dev/null
lsof -ti :"$FRONTEND_PORT" 2>/dev/null | xargs kill 2>/dev/null

# 清理函数
cleanup() {
    echo ""
    echo -e "${YELLOW}正在停止服务...${NC}"

    # Kill backend and all its children
    if [ -n "$BACKEND_PID" ]; then
        pkill -P "$BACKEND_PID" 2>/dev/null  # kill children first
        kill "$BACKEND_PID" 2>/dev/null
        echo -e "  ${GREEN}✓${NC} 后端已停止"
    fi

    # Kill frontend and all its children
    if [ -n "$FRONTEND_PID" ]; then
        pkill -P "$FRONTEND_PID" 2>/dev/null
        kill "$FRONTEND_PID" 2>/dev/null
        echo -e "  ${GREEN}✓${NC} 前端已停止"
    fi

    # Safety net: kill anything still on our ports
    lsof -ti :"$BACKEND_PORT" 2>/dev/null | xargs kill 2>/dev/null
    lsof -ti :"$FRONTEND_PORT" 2>/dev/null | xargs kill 2>/dev/null

    echo -e "${GREEN}已退出。${NC}"
    exit 0
}

trap cleanup SIGINT SIGTERM

# 端口检查
check_port() {
    local port=$1
    if lsof -i :"$port" -sTCP:LISTEN -t >/dev/null 2>&1; then
        echo -e "${RED}✗ 端口 $port 已被占用${NC}"
        echo -e "  请先关闭占用该端口的进程，或修改 config.yaml 中的端口配置"
        return 1
    fi
    return 0
}

# ============================================================
#  检查环境
# ============================================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  牙科设备推荐Agent — 启动中${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Python
if command -v python3 &>/dev/null; then
    PYTHON=python3
elif command -v python &>/dev/null; then
    PYTHON=python
else
    echo -e "${RED}✗ 未找到 Python，请先安装 Python 3.11+${NC}"
    exit 1
fi
PY_VERSION=$($PYTHON --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')
echo -e "  ${GREEN}✓${NC} Python $PY_VERSION"

# Node.js
if ! command -v node &>/dev/null; then
    echo -e "${RED}✗ 未找到 Node.js，请先安装 Node.js 18+${NC}"
    exit 1
fi
echo -e "  ${GREEN}✓${NC} Node.js $(node --version)"

# npm
if ! command -v npm &>/dev/null; then
    echo -e "${RED}✗ 未找到 npm${NC}"
    exit 1
fi
echo -e "  ${GREEN}✓${NC} npm $(npm --version)"

# 端口
check_port "$BACKEND_PORT" || exit 1
check_port "$FRONTEND_PORT" || exit 1
echo -e "  ${GREEN}✓${NC} 端口 $BACKEND_PORT / $FRONTEND_PORT 可用"

echo ""

# ============================================================
#  安装依赖
# ============================================================

# Python 依赖 — 每次都跑 install，已装的秒过
echo -e "${BLUE}[1/2] 检查 Python 依赖...${NC}"
cd "$BACKEND_DIR"
$PYTHON -m pip install -r requirements.txt 2>&1 | tail -5
if [ $? -ne 0 ]; then
    echo -e "  ${RED}✗${NC} Python 依赖安装失败，请检查上方错误信息"
    exit 1
fi
echo -e "  ${GREEN}✓${NC} Python 依赖就绪"

# Node.js 依赖
echo -e "${BLUE}[2/2] 检查 Node.js 依赖...${NC}"
cd "$FRONTEND_DIR"
NEED_INSTALL=false
if [ ! -d "node_modules" ]; then
    NEED_INSTALL=true
elif ! node -e "require('less')" 2>/dev/null; then
    NEED_INSTALL=true
fi
if [ "$NEED_INSTALL" = true ]; then
    echo -e "  ${YELLOW}→${NC} 安装前端依赖..."
    npm install 2>&1 | tail -5
fi
echo -e "  ${GREEN}✓${NC} 前端依赖就绪"

echo ""

# ============================================================
#  启动服务
# ============================================================

cd "$PROJECT_DIR"

# 启动后端
echo -e "${BLUE}[启动] 后端服务 (FastAPI :$BACKEND_PORT)...${NC}"
cd "$BACKEND_DIR"
DENTAL_AGENT_DEV=1 $PYTHON main.py > "$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!
echo -e "  ${GREEN}✓${NC} 后端 PID: $BACKEND_PID"
echo -e "  ${GREEN}✓${NC} 日志: $LOG_DIR/backend.log"

# 等待后端就绪
echo -n "  等待后端就绪"
for i in $(seq 1 30); do
    if curl -s "http://localhost:$BACKEND_PORT/api/health" >/dev/null 2>&1; then
        echo -e " ${GREEN}✓${NC}"
        break
    fi
    echo -n "."
    sleep 1
    if [ "$i" = "30" ]; then
        echo -e " ${RED}✗${NC}"
        echo -e "${RED}后端启动超时，请检查 $LOG_DIR/backend.log${NC}"
        cleanup
    fi
done

# 启动前端
echo -e "${BLUE}[启动] 前端服务 (Vite :$FRONTEND_PORT)...${NC}"
cd "$FRONTEND_DIR"
npm run dev > "$LOG_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!
echo -e "  ${GREEN}✓${NC} 前端 PID: $FRONTEND_PID"
echo -e "  ${GREEN}✓${NC} 日志: $LOG_DIR/frontend.log"

# 等待前端就绪
echo -n "  等待前端就绪"
for i in $(seq 1 30); do
    if curl -s "http://localhost:$FRONTEND_PORT" >/dev/null 2>&1; then
        echo -e " ${GREEN}✓${NC}"
        break
    fi
    echo -n "."
    sleep 1
    if [ "$i" = "30" ]; then
        echo -e " ${RED}✗${NC}"
        echo -e "${RED}前端启动超时，请检查 $LOG_DIR/frontend.log${NC}"
        cleanup
    fi
done

# ============================================================
#  完成
# ============================================================

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  ✓ 全部就绪！${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  前端:      ${BLUE}http://localhost:$FRONTEND_PORT${NC}"
echo -e "  后端 API:  ${BLUE}http://localhost:$BACKEND_PORT${NC}"
echo -e "  健康检查:  ${BLUE}http://localhost:$BACKEND_PORT/api/health${NC}"
echo ""
echo -e "  ${YELLOW}按 Ctrl+C 停止所有服务${NC}"
echo ""

# 打开浏览器
if command -v open &>/dev/null; then
    open "http://localhost:$FRONTEND_PORT"
elif command -v xdg-open &>/dev/null; then
    xdg-open "http://localhost:$FRONTEND_PORT"
fi

# 保持脚本运行
wait
