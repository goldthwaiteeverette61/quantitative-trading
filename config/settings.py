"""
全局配置 — Pydantic 模型
"""
from pydantic import BaseModel
from pathlib import Path


# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent


class DataConfig(BaseModel):
    """数据源配置"""
    cache_dir: str = str(PROJECT_ROOT / "data" / "cache")
    cache_max_age_hours: int = 4
    default_days: int = 120
    # 数据库后端: 仅支持 "mysql"
    db_type: str = "mysql"
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = "root123456"
    mysql_database: str = "quant_trading"


class TripleScreenConfig(BaseModel):
    """Elder 三重滤网参数"""
    # 第一重：周线趋势
    weekly_ema_period: int = 13
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    # 第二重：日线震荡
    force_index_period: int = 2
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    # 止损
    atr_period: int = 14
    atr_stop_multiplier: float = 2.0


class TurtleConfig(BaseModel):
    """海龟交易法参数"""
    entry_period: int = 20       # 入场突破周期
    exit_period: int = 10        # 出场突破周期
    atr_period: int = 20         # ATR 周期
    unit_risk_pct: float = 0.01  # 每单位风险占比（1%）
    max_units: int = 4           # 最大加仓单位
    add_unit_atr: float = 0.5    # 每上涨 N 倍 ATR 加仓
    stop_atr: float = 2.0        # 止损 ATR 倍数


class RiskConfig(BaseModel):
    """风控配置"""
    max_single_risk_pct: float = 0.02   # 单笔最大风险 2%（Elder 2% 规则）
    max_total_risk_pct: float = 0.06    # 总风险上限 6%（Elder 6% 规则）
    max_positions: int = 10             # 最大持仓数
    max_single_position_pct: float = 0.20  # 单只最大仓位 20%


class BacktestConfig(BaseModel):
    """回测配置"""
    initial_capital: float = 100000.0
    commission_rate: float = 0.0003     # 手续费 0.03%
    slippage_rate: float = 0.001        # 滑点 0.1%
    stamp_tax_rate: float = 0.001       # 印花税 0.1%（卖出时收取）


class AIConfig(BaseModel):
    """AI 策略生成配置"""
    endpoint: str = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    model: str = "qwen-plus"


class PortfolioConfig(BaseModel):
    """组合回测配置"""
    initial_capital: float = 500000.0       # 初始资金 50 万
    rebalance_frequency: str = "monthly"    # 调仓频率
    max_holdings: int = 20                  # 最大持仓数
    commission_rate: float = 0.0003         # 手续费 万三
    slippage_rate: float = 0.002            # 滑点 0.2%（中小盘更高）
    stamp_tax_rate: float = 0.001           # 印花税 千一（卖出）


class Config(BaseModel):
    """总配置"""
    data: DataConfig = DataConfig()
    triple_screen: TripleScreenConfig = TripleScreenConfig()
    turtle: TurtleConfig = TurtleConfig()
    risk: RiskConfig = RiskConfig()
    backtest: BacktestConfig = BacktestConfig()
    ai: AIConfig = AIConfig()
    portfolio: PortfolioConfig = PortfolioConfig()


config = Config()
