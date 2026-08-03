#!/bin/bash
# 修改前自动备份脚本

if [ $# -eq 0 ]; then
    echo "用法：$0 <文件路径>"
    exit 1
fi

FILE=$1
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP="${FILE}.backup_${TIMESTAMP}"

if [ -f "$FILE" ]; then
    cp "$FILE" "$BACKUP"
    echo "✅ 已备份：$BACKUP"

    # 验证备份
    if diff "$FILE" "$BACKUP" > /dev/null; then
        echo "✅ 备份验证通过"
    else
        echo "❌ 备份验证失败"
        exit 1
    fi
else
    echo "❌ 文件不存在：$FILE"
    exit 1
fi
