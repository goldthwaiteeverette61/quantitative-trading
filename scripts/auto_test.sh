#!/bin/bash
# 自动化测试脚本 - AI 修改后必跑

set -e
cd /opt/quantitative-trading

log() {
    echo "[$(date '+%H:%M:%S')] $1"
}

section() {
    echo ""
    log "════════════════════════════════"
    log "  $1"
    log "════════════════════════════════"
}

section "🚀 开始自动化测试"

# 测试 1: 语法检查
log "📝 语法检查..."
if .venv/bin/python -m py_compile server.py main.py; then
    log "✅ 语法检查通过"
else
    log "❌ 语法检查失败"
    exit 1
fi

# 测试 2: 导入测试
log "📦 模块导入..."
if .venv/bin/python -c "import server; import main"; then
    log "✅ 导入测试通过"
else
    log "❌ 导入测试失败"
    exit 1
fi

# 测试 3: 服务重启
log "🔄 重启服务..."
pkill -f "python.*server.py" || true
sleep 1
.venv/bin/python server.py > logs/web.log 2>&1 &
SERVER_PID=$!
sleep 3

if ps -p $SERVER_PID > /dev/null; then
    log "✅ 服务启动成功 (PID: $SERVER_PID)"
else
    log "❌ 服务启动失败"
    exit 1
fi

# 测试 4: API 检查
log "🌐 API 接口检查..."
if curl -s http://localhost:8001/api/portfolio/strategies | grep -q "strategies"; then
    log "✅ API 接口正常"
else
    log "❌ API 接口异常"
    kill $SERVER_PID 2>/dev/null
    exit 1
fi

# 测试 5: CLI 检查
log "💻 CLI 命令检查..."
if .venv/bin/python main.py --help > /dev/null 2>&1; then
    log "✅ CLI 命令正常"
else
    log "❌ CLI 命令异常"
    kill $SERVER_PID 2>/dev/null
    exit 1
fi

section "✅ 所有测试通过！"
log "🔧 服务 PID: $SERVER_PID"
disown $SERVER_PID
log "🎉 测试完成，服务继续运行"
