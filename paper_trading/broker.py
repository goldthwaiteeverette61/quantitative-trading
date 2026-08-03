import abc
from typing import Dict, List, Any
from loguru import logger
from data.trading_calendar import get_a_share_order_state, now_shanghai

from data.database import DatabaseManager
from data.fetcher import DataFetcher

class BaseBroker(abc.ABC):
    """
    代理交易层基类
    定义了模拟盘或实盘必须实现的统一接口
    """

    @abc.abstractmethod
    def get_account_info(self) -> Dict[str, Any]:
        """获取资产与持仓信息"""
        pass

    @abc.abstractmethod
    def buy(self, symbol: str, amount: float = 0, shares: int = 0, remark: str = '') -> bool:
        """买入（支持按金额或按股数）"""
        pass

    @abc.abstractmethod
    def sell(self, symbol: str, shares: int) -> bool:
        """指定股数卖出"""
        pass


class LocalBroker(BaseBroker):
    """
    本地零门槛数据库模拟盘
    """
    def __init__(self, account_id: str, slippage_rate: float = 0.0,
                 commission_rate: float = 0.0003, stamp_tax_rate: float = 0.001):
        self.account_id = account_id
        self.db = DatabaseManager()
        self.fetcher = DataFetcher()
        self.slippage_rate = slippage_rate       # 滑点率（仅模拟盘人为施加，实盘自然产生无需设置）
        self.commission_rate = commission_rate    # 佣金费率（万三）
        self.stamp_tax_rate = stamp_tax_rate      # 印花税率（千一，仅卖出收取）
        self.last_reject_reason = ''  # 最近一次交易被拒原因
        self.last_trade_id = 0         # 最近一次成交流水 ID
        logger.info(f"LocalBroker[{self.account_id}] 初始化完成 (滑点{slippage_rate*100:.2f}% 佣金{commission_rate*100:.2f}% 印花{stamp_tax_rate*100:.2f}%)")

    @staticmethod
    def _is_limit_up(symbol: str, price: float, prev_close: float) -> bool:
        """判断是否涨停（A股规则：主板10%，创业板/科创板20%，ST 5%）"""
        if prev_close <= 0 or price <= 0:
            return False
        # 创业板(300/301) 和 科创板(688) 涨停幅度 20%
        if symbol.startswith('300') or symbol.startswith('301') or symbol.startswith('688'):
            limit_pct = 0.20
        else:
            limit_pct = 0.10
        limit_price = round(prev_close * (1 + limit_pct), 2)
        return price >= limit_price

    def _bought_today(self, symbol: str) -> bool:
        """检查该股票是否今日买入（T+1 限制）"""
        today_str = now_shanghai().strftime('%Y-%m-%d')
        trades = self.db.get_paper_trade_log(self.account_id)
        if not trades:
            return False
        for t in trades:
            ts = t.get('trade_time', '')
            if ts.startswith(today_str) and t.get('action') == 'BUY' and t.get('symbol') == symbol:
                return True
        return False

    def _check_order_gate(self, action: str, symbol: str) -> bool:
        """统一A股普通即时成交门禁。"""
        state = get_a_share_order_state()
        if state.is_trading_time:
            return True
        self.last_reject_reason = 'MARKET_CLOSED'
        logger.warning(f"A股非普通即时成交窗口，拒绝{action}: {symbol} | {state.message}")
        return False

    def get_account_info(self) -> Dict[str, Any]:
        accounts = self.db.get_paper_accounts()
        acc = next((a for a in accounts if a['account_id'] == self.account_id), None)
        if not acc:
            raise ValueError(f"Account {self.account_id} not found in DB")

        positions_raw = self.db.get_paper_positions(self.account_id)

        positions = []
        market_value = 0.0

        for p in positions_raw:
            symbol = p['symbol']
            quote = self.fetcher.get_realtime_quote(symbol) or {}
            current_price = float(quote.get('最新价', p['cost_price']) or p['cost_price'])
            profit = (current_price - p['cost_price']) * p['shares']

            p_market_value = current_price * p['shares']
            market_value += p_market_value

            positions.append({
                "symbol": symbol,
                "name": quote.get('名称', symbol),
                "shares": p['shares'],
                "cost_price": p['cost_price'],
                "current_price": current_price,
                "market_value": p_market_value,
                "profit": profit,
                "profit_pct": (current_price - p['cost_price']) / (p['cost_price'] or 1.0) * 100
            })

        total_value = acc['cash'] + market_value
        # 统计该账户累计交易费用
        total_commission = 0.0
        try:
            trades = self.db.get_paper_trade_log(self.account_id)
            total_commission = sum(t.get('commission', 0) for t in trades)
        except Exception:
            pass
        return {
            "account_id": acc['account_id'],
            "initial_capital": acc['initial_capital'],
            "cash": acc['cash'],
            "market_value": market_value,
            "total_value": total_value,
            "total_profit": total_value - acc['initial_capital'],
            "total_commission": total_commission,
            "positions": positions
        }

    def buy(self, symbol: str, amount: float = 0, shares: int = 0, remark: str = '') -> bool:
        self.last_reject_reason = ''
        self.last_trade_id = 0
        if not self._check_order_gate('买入', symbol):
            return False

        acc = next((a for a in self.db.get_paper_accounts() if a['account_id'] == self.account_id), None)
        if not acc: return False

        quote = self.fetcher.get_realtime_quote(symbol)
        if not quote or '最新价' not in quote or quote['最新价'] <= 0:
            self.last_reject_reason = 'QUOTE_FAIL'
            logger.warning(f"获取 {symbol} 现价失败，拒绝买入")
            return False

        price = float(quote['最新价'])
        prev_close = float(quote.get('昨收价', 0))

        # 涨停检测 — 涨停板无法挂单买入
        if self._is_limit_up(symbol, price, prev_close):
            self.last_reject_reason = 'LIMIT_UP'
            name = quote.get('名称', symbol)
            logger.warning(f"{name}({symbol}) 涨停 {price:.2f}（昨收 {prev_close:.2f}），拒绝买入")
            return False

        # 模拟滑点：买入价上浮
        exec_price = price * (1 + self.slippage_rate)

        target_shares = shares
        if target_shares <= 0:
            if acc['cash'] < amount:
                logger.warning(f"余额不足: 余额 {acc['cash']} < 买入 {amount}")
                amount = acc['cash']
            # 逢 100 股取整
            target_shares = int(amount / exec_price / 100) * 100

        if target_shares < 100:
            self.last_reject_reason = 'INSUFFICIENT_FUNDS'
            logger.warning("买入资金不足 1手")
            return False

        cost = target_shares * exec_price
        commission = cost * self.commission_rate
        if acc['cash'] < cost + commission:
            target_shares = int((acc['cash'] - commission) / exec_price / 100) * 100
            cost = target_shares * exec_price
            commission = cost * self.commission_rate

        if target_shares < 100:
            self.last_reject_reason = 'INSUFFICIENT_FUNDS'
            return False

        new_cash = acc['cash'] - cost - commission
        self.db.update_paper_cash(self.account_id, new_cash)

        # 更新持仓均价（以含滑点的执行价记录成本）
        positions = self.db.get_paper_positions(self.account_id)
        pos = next((p for p in positions if p['symbol'] == symbol), None)
        if pos:
            total_cost = (pos['shares'] * pos['cost_price']) + cost
            new_shares = pos['shares'] + target_shares
            new_cost_price = total_cost / new_shares
            self.db.update_paper_position(self.account_id, symbol, new_shares, new_cost_price)
        else:
            self.db.update_paper_position(self.account_id, symbol, target_shares, exec_price)

        # 记录日志（记录实际执行价，含滑点）
        self.last_trade_id = self.db.log_paper_trade(
            self.account_id, symbol, "BUY", target_shares, exec_price, cost, commission,
            now_shanghai().strftime("%Y-%m-%d %H:%M:%S"), remark=remark
        )
        return True

    def sell(self, symbol: str, shares: int, remark: str = '') -> bool:
        self.last_reject_reason = ''
        self.last_trade_id = 0
        if not self._check_order_gate('卖出', symbol):
            return False

        positions = self.db.get_paper_positions(self.account_id)
        pos = next((p for p in positions if p['symbol'] == symbol), None)
        if not pos or pos['shares'] < shares:
            self.last_reject_reason = 'NO_POSITION'
            logger.warning("持仓不足，拒绝卖出")
            return False

        # T+1 限制 — 今日买入的股票不能当日卖出
        if self._bought_today(symbol):
            self.last_reject_reason = 'T_PLUS_1'
            logger.warning(f"{symbol} 今日买入，T+1限制不可卖出")
            return False

        quote = self.fetcher.get_realtime_quote(symbol)
        if not quote or '最新价' not in quote or quote['最新价'] <= 0:
            self.last_reject_reason = 'QUOTE_FAIL'
            logger.warning(f"获取 {symbol} 现价失败，拒绝卖出")
            return False

        price = float(quote['最新价'])

        # 模拟滑点：卖出价下浮
        exec_price = price * (1 - self.slippage_rate)

        revenue = shares * exec_price
        commission = revenue * self.commission_rate + revenue * self.stamp_tax_rate  # 佣金 + 印花税

        acc = next((a for a in self.db.get_paper_accounts() if a['account_id'] == self.account_id), None)
        new_cash = acc['cash'] + revenue - commission
        self.db.update_paper_cash(self.account_id, new_cash)

        self.db.update_paper_position(self.account_id, symbol, pos['shares'] - shares, pos['cost_price'])

        self.last_trade_id = self.db.log_paper_trade(
            self.account_id, symbol, "SELL", shares, exec_price, revenue, commission,
            now_shanghai().strftime("%Y-%m-%d %H:%M:%S"), remark=remark
        )
        return True


