#!/bin/bash
# 每日大盘复盘 (launchd 小时级触发 + 当日幂等门, 防本机重启漏跑)
# 门控: 工作日 16:35 后 + 当日 K 线已入库(防拿 T-1 数据出 T 日复盘)
cd /opt/quantitative-trading || exit 1

DOW=$(date +%u); HM=$(date +%H%M)
[ "$DOW" -gt 5 ] && exit 0
[ "$HM" -lt 1635 ] && exit 0

TODAY=$(date +%F)

# 幂等: 当日复盘已产出则退出
if .venv/bin/python - <<'PY'
import json, os, sys
from pathlib import Path
p = Path("research/daily_review/state.json")
if not p.exists():
    sys.exit(1)
try:
    d = json.loads(p.read_text(encoding="utf-8"))
except Exception:
    sys.exit(1)
sys.exit(0 if d.get("last_date") == os.environ.get("TODAY") else 1)
PY
then
    exit 0
fi

# 新鲜度断言: 当日 K 线未入库则等待下一个小时级 tick
MAXD=$(.venv/bin/python - <<'PY'
import sys
sys.path.insert(0, ".")
from config.settings import DataConfig
import pymysql
c = DataConfig()
conn = pymysql.connect(host=c.mysql_host, port=c.mysql_port, user=c.mysql_user,
                       password=c.mysql_password, database=c.mysql_database, charset="utf8mb4")
cur = conn.cursor()
cur.execute("SELECT MAX(date) FROM daily_kline")
r = cur.fetchone()
conn.close()
print(r[0] if r and r[0] else "")
PY
)
if [ "$MAXD" != "$TODAY" ]; then
    echo "$(date '+%F %T') [skip] 当日K线未入库 (daily_kline max=$MAXD)"
    exit 0
fi

export TODAY
.venv/bin/python -u scripts/review/run_review.py
rc=$?
echo "$(date '+%F %T') done rc=$rc date=$TODAY"
exit $rc
