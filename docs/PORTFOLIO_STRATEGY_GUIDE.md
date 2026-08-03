# 组合策略完全指南

## 一、组合策略 vs 单股策略

### 核心区别

| 维度 | 单股策略 (V1) | 组合策略 (V2 Portfolio) |
|------|--------------|------------------------|
| **代表策略** | 三重滤网、海龟交易 | 低 PE 轮动、小市值、动量因子 |
| **交易标的** | 单只股票 | 全市场股票组合 |
| **输出形式** | 买卖信号 (buy/sell/hold) | 目标权重 ({"000001": 0.05, ...}) |
| **调仓频率** | 每日判断 | 定期调仓 (周/双周/月) |
| **持仓数量** | 1-3 只 | 10-30 只 |
| **风险分散** | 低 | 高 |
| **适合资金** | 小资金 (<50 万) | 大资金 (>50 万) |

### 使用场景

**单股策略适合：**
- 资金量小 (<50 万)
- 追求高弹性
- 有时间盯盘
- 能接受较大回撤

**组合策略适合：**
- 资金量大 (>50 万)
- 追求稳健收益
- 没时间盯盘
- 风险厌恶型

---

## 二、组合策略架构

### V2 Portfolio 架构图

```
┌─────────────────────────────────────────────────────┐
│              PortfolioStrategy (抽象基类)             │
├─────────────────────────────────────────────────────┤
│  - name: 策略名称                                    │
│  - category: 策略类别                                │
│  - description: 策略描述                             │
│  - author: 策略作者                                  │
│  - rebalance_config: 调仓配置                        │
├─────────────────────────────────────────────────────┤
│  + get_target_weights(date, universe_df) → dict     │
│  + get_rebalance_dates(trading_dates) → list        │
│  + meta_dict() → dict                               │
└─────────────────────────────────────────────────────┘
                          ↑
        ┌─────────────────┼─────────────────┐
        ↓                 ↓                 ↓
┌───────────────┐ ┌───────────────┐ ┌───────────────┐
│ LowPERotation │ │ SmallCap      │ │ Momentum      │
│ 低 PE 轮动      │ │ 小市值        │ │ 动量因子      │
└───────────────┘ └───────────────┘ └───────────────┘
```

### 核心接口

```python
from strategy.portfolio_base import PortfolioStrategy, RebalanceConfig
from abc import abstractmethod

class MyStrategy(PortfolioStrategy):
    """自定义组合策略"""

    @property
    def name(self) -> str:
        return "我的策略"

    @abstractmethod
    def get_target_weights(
        self,
        date: pd.Timestamp,
        universe_df: pd.DataFrame,
    ) -> dict[str, float]:
        """
        计算目标持仓权重

        Args:
            date: 调仓日期
            universe_df: 全市场截面数据
                - index: 股票代码
                - columns: name, close, market_cap, pe, pb, roe, momentum_20d...

        Returns:
            权重字典 {"000001": 0.05, "600519": 0.05, ...}
            所有权重之和 ≤ 1.0
        """
        ...
```

---

## 三、已实现策略详解

### 1. 低 PE 轮动策略

**策略逻辑：**
```python
1. 筛选条件：
   - PE < 15 (低估)
   - ROE > 10% (盈利质量)
   - 非 ST、非停牌

2. 买入规则：
   - 按 PE 升序排序
   - 选前 10 只
   - 等权重配置

3. 卖出规则：
   - PE > 25 (高估)
   - 跌出前 20 名
   - 调仓日自动替换
```

**代码示例：**
```python
from strategy.low_pe_rotation import LowPERotationStrategy

strategy = LowPERotationStrategy(
    max_pe=15.0,        # 最大 PE
    min_roe=10.0,       # 最小 ROE
    sell_pe=25.0,       # 卖出 PE
    top_n=10,           # 持仓数量
)

# 获取调仓日权重
weights = strategy.get_target_weights(date, universe_df)
```

