#!/bin/bash
# ============================================================
#  量化交易系统 — 服务管理脚本
#  用法: ./manage.sh start|stop|restart|status|log
#        ./manage.sh start_api|stop_api|restart_api
#        ./manage.sh start_web|stop_web|restart_web
# ============================================================

APP_NAME="量化交易系统"
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$APP_DIR/.venv"

# API 服务配置
API_PID_FILE="$APP_DIR/.api.pid"
API_LOG_FILE="$APP_DIR/logs/api.log"
API_PORT=8001

# Web 服务配置
WEB_PID_FILE="$APP_DIR/.web.pid"
WEB_LOG_FILE="$APP_DIR/logs/web.log"
WEB_PORT=8002

# MySQL 配置
MYSQL_HOST="${MYSQL_HOST:-127.0.0.1}"
MYSQL_PORT="${MYSQL_PORT:-3306}"
MYSQL_USER="${MYSQL_USER:-root}"
MYSQL_PASSWORD="${MYSQL_PASSWORD:-root123456}"
MYSQL_DATABASE="${MYSQL_DATABASE:-quant_trading}"
MYSQL_START_TIMEOUT="${MYSQL_START_TIMEOUT:-30}"
MYSQL_CMD="${MYSQL_CMD:-/usr/local/mysql/bin/mysql}"
MYSQLADMIN_CMD="${MYSQLADMIN_CMD:-/usr/local/mysql/bin/mysqladmin}"
MYSQL_SERVER_CMD="${MYSQL_SERVER_CMD:-/usr/local/mysql/support-files/mysql.server}"

# 颜色
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[✓]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
log_error() { echo -e "${RED}[✗]${NC} $1"; }

mkdir -p "$APP_DIR/logs"

get_pid() {
    local pid_file=$1
    local port=$2
    if [ -f "$pid_file" ]; then
        local pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            echo "$pid"
            return 0
        fi
        rm -f "$pid_file"
    fi
    # 尝试通过端口查找
    local pid=$(lsof -i :$port -sTCP:LISTEN -t 2>/dev/null | head -1)
    if [ -n "$pid" ]; then
        echo "$pid"
        return 0
    fi
    return 1
}

find_cmd() {
    local default_path=$1
    local bin_name=$2
    if [ -x "$default_path" ]; then
        echo "$default_path"
        return 0
    fi
    command -v "$bin_name" 2>/dev/null
}

run_sudo() {
    if ! command -v sudo >/dev/null 2>&1; then
        return 1
    fi
    if [ -t 0 ]; then
        sudo "$@"
    else
        sudo -n "$@"
    fi
}

mysql_port_open() {
    if command -v nc >/dev/null 2>&1; then
        nc -z -w1 "$MYSQL_HOST" "$MYSQL_PORT" >/dev/null 2>&1
        return $?
    fi
    lsof -i :"$MYSQL_PORT" -sTCP:LISTEN -t >/dev/null 2>&1
}

mysql_ping() {
    local mysqladmin_cmd
    mysqladmin_cmd=$(find_cmd "$MYSQLADMIN_CMD" mysqladmin)
    if [ -z "$mysqladmin_cmd" ]; then
        return 1
    fi
    MYSQL_PWD="$MYSQL_PASSWORD" "$mysqladmin_cmd" \
        -h "$MYSQL_HOST" -P "$MYSQL_PORT" -u "$MYSQL_USER" ping >/dev/null 2>&1
}

mysql_ready() {
    local mysql_cmd
    mysql_cmd=$(find_cmd "$MYSQL_CMD" mysql)
    if [ -z "$mysql_cmd" ]; then
        mysql_ping
        return $?
    fi
    MYSQL_PWD="$MYSQL_PASSWORD" "$mysql_cmd" \
        -h "$MYSQL_HOST" -P "$MYSQL_PORT" -u "$MYSQL_USER" "$MYSQL_DATABASE" \
        -N -s -e "SELECT 1" >/dev/null 2>&1
}

