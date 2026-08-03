# 📊 策略管理系统 — 使用指南

## 快速开始

```bash
# 启动策略看板
cd /opt/quantitative-trading
source .venv/bin/activate
python server.py

# 浏览器打开
open http://localhost:8001
```

## 页面功能

### 📦 策略库
展示所有已注册的策略卡片。系统自动扫描 `strategy/` 目录下的所有 `BaseStrategy` 子类。
- 鼠标悬浮在 Tab 上可看到对应的 CLI 命令

### 🎯 策略评测
选择一个策略 + 输入股票代码 → 运行回测 → 显示六维雷达图和评分卡。

### 🏆 排行榜
输入股票代码 → 所有策略同时回测 → 按总分排序展示。

### 🤖 AI 策略生成
1. 填写 DashScope API Key
2. 选择来源类型（文本描述 / URL）
3. 输入交易思路或文章链接
4. AI 生成策略代码 → 预览 → 确认注册

### 📖 使用指南
内嵌了评分体系说明、分级标准和操作流程。

---

## 评分体系（100 分制）

| 指标 | 权重 | 满分条件 | 通俗解释 |
| :--- | :--- | :--- | :--- |
| SQN | 25% | ≥ 3.0 | 策略整体质量，越高越稳 |
| 夏普比率 | 20% | ≥ 1.5 | 每冒一分风险赚多少钱 |
| 最大回撤 | 20% | ≤ 10% | 历史最大亏损幅度 |
| 盈利因子 | 15% | ≥ 2.0 | 总赚 ÷ 总亏 |
| 胜率 | 10% | ≥ 50% | 赢的次数占比 |
| 交易次数 | 10% | ≥ 20 | 样本太少不可信 |

## 策略分级

- ★★★ **精选**（≥75 分）→ 可进入模拟盘验证
- ★★ **合格**（55-74 分）→ 有价值，继续优化
- ★ **观察**（35-54 分）→ 需改进或换股测试
- **淘汰**（< 35 分）→ 建议放弃

## 添加新策略

在 `strategy/` 目录下创建 `.py` 文件：

```python
from strategy.base import BaseStrategy, Signal, SignalType

class MyStrategy(BaseStrategy):
    @property
    def name(self): return "我的策略"

    @property
    def category(self): return "择时"  # 择时/选股/综合

    @property
    def description(self): return "策略描述"

    @property
    def author(self): return "作者名"

    def generate_signals(self, data):
        df = data['daily']
        signals = []
        # 你的逻辑...
        return signals
```

重启 `python server.py` 即可自动注册。