**适合场景：**
- 价值投资
- 长线持有
- 熊市/震荡市

---

### 2. 小市值策略

**策略逻辑：**
```python
1. 筛选条件：
   - 总市值 < 50 亿
   - 非 ST、非科创板
   - 上市 > 1 年

2. 买入规则：
   - 按市值升序排序
   - 选前 20 只
   - 等权重配置

3. 调仓频率：
   - 每月调仓一次
   - 自动替换新的小市值股票
```

**代码示例：**
```python
from strategy.small_cap import SmallCapStrategy

strategy = SmallCapStrategy(
    max_market_cap=50.0,  # 最大市值 (亿)
    top_n=20,             # 持仓数量
    exclude_kcb=True,     # 排除科创板
)

weights = strategy.get_target_weights(date, universe_df)
```

**适合场景：**
- 追求高弹性
- 牛市环境
- 能承受较大波动

---

### 3. 动量因子策略

**策略逻辑：**
```python
1. 筛选条件：
   - 非 ST、非停牌
   - 日均成交 > 1 亿

2. 动量计算：
   - 20 日涨幅：40% 权重
   - 60 日涨幅：40% 权重
   - 120 日涨幅：20% 权重

3. 买入规则：
   - 按动量得分排序
   - 选前 15 只
   - 等权重配置

4. 止损规则：
   - 单只股票回撤 > 15% 自动剔除
```

**代码示例：**
```python
from strategy.momentum_factor import MomentumFactorStrategy

strategy = MomentumFactorStrategy(
    top_n=15,                    # 持仓数量
    weights=[0.4, 0.4, 0.2],    # 动量权重
    stop_loss=0.15,             # 止损线
)

weights = strategy.get_target_weights(date, universe_df)
```

**适合场景：**
- 趋势跟踪
- 牛市环境
- 追求超额收益

---

## 四、回测实战

### 单策略回测

```bash
cd /opt/quantitative-trading
source .venv/bin/activate

# 低 PE 轮动回测
python main.py backtest -s 000001 --strategy low_pe_rotation --days 365

# 小市值回测
python main.py backtest -s 000001 --strategy small_cap --days 365

# 动量因子回测
python main.py backtest -s 000001 --strategy momentum_factor --days 365
```

### 多策略对比

```bash
# 对比所有组合策略
python main.py compare --strategies low_pe_rotation,small_cap,momentum_factor --days 365
```

### 回测指标解读

| 指标 | 含义 | 优秀标准 |
|------|------|---------|
| **总收益率** | 策略总收益 | > 30%/年 |
| **年化收益** | 年化收益率 | > 20% |
| **最大回撤** | 最大亏损幅度 | < 20% |
| **夏普比率** | 风险调整后收益 | > 1.5 |
| **胜率** | 盈利交易占比 | > 55% |
| **盈亏比** | 平均盈利/平均亏损 | > 1.5 |
| **SQN** | 系统质量数 | > 2.5 |

---

## 五、实盘配置

### 资金分配建议

| 策略类型 | 资金比例 | 说明 |
|---------|---------|------|
| 低 PE 轮动 | 40% | 稳健底仓 |
| 小市值 | 30% | 进攻仓位 |
| 动量因子 | 20% | 趋势增强 |
| 现金 | 10% | 机动资金 |

### 调仓执行

```bash
# 生成当期调仓清单
python main.py rebalance --strategy low_pe_rotation --output rebalance_list.csv

# 输出示例：
# 股票代码，股票名称，目标权重，目标金额，操作
# 000001,平安银行，0.05,50000,BUY
# 600519,贵州茅台，0.05,50000,BUY
# 000002,万科 A，0.00,0,SELL
```

### 风控规则

```python
# 单只股票最大仓位
max_single_weight = 0.10  # 10%

# 单一行业最大仓位
max_sector_weight = 0.30  # 30%

# 总体仓位控制
min_position = 0.60  # 最低 60% 仓位
max_position = 0.95  # 最高 95% 仓位
```

