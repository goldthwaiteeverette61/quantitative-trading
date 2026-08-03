# 综合量化交易系统

融合经典交易理论的量化交易系统，当前包含 Alexander Elder 三重滤网和海龟交易法两套策略。

## 理论基础

| 大师 | 代表作 | 核心贡献 |
|------|--------|----------|
| Alexander Elder | 《以交易为生》 | 三重滤网交易系统、2%/6% 风控规则 |
| Van K. Tharp | 《通往财务自由之路》 | R 倍数、头寸管理、SQN 系统质量数 |
| 海龟交易法 | 《海龟交易法则》 | Donchian 通道突破、ATR 仓位管理 |

## 快速开始

```bash
cd /opt/quantitative-trading
source .venv/bin/activate


# 三重滤网分析
python main.py analyze -s 000001

# 回测（默认三重滤网）
python main.py backtest -s 000001 --strategy triple --days 365

# 海龟策略回测
python main.py backtest -s 000001 --strategy turtle --days 365

# 多策略对比
python main.py compare -s 000001 --days 365

# 模拟交易
python main.py paper -s 000001 --strategy triple

# 实时行情
python main.py quote 000001
```

## 项目结构

```
├── config/           # 配置（策略参数、风控规则）
├── data/             # 数据获取（新浪/腾讯行情）
├── indicators/       # 技术指标库（EMA, MACD, RSI, Force Index, ATR, Donchian, KDJ）
├── strategy/         # 策略引擎（三重滤网、海龟）
├── risk/             # 风控体系（头寸管理、账户风控）
├── backtest/         # 回测引擎
├── paper_trading/    # 模拟交易
├── analytics/        # 绩效分析（含 SQN、R 倍数分布）
├── tests/            # 测试
└── main.py           # CLI 入口
```

## 风控体系

- **2% 规则**（Elder）：单笔交易最大亏损不超过总资金 2%
- **6% 规则**（Elder）：所有持仓总风险不超过总资金 6%
- **R 倍数**（Tharp）：统一用初始风险衡量盈亏
- **SQN**（Tharp）：系统质量数，评估策略整体质量