class XueqiuBroker(BaseBroker):
    """
    雪球组合代理接入层
    通过 easytrader 库，利用用户配置的 Cookie 接管真实雪球组合。
    """
    def __init__(self, account_id: str, auth_data: str):
        self.account_id = account_id
        self.last_reject_reason = ''
        self.last_trade_id = 0
        import easytrader
        self.user = easytrader.use('xq')
        try:
            self.user.prepare(auth_data) # auth_data 即保存了 cookie 信息的 JSON 字符串或路径
            logger.info(f"XueqiuBroker[{self.account_id}] 初始化并挂载 Cookie 成功")
            self.connected = True
        except Exception as e:
            logger.error(f"XueqiuBroker 初始化失败, 检查 Cookie: {str(e)}")
            self.connected = False

    def get_account_info(self) -> Dict[str, Any]:
        if not self.connected:
            raise ConnectionError("雪球未连接或 Cookie 已过期")

        balance = self.user.balance
        positions_raw = self.user.position

        positions = []
        for p in positions_raw:
            positions.append({
                "symbol": p['stock_code'],
                "name": p['stock_name'],
                "shares": p['current_amount'],
                "cost_price": float(p['cost_price']),
                "current_price": float(p['last_price']),
                "market_value": float(p['market_value']),
                "profit": float(p['keep_profit']),
                "profit_pct": float(p['income_rate']) * 100
            })

        return {
            "account_id": self.account_id,
            "initial_capital": 0.0, # 雪球不提供原始本金，由收益率推导或留空
            "cash": float(balance[0]['current_balance']),
            "market_value": float(balance[0]['market_value']),
            "total_value": float(balance[0]['asset_balance']),
            "total_profit": float(balance[0]['asset_balance']) - float(balance[0]['enable_balance']), # 近似
            "positions": positions
        }

    def _check_order_gate(self, action: str, symbol: str) -> bool:
        state = get_a_share_order_state()
        if state.is_trading_time:
            return True
        self.last_reject_reason = 'MARKET_CLOSED'
        logger.warning(f"A股非普通即时成交窗口，拒绝雪球{action}: {symbol} | {state.message}")
        return False

    def buy(self, symbol: str, amount: float = 0, shares: int = 0, remark: str = '') -> bool:
        self.last_reject_reason = ''
        self.last_trade_id = 0
        if not self.connected:
            self.last_reject_reason = 'NOT_CONNECTED'
            return False
        if not self._check_order_gate('买入', symbol):
            return False
        try:
            logger.info(f"雪球下单买入: {symbol} 资金 {amount} 股数 {shares}")
            # 简化为市价单处理
            if shares > 0:
                self.user.buy(symbol, price=0, amount=shares)
            else:
                self.user.buy(symbol, price=0, amount=amount)
            return True
        except Exception as e:
            logger.error(f"雪球下单失败: {str(e)}")
            return False

    def sell(self, symbol: str, shares: int, remark: str = '') -> bool:
        self.last_reject_reason = ''
        self.last_trade_id = 0
        if not self.connected:
            self.last_reject_reason = 'NOT_CONNECTED'
            return False
        if not self._check_order_gate('卖出', symbol):
            return False
        try:
            self.user.sell(symbol, price=0, amount=shares)
            return True
        except Exception as e:
            logger.error(f"雪球发单失败: {str(e)}")
            return False