---

## 六、自定义组合策略

### 步骤 1：继承基类

```python
# strategy/my_custom_strategy.py
from strategy.portfolio_base import PortfolioStrategy, RebalanceConfig
import pandas as pd

class MyCustomStrategy(PortfolioStrategy):
    """自定义组合策略"""

    def __init__(self):
        super().__init__(RebalanceConfig(
            frequency="monthly",
            max_holdings=20,
            min_weight=0.01,
        ))

    @property
    def name(self) -> str:
        return "我的自定义策略"

    @property
    def description(self) -> str:
        return "结合低 PE 和高动量的复合策略"

    @property
    def author(self) -> str:
        return "你的名字"

    def get_target_weights(
        self,
        date: pd.Timestamp,
        universe_df: pd.DataFrame,
    ) -> dict[str, float]:
        # 1. 筛选低 PE 股票
        low_pe = universe_df[universe_df['pe'] < 15]

        # 2. 筛选高动量股票
        high_momentum = universe_df[universe_df['momentum_20d'] > 10]

        # 3. 取交集
        candidates = low_pe.intersection(high_momentum)

        # 4. 综合评分
        candidates_df = universe_df.loc[candidates]
        candidates_df['score'] = (
            -candidates_df['pe'].rank(pct=True) * 0.5 +
            candidates_df['momentum_20d'].rank(pct=True) * 0.5
        )

        # 5. 选前 20 名
        top20 = candidates_df.nlargest(20, 'score')

        # 6. 等权重配置
        weight = 1.0 / len(top20)
        return {code: weight for code in top20.index}
```

### 步骤 2：注册策略

```bash
# 自动注册（运行时自动）
python main.py strategy register --class strategy.my_custom_strategy.MyCustomStrategy
```

### 步骤 3：回测验证

```bash
python main.py backtest --strategy my_custom_strategy --days 365
```

---

## 七、策略组合优化

### 相关性分析

```python
from analytics.portfolio_evaluator import PortfolioEvaluator

evaluator = PortfolioEvaluator()

# 计算策略相关性矩阵
corr_matrix = evaluator.calculate_strategy_correlation([
    'low_pe_rotation',
    'small_cap',
    'momentum_factor',
])

print(corr_matrix)
```

### 最优权重配置

```python
# 使用均值 - 方差优化
optimal_weights = evaluator.optimize_portfolio(
    strategies=['low_pe_rotation', 'small_cap', 'momentum_factor'],
    objective='max_sharpe',  # 最大化夏普比率
)

print(optimal_weights)
# 输出：{'low_pe_rotation': 0.5, 'small_cap': 0.3, 'momentum_factor': 0.2}
```

---

## 八、常见问题

### Q1: 组合策略需要多少资金？

**A:** 建议最低 50 万，原因：
- 分散 20 只股票，每只 2.5 万
- 考虑交易成本（佣金最低 5 元）
- 避免过度分散

### Q2: 调仓频率多少合适？

**A:** 取决于策略：
- 低 PE 轮动：月度（估值变化慢）
- 小市值：月度（市值变化快）
- 动量因子：双周（动量变化快）

### Q3: 如何止盈止损？

**A:** 组合策略一般不止损：
- 个股自动剔除（不符合选股条件）
- 整体仓位控制（熊市降低仓位）
- 动态再平衡（高估减持）

### Q4: 组合策略能跑赢指数吗？

**A:** 历史回测显示：
- 低 PE 轮动：年化 25%+，跑赢沪深 300
- 小市值：年化 30%+，但波动大
- 动量因子：年化 20%+，牛市表现好

---

## 九、进阶阅读

- 《主动投资组合管理》- Grinold & Kahn
- 《量化交易：如何建立自己的算法交易业务》- Ernest Chan
- 《Expected Returns》- Antti Ilmanen

---

**最后更新：** 2026-03-26
**维护者：** Quantitative Trading Team