start_mysql_server() {
    if [ -x "$MYSQL_SERVER_CMD" ]; then
        "$MYSQL_SERVER_CMD" start >/dev/null 2>&1 && return 0
        run_sudo "$MYSQL_SERVER_CMD" start
        return $?
    fi

    if command -v brew >/dev/null 2>&1; then
        brew services start mysql
        return $?
    fi

    if command -v systemctl >/dev/null 2>&1; then
        systemctl start mysql 2>/dev/null \
            || systemctl start mariadb 2>/dev/null \
            || run_sudo systemctl start mysql \
            || run_sudo systemctl start mariadb
        return $?
    fi

    if command -v service >/dev/null 2>&1; then
        service mysql start 2>/dev/null \
            || service mariadb start 2>/dev/null \
            || run_sudo service mysql start \
            || run_sudo service mariadb start
        return $?
    fi

    log_error "未找到可用的 MySQL 启动命令"
    return 1
}

ensure_mysql() {
    if mysql_ready; then
        log_info "MySQL 已运行 ($MYSQL_HOST:$MYSQL_PORT/$MYSQL_DATABASE)"
        return 0
    fi

    if mysql_port_open; then
        log_error "MySQL 端口已监听，但无法连接数据库: $MYSQL_DATABASE"
        log_error "请检查账号、密码或数据库是否存在"
        return 1
    fi

    log_warn "MySQL 未运行，正在自动启动..."
    if ! start_mysql_server; then
        log_error "MySQL 启动命令执行失败"
        return 1
    fi

    local waited=0
    while [ "$waited" -lt "$MYSQL_START_TIMEOUT" ]; do
        if mysql_ready; then
            log_info "MySQL 启动成功"
            return 0
        fi
        sleep 1
        waited=$((waited + 1))
    done

    log_error "MySQL 启动超时，请手动检查: $MYSQL_HOST:$MYSQL_PORT"
    return 1
}

# --- API 服务管理 ---
start_api() {
    if [ ! -d "$VENV_DIR" ]; then
        log_error "虚拟环境不存在: $VENV_DIR"
        return 1
    fi

    ensure_mysql || return 1

    local pid=$(get_pid "$API_PID_FILE" "$API_PORT")
    if [ -n "$pid" ]; then
        log_warn "API 服务已在运行 (PID: $pid, 端口: $API_PORT)"
        return 0
    fi

    log_info "启动 API 服务 (端口: $API_PORT) ..."
    source "$VENV_DIR/bin/activate"
    nohup python "$APP_DIR/server.py" >> "$API_LOG_FILE" 2>&1 &
    local new_pid=$!
    echo "$new_pid" > "$API_PID_FILE"

    sleep 2
    if kill -0 "$new_pid" 2>/dev/null; then
        log_info "API 服务启动成功 (PID: $new_pid)"
    else
        log_error "API 服务启动失败，请查看日志: tail -5 $API_LOG_FILE"
        rm -f "$API_PID_FILE"
        return 1
    fi
}

stop_api() {
    local pid=$(get_pid "$API_PID_FILE" "$API_PORT")
    if [ -z "$pid" ]; then
        log_warn "API 服务未在运行"
        return 0
    fi
    log_info "停止 API 服务 (PID: $pid) ..."
    kill "$pid" 2>/dev/null
    sleep 2
    if kill -0 "$pid" 2>/dev/null; then
        kill -9 "$pid" 2>/dev/null
    fi
    rm -f "$API_PID_FILE"
    log_info "API 服务已停止"
}

