#!/bin/bash
# ============================================================
#  一站式增量数据同步脚本
#
#  功能：
#    1. 查询 MySQL daily_kline 表中已有的最新日期
#    2. 仅导入 agzb2 目录中比该日期更新的 zip 文件
#    3. 增量同步 ST 标记（仅补新增日期区间）
#    4. 默认增量补齐 baostock_profit 与 eastmoney_roa 基本面数据
#
#  用法：
#    ./scripts/sync_daily_data.sh              # 日常增量（K线+ST+ROE/ROA，约30-60分钟）
#    ./scripts/sync_daily_data.sh --skip-roe   # 仅同步 K线+ST，跳过 ROE/ROA
#    ./scripts/sync_daily_data.sh --with-roe   # 兼容旧参数；当前默认已开启 ROE/ROA
#    ./scripts/sync_daily_data.sh --full-st    # 强制全量重刷 ST 标记（约30-60分钟）
#    ./scripts/sync_daily_data.sh --full-st --skip-roe
#    ./scripts/sync_daily_data.sh --skip-st  # baostock 不可用时，先补 K线+ROE/ROA
#    ./scripts/sync_daily_data.sh --st-from 2026-04-17  # 手动指定 ST 增量起点
#
#  预计耗时：
#    - 仅 K 线增量：几秒 ~ 1 分钟（取决于新增天数）
#    - ST 增量同步：2~5 分钟（仅查新增日期范围）
#    - ST 全量重刷：30~60 分钟
#    - ROE/ROA 基本面补齐：30~60 分钟
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"
DATA_DIR="$PROJECT_DIR/datas/agzb2"
MYSQL_CMD="/usr/local/mysql/bin/mysql"
MYSQL_ARGS="-u root -proot123456 quant_trading -N -s"

# 颜色
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[✓]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
log_error() { echo -e "${RED}[✗]${NC} $1"; }
log_step()  { echo -e "\n${CYAN}═══════════════════════════════════════${NC}"; echo -e "${CYAN}  $1${NC}"; echo -e "${CYAN}═══════════════════════════════════════${NC}"; }

usage() {
    cat <<EOF
用法:
  ./scripts/sync_daily_data.sh [--skip-roe] [--with-roe] [--full-st] [--skip-st] [--st-from YYYY-MM-DD]

参数:
  --skip-roe  跳过 baostock_profit 与 eastmoney_roa 基本面补齐
  --with-roe  兼容旧参数；当前默认已补齐 baostock_profit 与 eastmoney_roa
  --full-st   全量重刷 daily_kline.is_st 历史标记
  --skip-st   跳过 ST 同步；仅用于 baostock 不可用时临时补齐其他数据
  --st-from    手动指定 ST 增量起点；同步 date > YYYY-MM-DD 的 ST
  -h, --help  显示帮助
EOF
}

