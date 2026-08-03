#!/usr/bin/env python3
"""
策略管理系统 — FastAPI 服务端

启动: python server.py
访问: http://localhost:8001
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import os
os.environ['HTTP_PROXY'] = ''
os.environ['HTTPS_PROXY'] = ''
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['NO_PROXY'] = '*'

import socket
import pandas as pd
# 防御性编程：全局接管所有 TCP/UDP 连接超时，防止 Python 挂死在无 timeout 的阻塞调用上
socket.setdefaulttimeout(30)

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel as PydanticBaseModel
from typing import Optional
from loguru import logger

app = FastAPI(title="策略管理看板", version="1.0")

# 静态文件
web_dir = Path(__file__).parent / "web"
web_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")


# ===== 请求模型 =====

class EvaluateRequest(PydanticBaseModel):
    symbol: str = "000001"
    strategy_key: str = "elder_三重滤网"
    days: int = 365

class LeaderboardRequest(PydanticBaseModel):
    symbol: str = "000001"
    days: int = 365

class ModelsRequest(PydanticBaseModel):
    ai_api_key: str
    ai_endpoint: Optional[str] = None

class GenerateRequest(PydanticBaseModel):
    source_type: str = "text"      # 'url' / 'text'
    content: str = ""
    ai_api_key: str = ""
    ai_endpoint: Optional[str] = None
    model: Optional[str] = None

class ConfirmRequest(PydanticBaseModel):
    filename: str


# ===== 路由 =====

@app.get("/")
async def index():
    """主页"""
    return FileResponse(web_dir / "index.html")

@app.get("/app.js")
async def serve_app_js():
    return FileResponse(web_dir / "app.js")

@app.get("/style.css")
async def serve_style_css():
    return FileResponse(web_dir / "style.css")


@app.get("/api/strategies")
async def list_strategies():
    """获取所有已注册策略"""
    st_dict = _get_portfolio_strategies()
    # 兼容原有的前端格式
    strategies = [
        {
            "key": k,
            "name": v.name,
            "description": v.description,
            "author": getattr(v, "author", "未知"),
            "category": v.category
        }
        for k, v in st_dict.items()
    ]
    return {"strategies": strategies}


@app.post("/api/evaluate")
async def evaluate_strategy(req: EvaluateRequest):
    """单策略评测"""
    from data.fetcher import DataFetcher
    from backtest.engine import BacktestEngine
    from analytics.evaluator import evaluate

    st_dict = _get_portfolio_strategies()
    strategy = st_dict.get(req.strategy_key)
    if not strategy:
        raise HTTPException(404, f"策略未找到: {req.strategy_key}")

    try:
        fetcher = DataFetcher()
        data = fetcher.get_multi_period_data(
            req.symbol,
            daily_days=req.days,
            weekly_days=req.days // 5,
        )

        if data['daily'].empty:
            raise HTTPException(400, f"无法获取 {req.symbol} 的行情数据")

        signals = strategy.generate_signals(data)
        engine = BacktestEngine()
        result = engine.run(data['daily'], signals, strategy.name, req.symbol)

        score = evaluate(result)

        # 保存评测记录
        registry.add_evaluation(req.strategy_key, score.to_dict())

        return {"score": score.to_dict()}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"评测失败: {e}")
        raise HTTPException(500, str(e))


@app.post("/api/leaderboard")
async def leaderboard(req: LeaderboardRequest):
    """所有策略排行榜"""
    from strategy.registry import registry
    from data.fetcher import DataFetcher
    from backtest.engine import BacktestEngine
    from analytics.evaluator import evaluate

    keys = registry.get_keys()
    if not keys:
        return {"rankings": []}

    fetcher = DataFetcher()
    data = fetcher.get_multi_period_data(
        req.symbol,
        daily_days=req.days,
        weekly_days=req.days // 5,
    )

    if data['daily'].empty:
        raise HTTPException(400, f"无法获取 {req.symbol} 的行情数据")

    rankings = []
    for key in keys:
        strategy = registry.get(key)
        if not strategy:
            continue
        try:
            signals = strategy.generate_signals(data)
            engine = BacktestEngine()
            result = engine.run(data['daily'], signals, strategy.name, req.symbol)
            score = evaluate(result)
            rankings.append(score.to_dict())
        except Exception as e:
            logger.warning(f"排行榜跳过 {key}: {e}")

    # 按总分降序
    rankings.sort(key=lambda x: x["total_score"], reverse=True)
    return {"rankings": rankings, "symbol": req.symbol}


@app.post("/api/models")
async def list_models(req: ModelsRequest):
    """获取 AI 模型列表"""
    from strategy.strategy_generator import list_ai_models
    from config.settings import config

    endpoint = req.ai_endpoint or config.ai.endpoint
    models = await list_ai_models(req.ai_api_key, endpoint)
    return {"models": models}


@app.post("/api/generate")
async def generate_strategy(req: GenerateRequest):
    """AI 生成策略"""
    from strategy.strategy_generator import generate_strategy as gen
    from config.settings import config

    if not req.ai_api_key:
        raise HTTPException(400, "请提供 AI API Key")
    if not req.content:
        raise HTTPException(400, "请提供决策来源内容")

    endpoint = req.ai_endpoint or config.ai.endpoint
    model = req.model or config.ai.model

    result = await gen(
        source_type=req.source_type,
        content=req.content,
        ai_api_key=req.ai_api_key,
        ai_endpoint=endpoint,
        model=model,
    )

    if not result["success"]:
        raise HTTPException(500, result.get("error", "生成失败"))

    return result


@app.post("/api/confirm")
async def confirm_strategy(req: ConfirmRequest):
    """确认并注册 AI 生成的策略"""
    from strategy.strategy_generator import confirm_strategy as confirm

    result = confirm(req.filename)
    if not result["success"]:
        raise HTTPException(400, result["error"])

    return {"message": "策略注册成功"}


# ===== V2 组合回测 API =====

class PortfolioBacktestRequest(PydanticBaseModel):
    index_code: str = "csi500"          # csi300 / csi500 / csi1000
    strategy_key: str = "small_cap"     # small_cap / value_momentum
    start_date: str = "2020-01-01"      # 回测起始日期 YYYY-MM-DD
    end_date: str = "2026-01-01"        # 回测结束日期 YYYY-MM-DD
    days: int = 0                       # 兼容旧版：如果 >0 则用 days 推算日期（向后兼容）
    top_n: int = 10                     # 默认保留属性，后端策略用自身设定
    initial_capital: float = 500000.0
    stamp_tax_rate: float = 0.001       # 对齐原版：印花税千1（我们的引擎只在卖出时算，与聚宽一致）
    commission_rate: float = 0.0003     # 对齐原版：佣金万3
    slippage_rate: float = 0.0005       # 对齐原版：极低固定滑点万5（真实环境为0.5%）

class PortfolioLeaderboardRequest(PydanticBaseModel):
    index_code: str = "csi500"
    days: int = 3650
    top_n: int = 10
    initial_capital: float = 500000.0


# ===== 实时市场广度 API =====

@app.get("/api/market/breadth/realtime")
async def get_realtime_breadth():
    """计算最新交易日的市场广度（站在 MA60/120/250 之上的股票占比）"""
    import pymysql
    import pandas as pd
    import numpy as np
    import time

    t0 = time.time()
    try:
        conn = pymysql.connect(host='localhost', user='root', password='root123456', database='quant_trading')
        # 仅加载最近 400 天数据（够算 MA250 + 余量）
        query = """
            SELECT symbol, date, close
            FROM daily_kline
            WHERE date >= DATE_SUB((SELECT MAX(date) FROM daily_kline), INTERVAL 400 DAY)
            ORDER BY symbol, date
        """
        df = pd.read_sql_query(query, conn)
        conn.close()

        if df.empty:
            raise HTTPException(500, "无K线数据")

        df['close'] = df['close'].astype(np.float32)
        latest_date = str(df['date'].max())[:10]
        total_stocks = df[df['date'] == df['date'].max()]['symbol'].nunique()

        results = {}
        for ma in [60, 120, 250]:
            df[f'ma{ma}'] = df.groupby('symbol')['close'].transform(
                lambda x: x.rolling(ma, min_periods=ma).mean()
            )
            df[f'above_{ma}'] = (df['close'] > df[f'ma{ma}']).astype(int)

            latest_df = df[df['date'] == df['date'].max()].dropna(subset=[f'ma{ma}'])
            total = len(latest_df)
            above = int(latest_df[f'above_{ma}'].sum())
            pct = round(above / total * 100, 2) if total > 0 else 0

            results[str(ma)] = {
                "above": above,
                "total": total,
                "pct": pct,
                "label": f"MA{ma}",
            }
            df.drop(columns=[f'ma{ma}', f'above_{ma}'], inplace=True)

        elapsed = round(time.time() - t0, 1)
        return {
            "date": latest_date,
            "total_stocks": total_stocks,
            "breadth": results,
            "elapsed_seconds": elapsed,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"市场广度计算失败: {e}")
        raise HTTPException(500, str(e))

# ===== K 线查看器 API =====

@app.get("/api/stock/kline/{symbol}")
async def get_stock_kline(symbol: str, days: int = 365):
    """获取单只股票的日K线数据（本地 MySQL）"""
    from data.fetcher import DataFetcher
    try:
        fetcher = DataFetcher()
        df = fetcher.get_stock_data(symbol.strip(), 'daily', days)
        if df.empty:
            raise HTTPException(404, f"未找到 {symbol} 的K线数据")

        # 转换为前端需要的格式
        records = []
        for _, row in df.iterrows():
            records.append({
                "date": str(row['日期'].date()) if hasattr(row['日期'], 'date') else str(row['日期'])[:10],
                "open": round(float(row['开盘']), 2),
                "close": round(float(row['收盘']), 2),
                "high": round(float(row['最高']), 2),
                "low": round(float(row['最低']), 2),
                "volume": float(row['成交量']),
                "change_pct": round(float(row.get('涨跌幅', 0)), 2),
                "turnover": round(float(row.get('换手率', 0)), 2),
            })

        # 尝试获取股票名称
        name = symbol
        try:
            from data.database import DatabaseManager
            db = DatabaseManager()
            conn = db._get_conn()
            try:
                cur = conn.cursor()
                cur.execute("SELECT code_name FROM stock_basic WHERE symbol = %s", (symbol,))
                row = cur.fetchone()
                if row and row[0]:
                    name = row[0]
            finally:
                conn.close()
        except:
            pass

        return {
            "symbol": symbol,
            "name": name,
            "count": len(records),
            "klines": records,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"K线查询失败: {symbol}, {e}")
        raise HTTPException(500, str(e))

def _get_portfolio_strategies() -> dict:
    """获取所有可用的组合策略"""
    from strategy.small_cap import SmallCapStrategy
    from strategy.value_momentum import ValueMomentumStrategy
    from strategy.reversal import ReversalStrategy
    from strategy.low_turnover import LowTurnoverStrategy
    from strategy.low_vol_momentum import LowVolMomentumStrategy
    from strategy.retail_harvest_q4 import RetailHarvestQ4Strategy
    from strategy.barbell_2026 import Barbell2026Strategy
    from strategy.jk_spoiler import JKSpoilerStrategy
    from strategy.jk_spoiler_v2 import JKSpoilerV2Strategy
    from strategy.jk_spoiler_v3 import JKSpoilerV3Strategy
    from strategy.jk_spoiler_v5 import JKSpoilerV5Strategy
    from strategy.beipiao_microcap import BeipiaoMicrocapV1, BeipiaoMicrocapV2, BeipiaoMicrocapV3, BeipiaoMicrocapV4
    from strategy.beipiao_retail_hunter import BeipiaoRetailHunter
    from strategy.technical_resonance import TechResonanceNormal, TechResonanceMicroCap
    from strategy.oversold_rebound import OversoldReboundStrategy
    from strategy.friction_risk_filter import FrictionRiskFilterStrategy
    from strategy.momentum_breakout import MomentumBreakoutStrategy
    from strategy.momentum_breakout_v1_1 import MomentumBreakoutV11Strategy
    from strategy.momentum_breakout_v2 import MomentumBreakoutV2Strategy
    from strategy.momentum_breakout_v2_1 import MomentumBreakoutV21Strategy
    from strategy.momentum_breakout_v3 import MomentumBreakoutV3Strategy
    from strategy.momentum_breakout_v3_1 import MomentumBreakoutV31Strategy
    from strategy.volume_shock_observer import VolumeShockObserverStrategy

    return {
        "small_cap": SmallCapStrategy(),
        "value_momentum": ValueMomentumStrategy(),
        "reversal": ReversalStrategy(),
        "low_turnover": LowTurnoverStrategy(),
        "low_vol_momentum": LowVolMomentumStrategy(),
        "retail_harvest_q4": RetailHarvestQ4Strategy(),
        "barbell_2026": Barbell2026Strategy(),
        "jk_spoiler_v1": JKSpoilerStrategy(),
        "jk_spoiler_v2": JKSpoilerV2Strategy(),
        "jk_spoiler_v3": JKSpoilerV3Strategy(),
        "jk_spoiler_v5": JKSpoilerV5Strategy(),
        # ===== 五维共振技术策略 =====
        "tech_resonance": TechResonanceNormal(top_n=20, rebalance_freq="weekly"),
        "tech_resonance_micro": TechResonanceMicroCap(top_n=20, rebalance_freq="weekly"),
        "oversold_rebound": OversoldReboundStrategy(top_n=10, rebalance_freq="weekly"),
        # ===== 北漂青年1500万微盘股策略系列 =====
        "beipiao_v1": BeipiaoMicrocapV1(top_n=20, rebalance_freq="weekly"),
        "beipiao_v2": BeipiaoMicrocapV2(top_n=20, min_roe=0.15, rebalance_freq="weekly"),
        "beipiao_v3": BeipiaoMicrocapV3(top_n=20, min_roe=0.15, max_bias_20=0.05, rebalance_freq="weekly"),
        "beipiao_v4": BeipiaoMicrocapV4(top_n=20, min_roe=0.15, max_bias_20=0.10, max_market_cap=30.0, rebalance_freq="weekly"),
        "beipiao_retail_hunter": BeipiaoRetailHunter(top_n=20, min_roe=0.08, max_market_cap=50.0, rebalance_freq="weekly"),
        "friction_risk_filter": FrictionRiskFilterStrategy(top_n=30, rebalance_freq="monthly"),
        # ===== 动能突破策略 =====
        "momentum_breakout": MomentumBreakoutStrategy(max_holdings=100, vol_threshold=3.0, min_pct_chg=0.03, max_hold_days=5),
        "momentum_breakout_v1_1": MomentumBreakoutV11Strategy(max_holdings=100, vol_threshold=3.0, max_vol_threshold=8.0, min_pct_chg=0.03, max_hold_days=5, max_vwap_dev=0.10, max_turnover_pct=15.0),
        "momentum_breakout_v2": MomentumBreakoutV2Strategy(max_holdings=100, vol_threshold=3.0, min_pct_chg=0.03, max_hold_days=5, min_vwap_dev=0.02, min_turnover_pct=5.0),
        "momentum_breakout_v2_1": MomentumBreakoutV21Strategy(max_holdings=100, vol_threshold=3.0, min_pct_chg=0.03, max_hold_days=5, min_vwap_dev=0.02, max_vwap_dev=0.10, min_turnover_pct=5.0, max_turnover_pct=15.0),
        "momentum_breakout_v3": MomentumBreakoutV3Strategy(max_holdings=100, vol_threshold=3.0, atr_multiplier=1.5, min_pct_floor=0.01, max_hold_days=5),
        "momentum_breakout_v3_1": MomentumBreakoutV31Strategy(max_holdings=100, vol_threshold=3.0, atr_multiplier=1.5, min_pct_floor=0.01, max_hold_days=5, max_vwap_dev=0.10, max_turnover_pct=15.0),
        "volume_shock_observer": VolumeShockObserverStrategy(max_holdings=5, vol_threshold=3.0, max_vol_threshold=8.0, min_pct_chg=0.01, max_pct_chg=0.06, min_amount=30_000_000.0, max_hold_days=3),
    }


@app.get("/api/portfolio/strategies")
async def list_portfolio_strategies():
    """列出所有组合策略"""
    strategies = _get_portfolio_strategies()
    return {
        "strategies": [
            {
                "key": key,
                "name": strategy.name,
                "category": strategy.category,
                "description": strategy.description,
            }
            for key, strategy in strategies.items()
        ]
    }


@app.post("/api/portfolio/simulate")
async def run_portfolio_simulation(req: dict):
    """运行组合策略模拟交易"""
    from paper_trading.simulator import PaperTradingSimulator

    try:
        strategy_key = req.get('strategy_key')
        index_code = req.get('index_code', 'csi500')
        days = req.get('days', 60)
        top_n = req.get('top_n', 30)
        capital = req.get('initial_capital', 100000)
        rebalance_freq = req.get('rebalance_freq', 'monthly')

        # 获取策略
        strategies = _get_portfolio_strategies()
        if strategy_key not in strategies:
            raise HTTPException(status_code=400, detail=f'未知策略：{strategy_key}')

        strategy = strategies[strategy_key]

        # 创建模拟器
        simulator = PaperTradingSimulator(
            strategy=strategy,
            index_code=index_code,
            initial_capital=capital,
            top_n=top_n,
        )

        # 运行模拟
        simulator.run_simulation(days=days, rebalance_freq=rebalance_freq)

        # 返回结果
        return {
            "success": True,
            "result": {
                "initial_capital": simulator.initial_capital,
                "final_value": simulator.total_value,
                "total_profit": simulator.total_profit,
                "total_profit_pct": simulator.total_profit_pct,
                "trade_count": len(simulator.trade_log),
                "positions": [p.to_dict() for p in simulator.positions.values()],
                "trades": [t.to_dict() for t in simulator.trade_log[-20:]],
                "value_log": simulator.value_log[-30:],  # 最近 30 天净值
            }
        }

    except Exception as e:
        logger.error(f"模拟交易失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/portfolio/backtest")
async def portfolio_backtest(req: PortfolioBacktestRequest):
    """运行组合回测"""
    import asyncio
    from data.universe import UniverseManager
    from data.fetcher import DataFetcher
    from backtest.portfolio_engine import PortfolioBacktestEngine
    from analytics.portfolio_evaluator import evaluate_portfolio

    strategies = _get_portfolio_strategies()
    strategy = strategies.get(req.strategy_key)
    if not strategy:
        raise HTTPException(404, f"组合策略未找到: {req.strategy_key}")

    # 将前端传入的 top_n 注入策略实例
    if hasattr(strategy, 'top_n') and req.top_n:
        strategy.top_n = req.top_n
        strategy.rebalance_config.max_holdings = req.top_n

    try:
        # 1. 获取股票池
        um = UniverseManager()
        # 注意: 此处不应用 req.top_n 限制宇宙大小，否则策略获取不到足够标的
        symbols = um.get_universe(req.index_code)
        if not symbols:
            raise HTTPException(400, f"无法获取 {req.index_code} 的成分股")

        # 2. 批量获取历史价格（本地 MySQL 已含每日动态 market_cap/pe/pb，不再调用东财 API）
        fetcher = DataFetcher()
        price_data = {}
        for sym in symbols:
            df = fetcher.get_stock_data(sym, 'daily', req.days)
            if not df.empty and len(df) > 20:
                if 'industry' not in df.columns:
                    df['industry'] = '未知'
                price_data[sym] = df

        if len(price_data) < 5:
            raise HTTPException(400, f"有效股票数据不足: {len(price_data)} 只")

        logger.info(f"组合回测: {strategy.name}, {len(price_data)} 只, {req.days} 天")

        # 4. 运行组合回测
        index_names = {
            'csi300': '沪深300', 'csi500': '中证500',
            'csi1000': '中证1000', 'csi2000': '中证2000',
        }
        engine = PortfolioBacktestEngine(
            initial_capital=req.initial_capital,
            commission_rate=req.commission_rate,
            slippage_rate=req.slippage_rate,
            stamp_tax_rate=req.stamp_tax_rate,
        )
        # 将重度 CPU 密集型计算推入线程池以避免阻塞事件循环
        result = await asyncio.to_thread(engine.run, strategy, price_data, index_names.get(req.index_code, req.index_code))

        # 5. 评分
        score = evaluate_portfolio(result)

        return {
            "result": result.to_dict(),
            "score": score.to_dict(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"组合回测失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(500, str(e))


# ===== 异步回测队列字典 =====
import threading as _bt_threading
BACKTEST_TASKS = {} # task_id -> {"status": "pending|running|completed|failed", "result": dict, "score": dict, "error": str, "progress": str}
BACKTEST_STOP_EVENTS = {}  # task_id -> threading.Event

class BacktestCancelled(Exception):
    """回测被用户手动取消"""
    pass

def run_backtest_backend(task_id: str, req: PortfolioBacktestRequest):
    from data.universe import UniverseManager
    from data.fetcher import DataFetcher
    from backtest.portfolio_engine import PortfolioBacktestEngine
    from analytics.portfolio_evaluator import evaluate_portfolio
    import time as _bt_time

    stop_event = BACKTEST_STOP_EVENTS.get(task_id)
    t_total = _bt_time.time()

    def _check_stop(msg=''):
        if stop_event and stop_event.is_set():
            raise BacktestCancelled(f'用户手动停止{" — " + msg if msg else ""}')

    # ===== 日期参数标准化 =====
    from datetime import datetime as _dt, timedelta as _td
    if req.days > 0 and (not req.start_date or req.start_date == '2020-01-01'):
        # 兼容旧版 days 参数：从今天往前推 days 天
        _end = _dt.now()
        _start = _end - _td(days=req.days)
        bt_start_date = _start.strftime('%Y-%m-%d')
        bt_end_date = _end.strftime('%Y-%m-%d')
    else:
        bt_start_date = req.start_date
        bt_end_date = req.end_date

    # 数据加载天数 = 回测区间的自然天数 + 预热期（MA250 等需要前置数据）
    _date_diff = (_dt.strptime(bt_end_date, '%Y-%m-%d') - _dt.strptime(bt_start_date, '%Y-%m-%d')).days
    data_load_days = _date_diff + 400  # 额外 400 天用于指标预热
    logger.info(f"[回测 {task_id[:8]}] 日期参数: {bt_start_date} ~ {bt_end_date} (加载 {data_load_days} 天数据含预热)")

    try:
        _check_stop()
        strategies = _get_portfolio_strategies()
        strategy = strategies.get(req.strategy_key)
        if not strategy:
            raise Exception(f"组合策略未找到: {req.strategy_key}")

        if req.top_n:
            if hasattr(strategy, 'top_n'):
                strategy.top_n = req.top_n

            # 部分策略（如 JK_搅屎棍）使用 stock_num 而非 top_n 做实际选股
            if hasattr(strategy, 'stock_num'):
                strategy.stock_num = req.top_n

            strategy.rebalance_config.max_holdings = req.top_n

        # === 阶段1: 加载股票池 ===
        t0 = _bt_time.time()
        um = UniverseManager()
        BACKTEST_TASKS[task_id]['progress'] = '加载股票池...'
        _check_stop()
        symbols = um.get_universe(req.index_code)
        if not symbols:
            raise Exception(f"无法获取 {req.index_code} 的成分股")
        logger.info(f"[回测 {task_id[:8]}] 阶段1-股票池: {len(symbols)} 只, 耗时 {_bt_time.time()-t0:.1f}s")

        # === 阶段2: 加载行情数据 ===
        t0 = _bt_time.time()
        BACKTEST_TASKS[task_id]['progress'] = f'加载行情数据(共{len(symbols)}只)...'
        fetcher = DataFetcher()
        price_data = {}

        import concurrent.futures

        def _fetch_one(sym):
            try:
                df = fetcher.get_stock_data(sym, 'daily', data_load_days)
                if not df.empty and len(df) > 20:
                    if 'industry' not in df.columns:
                        df['industry'] = '未知'
                    return sym, df
            except Exception as e:
                logger.warning(f"获取 {sym} 行情异常: {e}")
            return sym, None

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            future_to_sym = {executor.submit(_fetch_one, sym): sym for sym in symbols}
            for i, future in enumerate(concurrent.futures.as_completed(future_to_sym)):
                _check_stop()
                if i % 100 == 0:
                    BACKTEST_TASKS[task_id]['progress'] = f'加载行情数据({i}/{len(symbols)})...'
                sym, df = future.result()
                if df is not None:
                    price_data[sym] = df

        if len(price_data) < 5:
            raise Exception(f"有效股票数据不足: {len(price_data)} 只")
        logger.info(f"[回测 {task_id[:8]}] 阶段2-行情加载: {len(price_data)} 只有效, 耗时 {_bt_time.time()-t0:.1f}s")

        # === 阶段3: 引擎核心演算 ===
        t0 = _bt_time.time()
        logger.info(f"[回测 {task_id[:8]}] 阶段3-引擎启动: {strategy.name}, {len(price_data)} 只, {bt_start_date} ~ {bt_end_date}")
        BACKTEST_TASKS[task_id]['progress'] = '引擎核心演算中(这可能需要几十分钟)...'

        index_names = {'csi300': '沪深300', 'csi500': '中证500', 'csi1000': '中证1000', 'csi2000': '中证2000', 'all_a': '全A股'}
        engine = PortfolioBacktestEngine(
            initial_capital=req.initial_capital, commission_rate=req.commission_rate,
            slippage_rate=req.slippage_rate, stamp_tax_rate=req.stamp_tax_rate
        )

        result = engine.run(strategy, price_data, index_names.get(req.index_code, req.index_code), stop_event=stop_event, start_date=bt_start_date)
        logger.info(f"[回测 {task_id[:8]}] 阶段3-引擎完成: 耗时 {_bt_time.time()-t0:.1f}s, 收益 {result.total_return_pct:+.2f}%")

        _check_stop()
        score = evaluate_portfolio(result)

        BACKTEST_TASKS[task_id]['result'] = result_dict = result.to_dict()
        BACKTEST_TASKS[task_id]['score'] = score.to_dict()
        BACKTEST_TASKS[task_id]['status'] = 'completed'

        total_elapsed = _bt_time.time() - t_total
        logger.info(f"[回测 {task_id[:8]}] ✅ 全部完成! 总耗时 {total_elapsed:.1f}s ({total_elapsed/60:.1f}min)")

        from data.database import DatabaseManager
        db = DatabaseManager()
        db.update_backtest_record(task_id, {
            'status': 'completed',
            'final_value': result_dict['initial_capital'] * result_dict['final_nav'],
            'return_pct': result_dict['total_return_pct'],
            'annual_return_pct': result_dict.get('annual_return_pct', 0),
            'max_drawdown_pct': result_dict.get('max_drawdown_pct', 0),
            'sharpe_ratio': result_dict.get('sharpe_ratio', 0),
            'volatility_pct': result_dict.get('volatility_pct', 0),
            'total_rebalances': result_dict.get('total_rebalances', 0)
        })

    except BacktestCancelled as e:
        logger.info(f"[回测 {task_id[:8]}] ⏹ 用户取消: {e}")
        BACKTEST_TASKS[task_id]['error'] = str(e)
        BACKTEST_TASKS[task_id]['status'] = 'cancelled'
        from data.database import DatabaseManager
        db = DatabaseManager()
        db.update_backtest_record(task_id, {'status': 'failed', 'error_msg': str(e)})
    except Exception as e:
        import traceback
        tb_str = traceback.format_exc()
        logger.error(f"[回测 {task_id[:8]}] ❌ 失败: {e}\n{tb_str}")
        BACKTEST_TASKS[task_id]['error'] = str(e)
        BACKTEST_TASKS[task_id]['status'] = 'failed'
        from data.database import DatabaseManager
        db = DatabaseManager()
        db.update_backtest_record(task_id, {'status': 'failed', 'error_msg': tb_str[:2000]})
    finally:
        BACKTEST_STOP_EVENTS.pop(task_id, None)


@app.post("/api/portfolio/backtest/async")
async def portfolio_backtest_async(req: PortfolioBacktestRequest, background_tasks: BackgroundTasks):
    """发起异步回测任务并立刻返回任务ID"""
    import uuid
    from data.database import DatabaseManager
    task_id = str(uuid.uuid4())
    BACKTEST_TASKS[task_id] = {"status": "pending", "progress": "初始化排队中..."}
    BACKTEST_STOP_EVENTS[task_id] = _bt_threading.Event()  # 创建停止信号

    db = DatabaseManager()

    # 偷偷在这里解开strategy名称用来存档
    strategies = _get_portfolio_strategies()
    st = strategies.get(req.strategy_key)
    s_name = st.name if st else req.strategy_key
    index_names = {'csi300': '沪深300', 'csi500': '中证500', 'csi1000': '中证1000', 'csi2000': '中证2000', 'all_a': '全A股'}

    req_dict = req.dict()
    req_dict['strategy_name'] = s_name
    req_dict['index_code'] = index_names.get(req.index_code, req.index_code)
    # 确保 start_date/end_date 正确存入
    if req.days > 0 and (not req.start_date or req.start_date == '2020-01-01'):
        from datetime import datetime as _dt2, timedelta as _td2
        _end2 = _dt2.now()
        _start2 = _end2 - _td2(days=req.days)
        req_dict['start_date'] = _start2.strftime('%Y-%m-%d')
        req_dict['end_date'] = _end2.strftime('%Y-%m-%d')
    db.create_pending_backtest(task_id, req_dict)

    background_tasks.add_task(run_backtest_backend, task_id, req)
    return {"task_id": task_id}

@app.get("/api/portfolio/backtest/task/{task_id}")
async def get_backtest_task(task_id: str):
    """轮询异步回测状态 — 只返回轻量摘要，避免每2秒传输MB级JSON"""
    task = BACKTEST_TASKS.get(task_id)
    if not task:
        return {"status": "not_found"}

    status = task.get("status", "pending")
    resp = {"status": status, "progress": task.get("progress", "")}

    if status == "completed":
        # 只返回核心指标摘要，不含巨大的 nav_series / final_holdings
        result = task.get("result", {})
        resp["result"] = {
            "strategy_name": result.get("strategy_name", ""),
            "index_name": result.get("index_name", ""),
            "initial_capital": result.get("initial_capital", 0),
            "final_nav": result.get("final_nav", 0),
            "total_return_pct": result.get("total_return_pct", 0),
            "annual_return_pct": result.get("annual_return_pct", 0),
            "max_drawdown_pct": result.get("max_drawdown_pct", 0),
            "sharpe_ratio": result.get("sharpe_ratio", 0),
            "volatility_pct": result.get("volatility_pct", 0),
            "total_rebalances": result.get("total_rebalances", 0),
            "total_trade_cost": result.get("total_trade_cost", 0),
        }
        resp["score"] = task.get("score", {})
    elif status == "failed":
        resp["error"] = task.get("error", "未知错误")

    return resp


@app.get("/api/portfolio/backtest/task/{task_id}/full")
async def get_backtest_task_full(task_id: str):
    """获取完整回测结果（含 nav_series 等大体量数据），仅在前端确认完成后调用一次"""
    task = BACKTEST_TASKS.get(task_id)
    if not task:
        return {"status": "not_found"}
    return task


@app.post("/api/portfolio/backtest/stop/{task_id}")
async def stop_backtest_task(task_id: str):
    """停止正在运行的回测任务"""
    stop_event = BACKTEST_STOP_EVENTS.get(task_id)
    task = BACKTEST_TASKS.get(task_id)

    if not task:
        raise HTTPException(404, "任务不存在")

    if task.get('status') in ('completed', 'failed', 'cancelled'):
        return {"status": "already_done", "message": "任务已结束"}

    if stop_event:
        stop_event.set()
        task['progress'] = '正在停止...'
        logger.info(f"用户请求停止回测任务: {task_id}")
        return {"status": "stopping", "message": "已发送停止信号"}
    else:
        # 没有 stop_event 但任务存在（可能是老任务）— 直接标记为失败
        task['status'] = 'cancelled'
        task['error'] = '用户手动停止'
        from data.database import DatabaseManager
        db = DatabaseManager()
        db.update_backtest_record(task_id, {'status': 'failed'})
        return {"status": "force_stopped", "message": "已强制标记为停止"}


# ===== 策略采用状态 =====

@app.get("/api/strategy/adoptions")
async def get_strategy_adoptions():
    """获取所有策略的采用状态和信号状态"""
    from data.database import DatabaseManager
    db = DatabaseManager()
    return {
        "adoptions": db.get_strategy_adoptions(),
        "statuses": db.get_strategy_statuses(),
    }


@app.post("/api/strategy/adopt")
async def set_strategy_adoption(req: dict):
    """设置策略采用/取消采用"""
    from data.database import DatabaseManager
    db = DatabaseManager()
    key = req.get("strategy_key", "")
    adopted = req.get("adopted", False)
    if not key:
        raise HTTPException(400, "缺少 strategy_key")
    db.set_strategy_adoption(key, adopted)
    return {"success": True, "strategy_key": key, "adopted": adopted}


@app.post("/api/strategy/status")
async def set_strategy_status(req: dict):
    """设置策略状态 (normal / no_signal / heavy_loss)"""
    from data.database import DatabaseManager
    db = DatabaseManager()
    key = req.get("strategy_key", "")
    status = req.get("status", "normal")
    if not key:
        raise HTTPException(400, "缺少 strategy_key")
    if status not in ("normal", "no_signal", "heavy_loss"):
        raise HTTPException(400, f"无效状态: {status}")
    db.set_strategy_status(key, status)
    return {"success": True, "strategy_key": key, "status": status}


# ===== 回测历史 =====

@app.get("/api/backtest/history")
async def get_backtest_history():
    """获取回测历史记录"""
    from data.database import DatabaseManager
    db = DatabaseManager()
    return {"history": db.get_backtest_history()}


@app.post("/api/backtest/save")
async def save_backtest_record(req: dict):
    """保存回测记录"""
    from data.database import DatabaseManager
    db = DatabaseManager()
    db.save_backtest_record(req)
    return {"success": True}


@app.delete("/api/backtest/history/{record_id}")
async def delete_backtest_record(record_id: int):
    """删除回测历史记录"""
    from data.database import DatabaseManager
    db = DatabaseManager()
    try:
        conn = db._get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM backtest_history WHERE id = %s", (record_id,))
        conn.commit()
        deleted = cursor.rowcount
        conn.close()
        if deleted == 0:
            raise HTTPException(404, f"记录 {record_id} 不存在")
        return {"success": True, "deleted_id": record_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除回测记录失败: {e}")
        raise HTTPException(500, str(e))


@app.post("/api/portfolio/leaderboard")
async def portfolio_leaderboard(req: PortfolioLeaderboardRequest):
    """组合策略排行榜"""
    from data.universe import UniverseManager
    from data.fetcher import DataFetcher
    from backtest.portfolio_engine import PortfolioBacktestEngine
    from analytics.portfolio_evaluator import evaluate_portfolio

    strategies = _get_portfolio_strategies()
    if not strategies:
        return {"rankings": []}

    try:
        # 获取股票池和数据（所有策略共用）
        um = UniverseManager()
        # 释放限制
        symbols = um.get_universe(req.index_code)
        if not symbols:
            raise HTTPException(400, f"无法获取 {req.index_code} 的成分股")

        # 本地 MySQL 已含每日动态 market_cap/pe/pb，不再调用东财 API
        fetcher = DataFetcher()
        price_data = {}
        for sym in symbols:
            df = fetcher.get_stock_data(sym, 'daily', req.days)
            if not df.empty and len(df) > 20:
                if 'industry' not in df.columns:
                    df['industry'] = '未知'
                price_data[sym] = df

        if len(price_data) < 5:
            raise HTTPException(400, f"有效股票数据不足: {len(price_data)} 只")

        index_names = {
            'csi300': '沪深300', 'csi500': '中证500',
            'csi1000': '中证1000', 'csi2000': '中证2000',
        }
        index_name = index_names.get(req.index_code, req.index_code)

        rankings = []
        for key, strategy in strategies.items():
            try:
                engine = PortfolioBacktestEngine(initial_capital=req.initial_capital)
                result = engine.run(strategy, price_data, index_name)
                score = evaluate_portfolio(result)
                entry = score.to_dict()
                entry['nav_series'] = result.nav_series
                rankings.append(entry)
            except Exception as e:
                logger.warning(f"排行榜跳过 {key}: {e}")

        rankings.sort(key=lambda x: x["total_score"], reverse=True)
        return {"rankings": rankings, "index": req.index_code}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"组合排行榜失败: {e}")
        raise HTTPException(500, str(e))

# ===== 数据管理与离线采集 =====
import threading
import time
import random

CRAWLER_STATE = {
    "is_running": False,
    "current_symbol": "",
    "progress": 0,
    "total": 0,
    "success": 0,
    "failed": 0,
    "skipped": 0,
    "message": "空闲"
}
crawler_thread = None
crawler_stop_event = threading.Event()

def crawler_worker(pool_name: str, days: int):
    global CRAWLER_STATE, crawler_stop_event
    from data.universe import UniverseManager
    from data.fetcher import DataFetcher
    from data.database import DatabaseManager

    CRAWLER_STATE.update({
        "is_running": True, "progress": 0, "total": 0, "success": 0,
        "failed": 0, "skipped": 0, "message": f"正在初始化股票池 {pool_name}..."
    })

    try:
        um = UniverseManager()
        fetcher = DataFetcher(use_cache=True)
        db = DatabaseManager()

        # 根据池子预估上限：沪深300(300),中证500(500),中证1000(1000)
        top_n = 1000
        symbols = um.get_universe(pool_name, top_n=top_n)
        CRAWLER_STATE["total"] = len(symbols)

        for i, symbol in enumerate(symbols):
            if crawler_stop_event.is_set():
                break

            code = symbol.split('.')[0]
            CRAWLER_STATE["current_symbol"] = code
            CRAWLER_STATE["progress"] = i + 1
            CRAWLER_STATE["message"] = f"采集中 [{code}] ({i+1}/{len(symbols)})"

            # 如果是请求全量长周期数据 (>100天) 且数据库已有大部分数据，则跳过
            # 如果是请求极少天数（如增量更新 5~30 天），则强制不跳过（覆盖写入）
            if days > 100:
                db_df = db.load_klines(code, days)
                if db_df is not None and len(db_df) >= int(days * 0.8):
                    CRAWLER_STATE["skipped"] += 1
                    continue

            for _ in range(3):
                if crawler_stop_event.is_set(): break
                time.sleep(0.01)

            if crawler_stop_event.is_set(): break

            market = 'sh' if code.startswith('6') else 'sz'
            ak_symbol = f"{market}{code}"

            try:
                import akshare as ak
                from datetime import datetime, timedelta
                end_date = datetime.now()
                start_date = end_date - timedelta(days=days)
                df = ak.stock_zh_a_daily(symbol=ak_symbol,
                                         start_date=start_date.strftime('%Y%m%d'),
                                         end_date=end_date.strftime('%Y%m%d'),
                                         adjust='qfq')
                if not df.empty:
                    if 'date' in df.columns: df.rename(columns={'date':'日期'}, inplace=True)
                    if 'volume' in df.columns: df.rename(columns={'volume':'成交量'}, inplace=True)
                    db.save_klines(code, df)
                    CRAWLER_STATE["success"] += 1
                else:
                    CRAWLER_STATE["failed"] += 1
            except Exception as e:
                CRAWLER_STATE["failed"] += 1

    except Exception as e:
        CRAWLER_STATE["message"] = f"采集异常中止: {str(e)}"
        logger.error(f"Crawler error: {e}")
    finally:
        was_stopped = crawler_stop_event.is_set()
        CRAWLER_STATE["is_running"] = False
        crawler_stop_event.clear()
        if "异常" not in CRAWLER_STATE["message"]:
            CRAWLER_STATE["message"] = "任务已手动终止" if was_stopped else "采集完毕！数据库已成功更新"

class CrawlerStartRequest(PydanticBaseModel):
    pool_name: str = "csi500"
    days: int = 3650

@app.get("/api/data/status")
async def get_data_status():
    import asyncio
    from data.database import DatabaseManager
    db = DatabaseManager()
    # 用 to_thread 包装，避免被后台线程 GIL 阻塞事件循环
    stats = await asyncio.to_thread(db.get_data_stats)

    crawler_state = dict(CRAWLER_STATE)

    # 将实盘任务引发的同步下载进度“劫持”合并到全局面板
    if not crawler_state["is_running"]:
        for tid, t in STRATEGY_TASKS.items():
            if t['status'] == 'starting' and 'progress' in t:
                crawler_state["is_running"] = True
                crawler_state["message"] = f"📡 {t['strategy_name']} 底仓缓存并发热身中..."
                try:
                    curr, tot = map(int, t['progress'].split('/'))
                    crawler_state["progress"] = curr
                    crawler_state["total"] = tot
                except:
                    pass
                break

    return {"stats": stats, "crawler": crawler_state}

@app.post("/api/data/start")
async def start_crawler(req: CrawlerStartRequest):
    global crawler_thread, crawler_stop_event, CRAWLER_STATE
    if CRAWLER_STATE["is_running"]:
        raise HTTPException(400, "采集正在进行中")
    crawler_stop_event.clear()
    crawler_thread = threading.Thread(target=crawler_worker, args=(req.pool_name, req.days), daemon=True)
    crawler_thread.start()
    return {"status": "ok"}

@app.post("/api/data/stop")
async def stop_crawler():
    global crawler_stop_event, CRAWLER_STATE
    if CRAWLER_STATE["is_running"]:
        crawler_stop_event.set()
        CRAWLER_STATE["message"] = "已发送停止指令，正等待当前网络请求放行结束..."
    return {"status": "stopping"}


# ==========================================
# 模拟实盘交易 (Paper Trading) API
# ==========================================
class PaperTradeRequest(PydanticBaseModel):
    account_id: str
    symbol: str
    action: str
    amount_or_shares: float

class PaperBindRequest(PydanticBaseModel):
    account_id: str
    cookie_str: str

@app.get("/api/paper/accounts")
async def get_paper_accounts():
    from paper_trading.manager import paper_manager
    try:
        data = paper_manager.get_account_list()
        return {"accounts": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/paper/account/{account_id}")
async def get_paper_account_detail(account_id: str):
    from paper_trading.manager import paper_manager
    try:
        data = paper_manager.get_account_detail(account_id)
        return {"status": "ok", "data": data}
    except Exception as e:
        logger.error(f"Paper details error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/paper/account/{account_id}/trades")
async def get_paper_account_trades(account_id: str):
    from data.database import DatabaseManager
    try:
        db = DatabaseManager()
        data = db.get_paper_trade_log(account_id)
        return {"status": "ok", "data": data}
    except Exception as e:
        logger.error(f"Paper trade logs error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/paper/trade")
async def paper_execute_trade(req: PaperTradeRequest):
    from paper_trading.manager import paper_manager
    try:
        if req.action.lower() == 'buy':
            success = paper_manager.execute_buy(req.account_id, req.symbol, req.amount_or_shares)
        elif req.action.lower() == 'sell':
            success = paper_manager.execute_sell(req.account_id, req.symbol, int(req.amount_or_shares))
        else:
            raise ValueError("Invalid action")

        return {"status": "success" if success else "failed"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/paper/bind")
async def paper_bind_account(req: PaperBindRequest):
    from paper_trading.manager import paper_manager
    try:
        res = paper_manager.bind_xueqiu_account(req.account_id, req.cookie_str)
        return res
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==========================================
# 策略守护任务系统 (Strategy Autopilot) - 持久化版
# ==========================================
import uuid
from datetime import datetime, timedelta

STRATEGY_TASKS = {}  # task_id -> task_state dict (内存运行态)

def _save_task_to_db(task_id: str, task: dict):
    """将任务状态持久化到数据库"""
    from data.database import DatabaseManager
    db = DatabaseManager()
    ph = db._placeholder()
    params = (
        task_id, task['account_id'], task['strategy_key'], task['strategy_name'],
        task['index_code'], task.get('top_n', 50), task.get('rebalance_interval_days', 30),
        task['status'], task['message'], task.get('rebalance_count', 0),
        task.get('last_rebalance_str'), task.get('next_rebalance_str'),
        task.get('created_at', datetime.now().strftime('%Y-%m-%d %H:%M')),
        task.get('allocation', 'even_amount'), task.get('fixed_lots', 10)
    )
    if db._db_type == 'mysql':
        sql = f'''INSERT INTO strategy_tasks
            (task_id, account_id, strategy_key, strategy_name, index_code,
             top_n, rebalance_days, status, message, rebalance_count,
             last_rebalance, next_rebalance, created_at, allocation, fixed_lots)
            VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})
            ON DUPLICATE KEY UPDATE status=VALUES(status), message=VALUES(message),
            rebalance_count=VALUES(rebalance_count), last_rebalance=VALUES(last_rebalance),
            next_rebalance=VALUES(next_rebalance)'''
    else:
        sql = f'''INSERT OR REPLACE INTO strategy_tasks
            (task_id, account_id, strategy_key, strategy_name, index_code,
             top_n, rebalance_days, status, message, rebalance_count,
             last_rebalance, next_rebalance, created_at, allocation, fixed_lots)
            VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})'''
    db._exec_write(sql, params)

def _load_tasks_from_db():
    """从数据库加载所有 running 状态的任务"""
    from data.database import DatabaseManager
    db = DatabaseManager()
    ph = db._placeholder()
    conn = db._get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(f'SELECT * FROM strategy_tasks WHERE status = {ph}', ('running',))
        cols = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
    finally:
        conn.close()
    return [dict(zip(cols, row)) for row in rows]

def _update_task_db(task_id: str, **kwargs):
    """增量更新任务某些字段"""
    from data.database import DatabaseManager
    db = DatabaseManager()
    ph = db._placeholder()
    sets = ', '.join(f'{k} = {ph}' for k in kwargs)
    vals = list(kwargs.values()) + [task_id]
    db._exec_write(f'UPDATE strategy_tasks SET {sets} WHERE task_id = {ph}', vals)

def strategy_autopilot_worker(task_id: str):
    """策略守护后台线程：定时检查调仓日，自动选股换仓。"""
    from paper_trading.manager import paper_manager
    from data.universe import UniverseManager
    from data.fetcher import DataFetcher
    from backtest.portfolio_engine import PortfolioBacktestEngine
    from data.trading_calendar import get_a_share_order_state, now_shanghai
    from datetime import time as datetime_time

    task = STRATEGY_TASKS[task_id]
    task['status'] = 'running'
    task['message'] = '策略守护已启动，等待调仓窗口...'
    _update_task_db(task_id, status='running', message=task['message'])

    strategies = _get_portfolio_strategies()
    if task['strategy_key'] not in strategies:
        task['status'] = 'stopped'
        task['message'] = f'策略 {task["strategy_key"]} 不存在，已停止'
        _update_task_db(task_id, status='stopped', message=task['message'])
        return
    strategy = strategies[task['strategy_key']]

    CHECK_INTERVAL = 60
    REBALANCE_START_CUTOFF = datetime_time(14, 0)

    while not task['_stop_event'].is_set():
        now = now_shanghai()

        last_rebalance = task.get('last_rebalance')
        rebalance_days = task.get('rebalance_interval_days', 30)

        should_rebalance = False
        if last_rebalance is None:
            should_rebalance = True
        elif (now - last_rebalance).days >= rebalance_days:
            should_rebalance = True

        if should_rebalance:
            order_state = get_a_share_order_state(now)
            if not order_state.is_trading_time:
                task['message'] = order_state.message
                _update_task_db(task_id, message=task['message'])
                for _ in range(CHECK_INTERVAL * 2):
                    if task['_stop_event'].is_set(): break
                    time.sleep(0.5)
                continue

            if now.time() >= REBALANCE_START_CUTOFF:
                task['message'] = f'调仓启动截止已过 {now.strftime("%H:%M")}，等待下一交易日'
                _update_task_db(task_id, message=task['message'])
                for _ in range(CHECK_INTERVAL * 2):
                    if task['_stop_event'].is_set(): break
                    time.sleep(0.5)
                continue

            task['message'] = f'🔄 正在执行调仓... ({now.strftime("%H:%M:%S")})'
            task['rebalance_count'] = task.get('rebalance_count', 0) + 1
            # ⭐ 关键修复：先持久化调仓标记，防止中断后重复调仓
            task['last_rebalance'] = now
            task['last_rebalance_str'] = now.strftime('%Y-%m-%d %H:%M')
            next_dt = now + timedelta(days=rebalance_days)
            task['next_rebalance_str'] = next_dt.strftime('%Y-%m-%d')
            _update_task_db(task_id,
                message=task['message'],
                rebalance_count=task['rebalance_count'],
                last_rebalance=task['last_rebalance_str'],
                next_rebalance=task['next_rebalance_str']
            )

            try:
                um = UniverseManager()
                symbols = um.get_universe(task['index_code'])

                if task['strategy_key'] == 'barbell_2026':
                    symbols = list(set(symbols + um.get_universe('csi300')))
                elif task['strategy_key'] == 'jk_spoiler':
                    # JK 搅屎棍策略的买入池纯血受限于中小板，大盘广度已移至内部 SQL 穿透直连。
                    # 实盘引擎无需为科创/创业板（301/688等）及主板大票加载百日价格行情与更新 Baostock。
                    # 直接在此处实施暴力裁剪，杜绝海量日志遍历，将 5300 只缩减至 1000 只左右。
                    symbols = [s for s in symbols if s.startswith('002')]

                cross_data = um.get_cross_section_data(symbols)
                fetcher = DataFetcher()

                # 预加载一次本地库里的最新行业映射补充实盘接口的反代拦截
                local_industry_dict = {}
                try:
                    from data.database import DatabaseManager
                    _db = DatabaseManager()
                    _conn = _db._get_conn()
                    try:
                        _cur = _conn.cursor()
                        _cur.execute("SELECT symbol, industry FROM stock_basic WHERE industry IS NOT NULL")
                        for _r in _cur.fetchall():
                            if _r[1] and _r[1] != '未知':
                                local_industry_dict[_r[0]] = _r[1]
                    finally:
                        _conn.close()
                except Exception:
                    pass

                price_data = {}
                total_symbols = len(symbols)
                task['message'] = f'📊 正在下载行情数据 0/{total_symbols}...'
                task['progress'] = f'0/{total_symbols}'
                for i, sym in enumerate(symbols):
                    if task['_stop_event'].is_set(): break
                    task['progress'] = f"{i+1}/{total_symbols}"
                    if i % 20 == 0:
                        pct = int((i + 1) / total_symbols * 100)
                        task['message'] = f'📊 正在下载行情数据 {i+1}/{total_symbols} ({pct}%)'
                    # 每只股票间 sleep 让出 GIL，避免堵死 FastAPI 事件循环
                    time.sleep(0.01)
                    df = fetcher.get_stock_data(sym, 'daily', task.get('days', 120))
                    if not df.empty and len(df) > 20:
                        if sym in cross_data.index:
                            row = cross_data.loc[sym]
                            # 防御：如果 cross_data 有重复 index，loc 返回 DataFrame 而非 Series
                            if isinstance(row, pd.DataFrame):
                                row = row.iloc[0]
                            df['market_cap'] = float(row.get('market_cap', 0))

                            ind = str(row.get('industry', '未知'))
                            if ind == '未知' or ind == '-':
                                ind = local_industry_dict.get(sym, '未知')
                            df['industry'] = ind
                        else:
                            df['industry'] = local_industry_dict.get(sym, '未知')
                        price_data[sym] = df

                if task['_stop_event'].is_set(): break

                task['message'] = f'🧮 正在计算策略选股（已获取 {len(price_data)} 只有效数据）...'
                task['progress'] = ''
                engine = PortfolioBacktestEngine(
                    commission_rate=task.get('commission_rate', 0.0003),
                    slippage_rate=task.get('slippage_rate', 0.005),
                    stamp_tax_rate=task.get('stamp_tax_rate', 0.0005),
                )
                result = engine.run(strategy, price_data, task['index_code'])
                target_symbols = set(h.symbol for h in result.final_holdings)
                task['message'] = f'📋 选股完成，目标持仓 {len(target_symbols)} 只'

                account_id = task['account_id']
                # 用任务配置的费率参数配置 broker（滑点/佣金/印花税）
                paper_manager.configure_broker(
                    account_id,
                    slippage_rate=task.get('slippage_rate', 0.005),
                    commission_rate=task.get('commission_rate', 0.0003),
                    stamp_tax_rate=task.get('stamp_tax_rate', 0.0005),
                )
                acc_info = paper_manager.get_account_detail(account_id)
                current_symbols = set(p['symbol'] for p in acc_info['positions'])

                to_sell = current_symbols - target_symbols
                to_buy = target_symbols - current_symbols
                to_keep = current_symbols & target_symbols

                sold = 0
                order_window_closed = False
                order_stop_message = ''
                if to_sell:
                    task['message'] = f'📤 正在卖出 {len(to_sell)} 只不再持有的股票...'
                for sym in to_sell:
                    if task['_stop_event'].is_set(): break
                    order_state = get_a_share_order_state()
                    if not order_state.is_trading_time:
                        order_window_closed = True
                        order_stop_message = order_state.message
                        task['message'] = f'调仓下单中止：{order_stop_message}'
                        break
                    ok = paper_manager.execute_sell(account_id, sym, 0)
                    if ok: sold += 1

                if to_buy and not task['_stop_event'].is_set() and not order_window_closed:
                    task['message'] = f'📥 正在买入 {len(to_buy)} 只新股票...'
                    acc_info = paper_manager.get_account_detail(account_id)
                    per_stock = (acc_info['cash'] * 0.95) / max(len(to_buy), 1)
                    bought = 0
                    anomaly_count = 0

                    allocation = task.get('allocation', 'even_amount')
                    fixed_lots = task.get('fixed_lots', 10)
                    rebalance_round = task.get('rebalance_count', 1)

                    from data.database import DatabaseManager as _AnomalyDB
                    anomaly_db = _AnomalyDB()
                    trade_date = now_shanghai().strftime('%Y-%m-%d')

                    for sym in to_buy:
                        if task['_stop_event'].is_set(): break
                        order_state = get_a_share_order_state()
                        if not order_state.is_trading_time:
                            order_window_closed = True
                            order_stop_message = order_state.message
                            task['message'] = f'调仓下单中止：{order_stop_message}'
                            break

                        # 组装动能数据备注
                        buy_remark = ''
                        try:
                            if sym in price_data and not price_data[sym].empty:
                                latest = price_data[sym].iloc[-1]
                                parts = []
                                # 量比（20日）
                                vr20 = latest.get('vol_ratio_20', 0)
                                if vr20 and float(vr20) > 0:
                                    parts.append(f"量比:{float(vr20):.1f}")
                                # 涨幅
                                c = float(latest.get('收盘', 0))
                                pc = float(latest.get('前收盘价', latest.get('_pre_close', 0)))
                                if c > 0 and pc > 0:
                                    pct = (c - pc) / pc * 100
                                    parts.append(f"涨幅:{pct:+.1f}%")
                                # 20日新高
                                is_high = latest.get('is_20d_high', 0)
                                if int(float(is_high)) == 1:
                                    parts.append("20日新高")
                                # 换手率（20日均值）
                                tr = latest.get('_avg_tr', latest.get('换手率', 0))
                                if tr and float(tr) > 0:
                                    parts.append(f"换手:{float(tr):.1f}%")
                                # 波动率
                                vol20 = latest.get('_vol20', 0)
                                if vol20 and float(vol20) > 0:
                                    parts.append(f"波动:{float(vol20):.0f}%")
                                # 5日量比
                                vr5 = latest.get('vol_ratio', 0)
                                if vr5 and float(vr5) > 0:
                                    parts.append(f"5日量比:{float(vr5):.1f}")
                                if parts:
                                    buy_remark = ' | '.join(parts)
                        except Exception:
                            pass

                        if allocation == 'fixed_lots':
                            ok = paper_manager.execute_buy(account_id, sym, shares=(fixed_lots * 100), remark=buy_remark)
                        else: # even_amount
                            ok = paper_manager.execute_buy(account_id, sym, amount=per_stock, remark=buy_remark)

                        if ok:
                            bought += 1
                        else:
                            # 买入失败 — 记录调仓异常日志
                            anomaly_count += 1
                            try:
                                # 尝试从 broker 读取拒绝原因
                                broker_reason = ''
                                try:
                                    broker = paper_manager._get_broker(account_id)
                                    broker_reason = getattr(broker, 'last_reject_reason', '')
                                except Exception:
                                    pass

                                quote = fetcher.get_realtime_quote(sym)
                                stock_price = float(quote.get('最新价', 0)) if quote else 0
                                stock_name = quote.get('名称', sym) if quote else sym
                                prev_close = float(quote.get('昨收价', 0)) if quote else 0

                                # 重新获取最新现金余额
                                latest_acc = paper_manager.get_account_detail(account_id)
                                current_cash = latest_acc.get('cash', 0)

                                min_cost = stock_price * 100  # 1手 = 100股

                                if broker_reason == 'MARKET_CLOSED':
                                    anomaly_type = 'MARKET_CLOSED'
                                    detail = f'{stock_name}({sym}) 非交易时段拒绝买入：{get_a_share_order_state().message}'
                                elif broker_reason == 'LIMIT_UP':
                                    anomaly_type = 'LIMIT_UP'
                                    detail = (f'{stock_name}({sym}) 涨停无法买入：'
                                              f'现价 ¥{stock_price:.2f}（昨收 ¥{prev_close:.2f}），'
                                              f'涨停板封死，挂单无法成交')
                                elif stock_price <= 0:
                                    anomaly_type = 'QUOTE_FAIL'
                                    detail = f'{stock_name}({sym}) 无法获取实时报价，跳过买入'
                                elif allocation == 'fixed_lots':
                                    need = stock_price * fixed_lots * 100
                                    anomaly_type = 'INSUFFICIENT_FUNDS'
                                    detail = (f'{stock_name}({sym}) 固定手数买入失败：'
                                              f'需要 ¥{need:,.0f}（{fixed_lots}手×{stock_price:.2f}），'
                                              f'可用现金 ¥{current_cash:,.0f}')
                                elif min_cost > per_stock:
                                    anomaly_type = 'PRICE_TOO_HIGH'
                                    detail = (f'{stock_name}({sym}) 股价过高无法买入1手：'
                                              f'1手最低需 ¥{min_cost:,.0f}（{stock_price:.2f}×100股），'
                                              f'等分额度仅 ¥{per_stock:,.0f}')
                                elif min_cost > current_cash:
                                    anomaly_type = 'CASH_EXHAUSTED'
                                    detail = (f'{stock_name}({sym}) 现金耗尽无法买入：'
                                              f'1手需 ¥{min_cost:,.0f}，'
                                              f'剩余现金仅 ¥{current_cash:,.0f}')
                                else:
                                    anomaly_type = 'BUY_FAILED'
                                    detail = (f'{stock_name}({sym}) 买入失败（未知原因），'
                                              f'股价 {stock_price:.2f}，现金 ¥{current_cash:,.0f}')

                                anomaly_db.log_rebalance_anomaly(
                                    task_id=task_id,
                                    account_id=account_id,
                                    rebalance_round=rebalance_round,
                                    symbol=sym,
                                    anomaly_type=anomaly_type,
                                    detail=detail,
                                    stock_price=stock_price,
                                    required_amount=min_cost if stock_price > 0 else 0,
                                    available_cash=current_cash,
                                    trade_date=trade_date,
                                )
                                logger.warning(f'Autopilot [{task_id}] 调仓异常: {detail}')
                            except Exception as log_err:
                                logger.error(f'记录调仓异常日志失败: {log_err}')
                else:
                    bought = 0
                    anomaly_count = 0

                if order_window_closed:
                    task['message'] = (
                        f'调仓下单中止：{order_stop_message or "交易窗口关闭"} '
                        f'| 卖出{sold} 买入{bought} 保持{len(to_keep)} '
                        f'| 下次 {task["next_rebalance_str"]}'
                    )
                else:
                    anomaly_msg = f' ⚠️异常{anomaly_count}' if anomaly_count > 0 else ''
                    task['message'] = (
                        f'✅ 第{task["rebalance_count"]}次调仓完成 '
                        f'| 卖出{sold} 买入{bought} 保持{len(to_keep)}{anomaly_msg} '
                        f'| 下次 {task["next_rebalance_str"]}'
                    )
                # 更新完成消息（last_rebalance 和 rebalance_count 已在调仓前持久化）
                _update_task_db(task_id, message=task['message'])

            except Exception as e:
                task['message'] = f'⚠️ 调仓异常: {str(e)[:80]}'
                logger.error(f'Autopilot [{task_id}] 调仓失败: {e}')
                _update_task_db(task_id, message=task['message'])

        for _ in range(CHECK_INTERVAL * 2):
            if task['_stop_event'].is_set(): break
            time.sleep(0.5)

    task['status'] = 'stopped'
    task['message'] = '策略守护已停止'
    _update_task_db(task_id, status='stopped', message=task['message'])


def _start_task_thread(task_id: str, strategy_key: str):
    """按策略类型启动对应后台线程。"""
    if strategy_key == 'volume_shock_observer':
        from paper_trading.volume_shock_observer import volume_shock_worker_entry
        thread = threading.Thread(
            target=volume_shock_worker_entry,
            args=(task_id, STRATEGY_TASKS[task_id]),
            daemon=True,
        )
    else:
        thread = threading.Thread(target=strategy_autopilot_worker, args=(task_id,), daemon=True)
    thread.start()
    return thread


def _restore_task(row: dict):
    """从数据库记录恢复一个任务并启动后台线程"""
    task_id = row['task_id']
    task_state = {
        'account_id': row['account_id'],
        'strategy_key': row['strategy_key'],
        'strategy_name': row['strategy_name'],
        'index_code': row['index_code'],
        'top_n': row.get('top_n', 50),
        'days': 120,
        'rebalance_interval_days': row.get('rebalance_days', 30),
        'status': 'starting',
        'message': '🔄 服务重启后自动恢复中...',
        'rebalance_count': row.get('rebalance_count', 0),
        'created_at': row.get('created_at', ''),
        'allocation': row.get('allocation', 'even_amount'),
        'fixed_lots': row.get('fixed_lots', 10),
        'stamp_tax_rate': row.get('stamp_tax_rate', 0.0005),
        'commission_rate': row.get('commission_rate', 0.0003),
        'slippage_rate': row.get('slippage_rate', 0.005),
        '_stop_event': threading.Event(),
    }
    # 恢复上次调仓时间
    if row.get('last_rebalance'):
        task_state['last_rebalance_str'] = row['last_rebalance']
        try:
            task_state['last_rebalance'] = datetime.strptime(row['last_rebalance'], '%Y-%m-%d %H:%M')
        except Exception:
            task_state['last_rebalance'] = None
    if row.get('next_rebalance'):
        task_state['next_rebalance_str'] = row['next_rebalance']

    STRATEGY_TASKS[task_id] = task_state
    _start_task_thread(task_id, row['strategy_key'])
    logger.info(f'已恢复策略任务: {task_id} ({row["strategy_name"]})')


class TaskStartRequest(PydanticBaseModel):
    account_id: str = 'local_1'
    strategy_key: str
    index_code: str = 'csi500'
    rebalance_days: int = 30
    top_n: int = 50
    initial_capital: float = 1000000.0
    allocation: str = 'even_amount'
    fixed_lots: int = 10
    stamp_tax_rate: float = 0.0005
    commission_rate: float = 0.0003
    slippage_rate: float = 0.005

@app.get("/api/market/indices")
async def get_market_indices():
    """获取主要大盘指数实时行情"""
    import requests
    # 上证, 深证, 创业板, 沪深300, 中证500, 中证1000, 科创50
    indices = {
        'sh000001': '上证指数',
        'sz399001': '深证成指',
        'sz399006': '创业板指',
        'sh000300': '沪深300',
        'sh000905': '中证500',
        'sh000852': '中证1000',
        'sh000688': '科创50',
    }
    codes_str = ",".join(indices.keys())
    url = f"http://qt.gtimg.cn/q={codes_str}"

    results = []
    try:
        resp = requests.get(url, timeout=5)
        text = resp.text
        for line in text.strip().split('\n'):
            if not line: continue
            # v_sh000001="1~上证指数~3218.15~..."
            # 提取前缀的 code
            code_part = line.split('=')[0].replace('v_', '')
            data_str = line.split('"')[1]
            parts = data_str.split('~')
            if len(parts) > 5:
                results.append({
                    'symbol': code_part,
                    'name': indices.get(code_part, parts[1]),
                    'price': float(parts[3]),
                    'change': float(parts[31]), # 涨跌额
                    'change_pct': float(parts[32]), # 涨跌幅 %
                    'volume': float(parts[6]), # 成交量(手)
                    'amount': float(parts[37]) # 成交额(万)
                })
    except Exception as e:
        logger.error(f"批量获取指数行情失败: {e}")

    return {"status": "ok", "data": results}

@app.get("/api/paper/tasks")
async def get_strategy_tasks():
    """获取所有策略守护任务状态（合并内存 + DB）"""
    strategies = _get_portfolio_strategies()

    # 先从内存取运行中的
    tasks = []
    seen_ids = set()
    for tid, t in STRATEGY_TASKS.items():
        s_key = t['strategy_key']
        s_desc = strategies[s_key].description if s_key in strategies else '策略守护任务'
        seen_ids.add(tid)
        tasks.append({
            'task_id': tid,
            'account_id': t['account_id'],
            'strategy_key': s_key,
            'strategy_name': t['strategy_name'],
            'strategy_desc': s_desc,
            'index_code': t['index_code'],
            'status': t['status'],
            'message': t['message'],
            'progress': t.get('progress', ''),
            'rebalance_count': t.get('rebalance_count', 0),
            'last_rebalance': t.get('last_rebalance_str', '--'),
            'next_rebalance': t.get('next_rebalance_str', '--'),
            'rebalance_days': t.get('rebalance_interval_days', 30),
            'created_at': t.get('created_at', ''),
            'stamp_tax_rate': t.get('stamp_tax_rate', 0.0005),
            'commission_rate': t.get('commission_rate', 0.0003),
            'slippage_rate': t.get('slippage_rate', 0.005),
        })
    # 再从 DB 取已停止的历史任务
    try:
        from data.database import DatabaseManager
        db = DatabaseManager()
        conn = db._get_conn()
        try:
            cursor = conn.cursor()
            ph = db._placeholder()
            cursor.execute(f'SELECT * FROM strategy_tasks WHERE status != {ph} ORDER BY created_at DESC LIMIT 20', ('running',))
            cols = [desc[0] for desc in cursor.description]
            for row in cursor.fetchall():
                r = dict(zip(cols, row))
                if r['task_id'] not in seen_ids:
                    s_key = r['strategy_key']
                    s_desc = strategies[s_key].description if s_key in strategies else '策略守护任务'
                    tasks.append({
                        'task_id': r['task_id'],
                        'account_id': r['account_id'],
                        'strategy_key': s_key,
                        'strategy_name': r['strategy_name'],
                        'strategy_desc': s_desc,
                        'index_code': r['index_code'],
                        'status': r['status'],
                        'message': r.get('message', ''),
                        'rebalance_count': r.get('rebalance_count', 0),
                        'last_rebalance': r.get('last_rebalance') or '--',
                        'next_rebalance': r.get('next_rebalance') or '--',
                        'rebalance_days': r.get('rebalance_days', 30),
                        'created_at': r.get('created_at', ''),
                        'stamp_tax_rate': r.get('stamp_tax_rate', 0.0005),
                        'commission_rate': r.get('commission_rate', 0.0003),
                        'slippage_rate': r.get('slippage_rate', 0.005),
                    })
        finally:
            conn.close()
    except Exception:
        pass
    try:
        from paper_trading.manager import paper_manager
        for t in tasks:
            try:
                acc_data = paper_manager.get_account_detail(t['account_id'])
                t['fin_total'] = acc_data.get('total_value', 0)
                t['fin_initial'] = acc_data.get('initial_capital', 0)
                t['fin_cash'] = acc_data.get('cash', 0)
                t['fin_market'] = acc_data.get('market_value', 0)
                t['fin_profit'] = acc_data.get('total_profit', 0)
            except Exception as e:
                # 若某个账户由于被删等原因获取不到详情，忽略即可，显示0
                t['fin_total'] = 0
                t['fin_initial'] = 0
                t['fin_cash'] = 0
                t['fin_market'] = 0
                t['fin_profit'] = 0
    except Exception as e:
        import logging
        logging.error(f"附加实盘财务模块加载失败: {e}")

    # 附加每个任务的最新异常日期和异常总数
    try:
        from data.database import DatabaseManager
        _adb = DatabaseManager()
        ph = _adb._placeholder()
        conn = _adb._get_conn()
        try:
            cursor = conn.cursor()
            for t in tasks:
                tid = t['task_id']
                cursor.execute(
                    f'SELECT MAX(created_at) as last_at, COUNT(*) as cnt FROM rebalance_anomaly_log WHERE task_id = {ph}',
                    (tid,)
                )
                row = cursor.fetchone()
                t['last_anomaly_at'] = row[0] or '' if row else ''
                t['anomaly_count'] = row[1] or 0 if row else 0
        finally:
            conn.close()
    except Exception:
        for t in tasks:
            t.setdefault('last_anomaly_at', '')
            t.setdefault('anomaly_count', 0)

    return {'tasks': tasks}
@app.post("/api/paper/task/start")
async def start_strategy_task(req: TaskStartRequest):
    """启动一个策略守护任务"""
    strategies = _get_portfolio_strategies()
    if req.strategy_key not in strategies:
        raise HTTPException(400, f'未知策略: {req.strategy_key}')

    for t in STRATEGY_TASKS.values():
        if (t['strategy_key'] == req.strategy_key and
            t['account_id'] == req.account_id and
            t['status'] == 'running'):
            raise HTTPException(400, '该策略已在运行中，请勿重复启动')

    task_id = str(uuid.uuid4())[:8]
    strategy = strategies[req.strategy_key]
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M')

    # 为每个策略任务创建一个专属的独立模拟账户，隔离资金和持仓
    task_account_id = f"task_{task_id}"
    from data.database import DatabaseManager
    db = DatabaseManager()
    db.add_or_update_paper_account(task_account_id, 'LOCAL', '', req.initial_capital, req.initial_capital)

    task_state = {
        'account_id': task_account_id,
        'strategy_key': req.strategy_key,
        'strategy_name': strategy.name,
        'index_code': req.index_code,
        'top_n': req.top_n,
        'days': 120,
        'rebalance_interval_days': req.rebalance_days,
        'status': 'starting',
        'message': '正在启动...',
        'rebalance_count': 0,
        'created_at': now_str,
        'allocation': req.allocation,
        'fixed_lots': req.fixed_lots,
        'stamp_tax_rate': req.stamp_tax_rate,
        'commission_rate': req.commission_rate,
        'slippage_rate': req.slippage_rate,
        'last_rebalance': None,
        '_stop_event': threading.Event(),
    }
    STRATEGY_TASKS[task_id] = task_state

    # 持久化到数据库
    _save_task_to_db(task_id, task_state)

    _start_task_thread(task_id, req.strategy_key)

    return {'status': 'ok', 'task_id': task_id, 'strategy': strategy.name}

@app.post("/api/paper/task/stop/{task_id}")
async def stop_strategy_task(task_id: str):
    """停止一个策略守护任务"""
    if task_id not in STRATEGY_TASKS:
        raise HTTPException(404, '任务不存在')
    task = STRATEGY_TASKS[task_id]
    task['_stop_event'].set()
    task['message'] = '正在停止...'
    return {'status': 'stopping'}

@app.post("/api/paper/task/restart/{task_id}")
async def restart_strategy_task(task_id: str):
    """重新启动一个已停止的策略守护任务"""
    from data.database import DatabaseManager
    # 如果任务还在运行，先停掉
    if task_id in STRATEGY_TASKS:
        old_task = STRATEGY_TASKS[task_id]
        if old_task.get('status') == 'running':
            raise HTTPException(400, '任务正在运行中，请先停止')
        old_task['_stop_event'].set()

    # 从 DB 读取任务配置
    db = DatabaseManager()
    ph = db._placeholder()
    conn = db._get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(f'SELECT * FROM strategy_tasks WHERE task_id = {ph}', (task_id,))
        columns = [desc[0] for desc in cursor.description]
        raw = cursor.fetchone()
        row = dict(zip(columns, raw)) if raw else None
    finally:
        conn.close()

    if not row:
        raise HTTPException(404, '数据库中未找到该任务')

    # 补上 account_id（DB 里可能没存）
    if 'account_id' not in row or not row['account_id']:
        row['account_id'] = f'task_{task_id}'

    # 更新 DB 状态为 running
    db._exec_write(f'UPDATE strategy_tasks SET status = {ph} WHERE task_id = {ph}', ('running', task_id))

    # 恢复并启动线程
    _restore_task(row)
    logger.info(f'手动重启策略任务: {task_id} ({row["strategy_name"]})')
    return {'status': 'ok', 'message': f'任务 {task_id} 已重新启动'}

@app.delete("/api/paper/task/{task_id}")
async def delete_strategy_task(task_id: str):
    """彻底删除一个策略任务及相关模拟账户"""
    from data.database import DatabaseManager

    if task_id in STRATEGY_TASKS:
        t = STRATEGY_TASKS[task_id]
        t['_stop_event'].set()
        account_id = t['account_id']
        STRATEGY_TASKS.pop(task_id, None)
    else:
        account_id = f"task_{task_id}"

    db = DatabaseManager()
    ph = db._placeholder()
    conn = db._get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(f'DELETE FROM strategy_tasks WHERE task_id = {ph}', (task_id,))
        cursor.execute(f'DELETE FROM trade_signal_ext WHERE task_id = {ph} OR account_id = {ph}', (task_id, account_id))
        cursor.execute(f'DELETE FROM paper_accounts WHERE account_id = {ph}', (account_id,))
        cursor.execute(f'DELETE FROM paper_positions WHERE account_id = {ph}', (account_id,))
        cursor.execute(f'DELETE FROM paper_trade_log WHERE account_id = {ph}', (account_id,))
        cursor.execute(f'DELETE FROM rebalance_anomaly_log WHERE task_id = {ph}', (task_id,))
        conn.commit()
    finally:
        conn.close()
    return {'status': 'deleted'}


@app.get("/api/paper/task/{task_id}/anomalies")
async def get_task_anomalies(task_id: str):
    """获取指定任务的调仓异常日志"""
    from data.database import DatabaseManager
    db = DatabaseManager()
    anomalies = db.get_rebalance_anomalies(task_id)
    return {"status": "ok", "anomalies": anomalies}


# ===== 极限矩阵压测 (Matrix Lab) =====
import subprocess
import uuid
import pymysql.cursors

@app.post("/api/matrix/start")
async def start_matrix_task(req: dict):
    from data.database import DatabaseManager

    task_id = str(uuid.uuid4())
    strats = req.get("strats", ["V2"])
    years = req.get("years", [1,3,5])
    capitals = req.get("capitals", [300000])

    years_str = ",".join(map(str, years))
    capitals_str = ",".join(map(str, capitals))
    strats_str = ",".join(strats)

    db = DatabaseManager()
    conn = db._get_conn()
    cursor = conn.cursor()

    task_name = f"矩阵压测 {len(strats)}模型 x {len(years)}年限"
    now_ts = time.strftime('%Y-%m-%d %H:%M:%S')

    try:
        sql = "INSERT INTO matrix_tasks (id, task_name, status, created_at, updated_at) VALUES (%s, %s, %s, %s, %s)"
        if db._db_type != 'mysql':
            sql = sql.replace('%s', '?')
        cursor.execute(sql, (task_id, task_name, 'pending', now_ts, now_ts))
        conn.commit()
    except Exception as e:
        logger.error(f"Error inserting matrix task: {e}")
        raise HTTPException(500, "Database error")
    finally:
        cursor.close()
        conn.close()

    # Launch subprocess
    cmd = [
        sys.executable,
        os.path.join(str(Path(__file__).parent), 'scripts', 'run_matrix_worker.py'),
        "--task_id", task_id,
        "--years", years_str,
        "--capital", capitals_str,
        "--strats", strats_str
    ]

    # Run fully detached, record PID
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Save PID so we can kill it later
    conn2 = db._get_conn()
    cur2 = conn2.cursor()
    cur2.execute("UPDATE matrix_tasks SET logs=%s WHERE id=%s", (f'Worker PID: {proc.pid}\n', task_id))
    conn2.commit()
    cur2.close()
    conn2.close()

    return {"status": "ok", "task_id": task_id}

@app.post("/api/matrix/stop/{task_id}")
async def stop_matrix_task(task_id: str):
    """停止正在运行的矩阵压测任务"""
    from data.database import DatabaseManager
    import signal

    db = DatabaseManager()
    conn = db._get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT status, logs FROM matrix_tasks WHERE id=%s", (task_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(404, "Task not found")

        status, logs = row
        if status not in ('running', 'pending'):
            return {"status": "ok", "message": "任务已结束"}

        # Extract PID from logs
        pid = None
        if logs:
            for line in logs.split('\n'):
                if 'Worker PID:' in line:
                    try:
                        pid = int(line.split('Worker PID:')[1].strip())
                    except:
                        pass

        # Kill process
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
                logger.info(f"已终止矩阵压测进程 PID={pid}")
            except ProcessLookupError:
                pass

        # Update DB
        now_ts = time.strftime('%Y-%m-%d %H:%M:%S')
        new_logs = (logs or '') + f'[{time.strftime("%H:%M:%S")}] ⛔ 用户手动终止了压测任务\n'
        cursor.execute("UPDATE matrix_tasks SET status=%s, logs=%s, updated_at=%s WHERE id=%s",
                       ('failed', new_logs, now_ts, task_id))
        conn.commit()
        return {"status": "ok", "message": "已停止"}
    finally:
        cursor.close()
        conn.close()

@app.get("/api/matrix/tasks")
async def get_matrix_tasks():
    from data.database import DatabaseManager
    db = DatabaseManager()
    conn = db._get_conn()
    cursor = conn.cursor(pymysql.cursors.DictCursor) if db._db_type == 'mysql' else conn.cursor()
    try:
        cursor.execute("SELECT id, task_name, status, progress, created_at, updated_at FROM matrix_tasks ORDER BY created_at DESC")
        if db._db_type == 'mysql':
            rows = cursor.fetchall()
            return {"status": "ok", "tasks": rows}
        else:
            rows = cursor.fetchall()
            keys = [description[0] for description in cursor.description]
            ret = [dict(zip(keys, row)) for row in rows]
            return {"status": "ok", "tasks": ret}
    finally:
        cursor.close()
        conn.close()

@app.get("/api/matrix/task/{task_id}")
async def get_matrix_task_detail(task_id: str):
    from data.database import DatabaseManager
    import json

    db = DatabaseManager()
    conn = db._get_conn()
    cursor = conn.cursor(pymysql.cursors.DictCursor) if db._db_type == 'mysql' else conn.cursor()
    try:
        sql = "SELECT * FROM matrix_tasks WHERE id=%s"
        if db._db_type != 'mysql':
            sql = sql.replace('%s', '?')
        cursor.execute(sql, (task_id,))
        if db._db_type == 'mysql':
            row = cursor.fetchone()
        else:
            row_raw = cursor.fetchone()
            if row_raw:
                keys = [description[0] for description in cursor.description]
                row = dict(zip(keys, row_raw))
            else:
                row = None
        if not row:
            raise HTTPException(404, "Task not found")

        if row.get('results_json'):
            try:
                row['results'] = json.loads(row['results_json'])
            except:
                row['results'] = None
        if 'results_json' in row:
            del row['results_json']
        return {"status": "ok", "task": row}
    finally:
        cursor.close()
        conn.close()

# 服务启动时延迟恢复数据库中 running 状态的任务，让 HTTP 服务先就绪
def _delayed_restore():
    """延迟 30 秒后恢复策略任务，避免启动时堵死 HTTP 服务"""
    time.sleep(30)
    try:
        rows = _load_tasks_from_db()
        for row in rows:
            _restore_task(row)
        if rows:
            logger.info(f'已从数据库延迟恢复 {len(rows)} 个策略守护任务')
    except Exception as e:
        logger.warning(f'恢复策略任务失败: {e}')

@app.on_event("startup")
async def restore_strategy_tasks():
    # 明确仅在此处做一次全库级别“异常中断回测”状态清理
    from data.database import DatabaseManager
    db = DatabaseManager()
    db.cleanup_dangling_backtest_tasks()

    restore_thread = threading.Thread(target=_delayed_restore, daemon=True)
    restore_thread.start()
    logger.info('策略任务将在 30 秒后自动恢复，HTTP 服务已就绪')


if __name__ == "__main__":
    import uvicorn
    logger.info("启动策略管理看板: http://localhost:8001")
    uvicorn.run(app, host="0.0.0.0", port=8001)