# --- Web 服务管理 ---
start_web() {
    local pid=$(get_pid "$WEB_PID_FILE" "$WEB_PORT")
    if [ -n "$pid" ]; then
        log_warn "Web 服务已在运行 (PID: $pid, 端口: $WEB_PORT)"
        return 0
    fi

    log_info "启动 Web 服务 (端口: $WEB_PORT) ..."
    cd "$APP_DIR/web" || return 1
    # 简单的 HTTP 服务器挂载前端静态文件
    nohup python3 -m http.server $WEB_PORT >> "$WEB_LOG_FILE" 2>&1 &
    local new_pid=$!
    echo "$new_pid" > "$WEB_PID_FILE"
    cd "$APP_DIR" || return 1

    sleep 2
    if kill -0 "$new_pid" 2>/dev/null; then
        log_info "Web 服务启动成功 (PID: $new_pid)"
    else
        log_error "Web 服务启动失败，请查看日志: tail -5 $WEB_LOG_FILE"
        rm -f "$WEB_PID_FILE"
        return 1
    fi
}

stop_web() {
    local pid=$(get_pid "$WEB_PID_FILE" "$WEB_PORT")
    if [ -z "$pid" ]; then
        log_warn "Web 服务未在运行"
        return 0
    fi
    log_info "停止 Web 服务 (PID: $pid) ..."
    kill "$pid" 2>/dev/null
    sleep 1
    if kill -0 "$pid" 2>/dev/null; then
        kill -9 "$pid" 2>/dev/null
    fi
    rm -f "$WEB_PID_FILE"
    log_info "Web 服务已停止"
}

# --- 综合管理 ---
start_all() {
    start_api || return 1
    echo -e "  ${CYAN}→${NC} 请直接访问(前端+后端集成): http://localhost:$API_PORT"
}

stop_all() {
    stop_web
    stop_api
}

status_all() {
    echo -e "${CYAN}=== 服务运行状态 ===${NC}"
    local api_pid=$(get_pid "$API_PID_FILE" "$API_PORT")
    if [ -n "$api_pid" ]; then
        echo -e "API 服务 : ${GREEN}运行中${NC} (PID: $api_pid, 端口: $API_PORT)"
    else
        echo -e "API 服务 : ${RED}已停止${NC}"
    fi

    local web_pid=$(get_pid "$WEB_PID_FILE" "$WEB_PORT")
    if [ -n "$web_pid" ]; then
        echo -e "Web 服务 : ${GREEN}运行中${NC} (PID: $web_pid, 端口: $WEB_PORT)"
    else
        echo -e "Web 服务 : ${RED}已停止${NC}"
    fi

    if mysql_ready; then
        echo -e "MySQL    : ${GREEN}运行中${NC} ($MYSQL_HOST:$MYSQL_PORT/$MYSQL_DATABASE)"
    else
        echo -e "MySQL    : ${RED}已停止或不可连接${NC} ($MYSQL_HOST:$MYSQL_PORT/$MYSQL_DATABASE)"
    fi
}

log_all() {
    if [ -f "$API_LOG_FILE" ]; then
        echo -e "\n${CYAN}=== API 服务日志 (最后20行) ===${NC}"
        tail -20 "$API_LOG_FILE"
    fi
    if [ -f "$WEB_LOG_FILE" ]; then
        echo -e "\n${CYAN}=== Web 服务日志 (最后20行) ===${NC}"
        tail -20 "$WEB_LOG_FILE"
    fi
}

# ===== 主入口 =====
case "$1" in
    start_api)   start_api ;;
    stop_api)    stop_api ;;
    restart_api) stop_api; sleep 1; start_api ;;

    start_web)   start_web ;;
    stop_web)    stop_web ;;
    restart_web) stop_web; sleep 1; start_web ;;

    start)       start_all ;;
    stop)        stop_all ;;
    restart)     stop_all; sleep 1; start_all ;;
    status)      status_all ;;
    log)         log_all ;;

    *)
        echo ""
        echo -e "  ${CYAN}$APP_NAME — 服务管理${NC}"
        echo ""
        echo "  用法: $0 {start|stop|restart|status|log}                (管理所有服务)"
        echo "        $0 {start_api|stop_api|restart_api}               (仅管理 API 服务)"
        echo "        $0 {start_web|stop_web|restart_web}               (仅管理 Web 服务)"
        echo ""
        exit 1
        ;;
esac