table_count_or_na() {
    local table_name="$1"
    local exists
    exists=$($MYSQL_CMD $MYSQL_ARGS -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = '$table_name'" 2>/dev/null)
    if [[ "$exists" == "1" ]]; then
        $MYSQL_CMD $MYSQL_ARGS -e "SELECT COUNT(*) FROM $table_name" 2>/dev/null
    else
        echo "N/A"
    fi
}

ensure_sync_state_table() {
    $MYSQL_CMD $MYSQL_ARGS -e "
        CREATE TABLE IF NOT EXISTS data_sync_state (
            key_name varchar(100) NOT NULL PRIMARY KEY,
            state_value varchar(255) NOT NULL,
            updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    " >/dev/null
}

get_sync_state() {
    local key_name="$1"
    $MYSQL_CMD $MYSQL_ARGS -e "SELECT state_value FROM data_sync_state WHERE key_name = '$key_name' LIMIT 1" 2>/dev/null
}

set_sync_state() {
    local key_name="$1"
    local state_value="$2"
    $MYSQL_CMD $MYSQL_ARGS -e "
        INSERT INTO data_sync_state (key_name, state_value)
        VALUES ('$key_name', '$state_value')
        ON DUPLICATE KEY UPDATE state_value = VALUES(state_value)
    " >/dev/null
}

FULL_ST=false
WITH_ROE=true
WITH_ROE_EXPLICIT=false
SKIP_ROE=false
SKIP_ST=false
ST_FROM=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --full-st)
            FULL_ST=true
            shift
            ;;
        --with-roe)
            WITH_ROE=true
            WITH_ROE_EXPLICIT=true
            shift
            ;;
        --skip-roe)
            SKIP_ROE=true
            shift
            ;;
        --skip-st)
            SKIP_ST=true
            shift
            ;;
        --st-from=*)
            ST_FROM="${1#*=}"
            shift
            ;;
        --st-from)
            if [[ $# -lt 2 ]]; then
                log_error "--st-from 缺少日期参数"
                usage
                exit 1
            fi
            ST_FROM="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            log_error "未知参数: $1"
            usage
            exit 1
            ;;
    esac
done

if [[ "$FULL_ST" == true && "$SKIP_ST" == true ]]; then
    log_error "--full-st 与 --skip-st 不能同时使用"
    usage
    exit 1
fi

if [[ "$SKIP_ROE" == true && "$WITH_ROE_EXPLICIT" == true ]]; then
    log_error "--with-roe 与 --skip-roe 不能同时使用"
    usage
    exit 1
fi

if [[ "$SKIP_ROE" == true ]]; then
    WITH_ROE=false
fi

if [[ "$FULL_ST" == true && -n "$ST_FROM" ]]; then
    log_error "--full-st 与 --st-from 不能同时使用"
    usage
    exit 1
fi

if [[ "$SKIP_ST" == true && -n "$ST_FROM" ]]; then
    log_error "--skip-st 与 --st-from 不能同时使用"
    usage
    exit 1
fi

if [[ -n "$ST_FROM" && ! "$ST_FROM" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
    log_error "--st-from 日期格式错误: $ST_FROM"
    usage
    exit 1
fi

ST_STATE_KEY="daily_kline_st_synced_to"
STAGE_TOTAL=3
if [[ "$WITH_ROE" == true ]]; then
    STAGE_TOTAL=5
fi

# 检查前置依赖
if [[ ! -f "$VENV_PYTHON" ]]; then
    log_error "虚拟环境不存在: $VENV_PYTHON"
    exit 1
fi

if [[ ! -d "$DATA_DIR" ]]; then
    log_error "数据目录不存在: $DATA_DIR"
    exit 1
fi

if ! nc -z -w1 127.0.0.1 3306 2>/dev/null; then
    log_error "MySQL 未运行，请先启动数据库"
    exit 1
fi

# ── 阶段 1：查询数据库最新日期 ──────────────────────
log_step "阶段 1/$STAGE_TOTAL：查询数据库现有数据边界"

DB_MAX_DATE=$($MYSQL_CMD $MYSQL_ARGS -e "SELECT MAX(date) FROM daily_kline" 2>/dev/null)
DB_ROW_COUNT=$($MYSQL_CMD $MYSQL_ARGS -e "SELECT COUNT(*) FROM daily_kline" 2>/dev/null)

if [[ -z "$DB_MAX_DATE" || "$DB_MAX_DATE" == "NULL" ]]; then
    log_warn "数据库为空，将执行全量导入"
    DB_MAX_DATE="1970-01-01"
fi

log_info "数据库最新日期: $DB_MAX_DATE"
log_info "数据库总行数: $(printf "%'d" $DB_ROW_COUNT)"

# ── 阶段 2：筛选并导入增量 zip ────────────────────
log_step "阶段 2/$STAGE_TOTAL：筛选增量 K 线数据"

log_info "直接传递增量日期 $DB_MAX_DATE 给 Python 脚本，由其内部过滤"
$VENV_PYTHON "$SCRIPT_DIR/import_agzb2_data.py" --after-date "$DB_MAX_DATE"

# 获取导入后的最新日期
NEW_MAX_DATE=$($MYSQL_CMD $MYSQL_ARGS -e "SELECT MAX(date) FROM daily_kline WHERE date REGEXP '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'" 2>/dev/null)
NEW_ROW_COUNT=$($MYSQL_CMD $MYSQL_ARGS -e "SELECT COUNT(*) FROM daily_kline" 2>/dev/null)
ADDED=$((NEW_ROW_COUNT - DB_ROW_COUNT))

if [[ $ADDED -gt 0 ]]; then
    log_info "导入完成: 新增 $(printf "%'d" $ADDED) 行，最新日期更新至 $NEW_MAX_DATE"
    TOTAL_NEW=$ADDED
else
    log_info "无新增数据，数据库已是最新 ($DB_MAX_DATE)"
    TOTAL_NEW=0
fi

# ── 阶段 3：增量同步 ST 标记 ──────────────────────
log_step "阶段 3/$STAGE_TOTAL：同步 ST 标记"

if [[ "$SKIP_ST" == true ]]; then
    log_warn "已指定 --skip-st，跳过 ST 同步；当前 daily_kline.is_st 不会更新"
elif [[ "$FULL_ST" == true ]]; then
    log_warn "使用 --full-st 参数，执行全量 ST 标记重刷"
    echo ""
    echo "⏱️ 预计运行时间: ~30-60分钟 | CPU占用: 低 | 内存: ~200MB"
    echo ""
    $VENV_PYTHON "$SCRIPT_DIR/sync_st_status.py"
    ensure_sync_state_table
    set_sync_state "$ST_STATE_KEY" "$NEW_MAX_DATE"
    log_info "ST 同步状态已更新至 $NEW_MAX_DATE"
else
    ensure_sync_state_table

    if [[ -n "$ST_FROM" ]]; then
        ST_SYNC_START="$ST_FROM"
        log_warn "使用手动 ST 增量起点: $ST_SYNC_START"
    else
        ST_SYNC_START=$(get_sync_state "$ST_STATE_KEY")
        if [[ -z "$ST_SYNC_START" && $TOTAL_NEW -gt 0 ]]; then
            ST_SYNC_START="$DB_MAX_DATE"
            log_warn "未找到 ST 同步状态，使用本次 K线增量前日期作为起点: $ST_SYNC_START"
        fi
    fi

    if [[ -z "$ST_SYNC_START" ]]; then
        log_warn "未找到 ST 增量起点，且本次无新增 K线；跳过 ST。首次校准请运行 --full-st 或 --st-from=YYYY-MM-DD"
    elif [[ "$ST_SYNC_START" < "$NEW_MAX_DATE" ]]; then
        log_info "ST 增量模式：同步 ($ST_SYNC_START, $NEW_MAX_DATE] 的 ST 标记"
        $VENV_PYTHON "$SCRIPT_DIR/sync_st_status.py" --from-date "$ST_SYNC_START" --to-date "$NEW_MAX_DATE"
        set_sync_state "$ST_STATE_KEY" "$NEW_MAX_DATE"
        log_info "ST 同步状态已更新至 $NEW_MAX_DATE"
    else
        log_info "ST 已同步到 $ST_SYNC_START，无新增日期需要处理"
        if [[ -n "$ST_FROM" ]]; then
            set_sync_state "$ST_STATE_KEY" "$NEW_MAX_DATE"
            log_info "ST 同步状态已标记为 $NEW_MAX_DATE"
        fi
    fi
fi

# ── 阶段 4/5：可选补齐基本面数据 ────────────────
if [[ "$WITH_ROE" == true ]]; then
    log_step "阶段 4/$STAGE_TOTAL：补齐 baostock_profit 季报"
    echo ""
    echo "⏱️ 预计运行时间: ~10-30分钟 | CPU占用: 中/低 | 内存: ~200-500MB"
    echo ""
    $VENV_PYTHON -u "$SCRIPT_DIR/fetch_roe_akshare.py"

    log_step "阶段 5/$STAGE_TOTAL：补齐 eastmoney_roa 报告期数据"
    echo ""
    echo "⏱️ 预计运行时间: ~20-40分钟 | CPU占用: 中/低 | 内存: ~200-500MB"
    echo ""
    $VENV_PYTHON -u "$SCRIPT_DIR/sync_eastmoney_roa.py"
else
    log_info "已指定 --skip-roe，跳过 baostock_profit 与 eastmoney_roa 基本面补齐"
fi

# ── 最终汇总 ──────────────────────────────────────
log_step "全部完成"

FINAL_MAX=$($MYSQL_CMD $MYSQL_ARGS -e "SELECT MAX(date) FROM daily_kline" 2>/dev/null)
FINAL_COUNT=$($MYSQL_CMD $MYSQL_ARGS -e "SELECT COUNT(*) FROM daily_kline" 2>/dev/null)
FINAL_SYMBOLS=$($MYSQL_CMD $MYSQL_ARGS -e "SELECT COUNT(DISTINCT symbol) FROM daily_kline" 2>/dev/null)
ST_COUNT=$($MYSQL_CMD $MYSQL_ARGS -e "SELECT COUNT(*) FROM daily_kline WHERE is_st = 1" 2>/dev/null)
ROE_COUNT=$(table_count_or_na "baostock_profit")
EASTMONEY_ROA_COUNT=$(table_count_or_na "eastmoney_roa")

echo ""
echo "  数据库统计:"
echo "    K 线总行数:   $(printf "%'d" $FINAL_COUNT)"
echo "    股票数:       $FINAL_SYMBOLS"
echo "    日期范围:     ~ $FINAL_MAX"
echo "    ST 标记行数:  $(printf "%'d" $ST_COUNT)"
if [[ "$ROE_COUNT" == "N/A" ]]; then
    echo "    ROE 季报行数: N/A"
else
    echo "    ROE 季报行数: $(printf "%'d" $ROE_COUNT)"
fi
if [[ "$EASTMONEY_ROA_COUNT" == "N/A" ]]; then
    echo "    东财 ROE/ROA: N/A"
else
    echo "    东财 ROE/ROA: $(printf "%'d" $EASTMONEY_ROA_COUNT)"
fi
echo ""
if [[ "$ST_COUNT" == "0" ]]; then
    log_warn "ST 标记行数为 0，ST 数据不可信。完成 --full-st 前不要用该库做正式回测/实盘模拟。"
else
    log_info "数据已全部就绪，可直接运行实盘模拟任务"
fi
