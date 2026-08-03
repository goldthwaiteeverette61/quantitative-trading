import threading
from typing import Dict, Any, List
from loguru import logger

from data.database import DatabaseManager
from paper_trading.broker import BaseBroker, LocalBroker, XueqiuBroker

class PaperTradingManager:
    """
    模拟实盘组合与多账户统一大管家。
    支持不同账户采用不同的底层 Broker。
    """
    def __init__(self):
        self.db = DatabaseManager()
        self._brokers: Dict[str, BaseBroker] = {}
        self._trade_lock = threading.Lock()
        self._broker_configs: Dict[str, dict] = {}  # 账户级别的交易费率配置
        self._last_trade_ids: Dict[str, int] = {}

    def _get_broker(self, account_id: str) -> BaseBroker:
        """从数据库读取并懒惰初始化 Broker"""
        if account_id in self._brokers:
            return self._brokers[account_id]

        accounts = self.db.get_paper_accounts()
        acc = next((a for a in accounts if a['account_id'] == account_id), None)
        if not acc:
            raise ValueError(f"Account {account_id} not found in database")

        b_type = acc['broker_type'].upper()
        if b_type == 'LOCAL':
            cfg = self._broker_configs.get(account_id, {})
            broker = LocalBroker(
                account_id,
                slippage_rate=cfg.get('slippage_rate', 0.0),
                commission_rate=cfg.get('commission_rate', 0.0003),
                stamp_tax_rate=cfg.get('stamp_tax_rate', 0.001),
            )
        elif b_type == 'XUEQIU':
            broker = XueqiuBroker(account_id, auth_data=acc['auth_data'])
        else:
            raise ValueError(f"Unknown broker_type: {b_type}")

        self._brokers[account_id] = broker
        return broker

    def configure_broker(self, account_id: str, slippage_rate: float = 0.0,
                         commission_rate: float = 0.0003, stamp_tax_rate: float = 0.001):
        """为指定账户设置交易费率参数（重建 broker 实例以生效）"""
        self._broker_configs[account_id] = {
            'slippage_rate': slippage_rate,
            'commission_rate': commission_rate,
            'stamp_tax_rate': stamp_tax_rate,
        }
        # 清除旧实例，下次调用时会用新配置重建
        self._brokers.pop(account_id, None)

    def get_account_list(self) -> List[Dict[str, Any]]:
        """获取所有可用模拟账户（简要摘要）"""
        results = []
        for acc in self.db.get_paper_accounts():
            b_type = acc['broker_type']
            results.append({
                "account_id": acc['account_id'],
                "broker_type": b_type,
                "cash": acc['cash'],
                "initial_capital": acc['initial_capital']
            })
        return results

    def get_account_detail(self, account_id: str) -> Dict[str, Any]:
        """获取账户全景信息"""
        broker = self._get_broker(account_id)
        return broker.get_account_info()

    def bind_xueqiu_account(self, account_id: str, cookie_json_str: str):
        """绑定/更换雪球 Cookie 到指定账户"""
        # 注意: 如果需要自动创建此户
        accounts = self.db.get_paper_accounts()
        existing = next((a for a in accounts if a['account_id'] == account_id), None)

        init_cap = existing['initial_capital'] if existing else 1000000.0
        cash = existing['cash'] if existing else 1000000.0

        self.db.add_or_update_paper_account(
            account_id=account_id,
            broker_type='XUEQIU',
            auth_data=cookie_json_str,
            initial_capital=init_cap,
            cash=cash
        )
        # 清理内存中的老 broker 实例，强制下次重载
        self._brokers.pop(account_id, None)
        return {"status": "success", "message": f"Successfully bound Xueqiu cookie to {account_id}"}

    def execute_buy(self, account_id: str, symbol: str, amount: float = 0, shares: int = 0, remark: str = '') -> bool:
        """执行全网金额或股数买入"""
        with self._trade_lock:
            broker = self._get_broker(account_id)
            success = broker.buy(symbol, amount=amount, shares=shares, remark=remark)
            if success:
                self._last_trade_ids[account_id] = int(getattr(broker, 'last_trade_id', 0) or 0)
                logger.info(f"[{account_id}] {symbol} Buy {amount} / {shares} shares success")
            else:
                reason = getattr(broker, 'last_reject_reason', '')
                logger.error(f"[{account_id}] {symbol} Buy {amount} / {shares} shares failed reason={reason}")
            return success

    def get_last_trade_id(self, account_id: str) -> int:
        """返回指定账户最近一次成功成交流水 ID。"""
        return int(self._last_trade_ids.get(account_id, 0) or 0)

    def execute_sell(self, account_id: str, symbol: str, shares: int = 0, remark: str = '') -> bool:
        """执行全网指定股数卖出 (shares=0 代表全仓清空)"""
        with self._trade_lock:
            broker = self._get_broker(account_id)

            # 处理全仓清空逻辑
            if shares == 0:
                info = broker.get_account_info()
                pos = next((p for p in info.get("positions", []) if p['symbol'] == symbol), None)
                if not pos:
                    return False
                shares = pos['shares']

            success = broker.sell(symbol, shares, remark=remark)
            if success:
                self._last_trade_ids[account_id] = int(getattr(broker, 'last_trade_id', 0) or 0)
                logger.info(f"[{account_id}] {symbol} Sell {shares} success")
            else:
                reason = getattr(broker, 'last_reject_reason', '')
                logger.error(f"[{account_id}] {symbol} Sell {shares} failed reason={reason}")
            return success

# 全局单例实例化
paper_manager = PaperTradingManager()
