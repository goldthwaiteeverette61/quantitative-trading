"""A股交易日和交易时段统一门禁。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

OPEN_CALL_AUCTION_START = time(9, 15)
OPEN_CALL_AUCTION_END = time(9, 25)
MORNING_OPEN = time(9, 30)
MORNING_SCAN_START = time(9, 35)
MORNING_CLOSE = time(11, 30)
AFTERNOON_OPEN = time(13, 0)
ORDER_CUTOFF = time(14, 55)
CLOSE_CALL_AUCTION_START = time(14, 57)
MARKET_CLOSE = time(15, 0)

_A_SHARE_CLOSED_RANGES_2026 = (
    ("2026-01-01", "2026-01-02"),
    ("2026-02-16", "2026-02-23"),
    ("2026-04-06", "2026-04-06"),
    ("2026-05-01", "2026-05-05"),
    ("2026-06-19", "2026-06-19"),
    ("2026-09-25", "2026-09-25"),
    ("2026-10-01", "2026-10-07"),
)


def _date_range(start: str, end: str) -> set[date]:
    start_day = date.fromisoformat(start)
    end_day = date.fromisoformat(end)
    days = (end_day - start_day).days
    return {start_day + timedelta(days=i) for i in range(days + 1)}


A_SHARE_CLOSED_DATES = set().union(
    *(_date_range(start, end) for start, end in _A_SHARE_CLOSED_RANGES_2026)
)


@dataclass(frozen=True)
class TradingSessionState:
    """交易门禁判断结果。"""

    is_trading_day: bool
    is_trading_time: bool
    phase: str
    message: str


def now_shanghai() -> datetime:
    """返回上海时区的无时区 datetime，兼容现有代码的 naive datetime。"""
    return datetime.now(SHANGHAI_TZ).replace(tzinfo=None)


def _to_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _to_time(value: datetime | time) -> time:
    if isinstance(value, datetime):
        return value.time().replace(tzinfo=None)
    return value


def _normalize_datetime(value: datetime | None) -> datetime:
    if value is None:
        return now_shanghai()
    if value.tzinfo is not None:
        return value.astimezone(SHANGHAI_TZ).replace(tzinfo=None)
    return value


def is_a_share_trading_day(
    value: date | datetime | str,
    known_trading_dates: set[date] | None = None,
) -> bool:
    """判断A股交易日。

    回测可传入历史K线日期集合；模拟实盘使用周末+已知休市日兜底。
    """
    day = _to_date(value)
    if known_trading_dates is not None:
        return day in known_trading_dates
    if day.weekday() >= 5:
        return False
    if day in A_SHARE_CLOSED_DATES:
        return False
    return True


def filter_a_share_trading_dates(values):
    """过滤掉周末和已知休市日，保留原始日期对象类型。"""
    return [value for value in values if is_a_share_trading_day(value)]


def count_a_share_trading_days(
    start: date | datetime | str,
    end: date | datetime | str,
    include_start: bool = False,
    include_end: bool = True,
) -> int:
    """统计区间内A股交易日数量。"""
    start_day = _to_date(start)
    end_day = _to_date(end)
    if end_day < start_day:
        return 0

    current = start_day if include_start else start_day + timedelta(days=1)
    last = end_day if include_end else end_day - timedelta(days=1)
    count = 0
    while current <= last:
        if is_a_share_trading_day(current):
            count += 1
        current += timedelta(days=1)
    return count


def is_a_share_precompute_time(now: datetime | None = None) -> bool:
    now_dt = _normalize_datetime(now)
    if not is_a_share_trading_day(now_dt):
        return False
    current = _to_time(now_dt)
    return OPEN_CALL_AUCTION_START <= current < OPEN_CALL_AUCTION_END


def _window_contains(current: time, start: time, end: time) -> bool:
    return start <= current < end


def get_a_share_trading_state(
    now: datetime | None = None,
    *,
    scan_start: time = MORNING_OPEN,
    afternoon_cutoff: time = ORDER_CUTOFF,
    include_precompute: bool = False,
) -> TradingSessionState:
    """返回当前A股即时成交/扫描窗口状态。

    这里不模拟开盘集合竞价和收盘集合竞价。普通即时成交统一截止到14:55，
    为策略计算和网络延迟留出保护边界。
    """
    now_dt = _normalize_datetime(now)
    day = now_dt.date()
    current = now_dt.time().replace(tzinfo=None)
    time_text = now_dt.strftime("%H:%M")

    if not is_a_share_trading_day(day):
        if day.weekday() >= 5:
            return TradingSessionState(False, False, "weekend", "周末休市，等待下一个交易日")
        return TradingSessionState(False, False, "holiday", f"休市日 {day.isoformat()}，等待下一个交易日")

    if include_precompute and OPEN_CALL_AUCTION_START <= current < OPEN_CALL_AUCTION_END:
        return TradingSessionState(True, False, "precompute", "PRECOMPUTE")

    if current < OPEN_CALL_AUCTION_START:
        return TradingSessionState(True, False, "before_open", f"尚未开盘 {time_text}")
    if OPEN_CALL_AUCTION_START <= current < OPEN_CALL_AUCTION_END:
        return TradingSessionState(True, False, "opening_call_auction", f"开盘集合竞价 {time_text}，不模拟即时成交")
    if OPEN_CALL_AUCTION_END <= current < MORNING_OPEN:
        return TradingSessionState(True, False, "before_continuous", f"开盘前静默 {time_text}")

    if _window_contains(current, MORNING_OPEN, MORNING_CLOSE):
        if current < scan_start:
            return TradingSessionState(True, False, "morning_quiet", f"早盘静默 {time_text}")
        return TradingSessionState(True, True, "continuous_auction", "")

    if MORNING_CLOSE <= current < AFTERNOON_OPEN:
        return TradingSessionState(True, False, "lunch_break", f"午休 {time_text}")

    if _window_contains(current, AFTERNOON_OPEN, afternoon_cutoff):
        return TradingSessionState(True, True, "continuous_auction", "")

    if afternoon_cutoff <= current < CLOSE_CALL_AUCTION_START:
        return TradingSessionState(True, False, "order_cutoff", f"尾盘保护窗口 {time_text}，停止普通即时成交")
    if CLOSE_CALL_AUCTION_START <= current <= MARKET_CLOSE:
        return TradingSessionState(True, False, "closing_call_auction", f"收盘集合竞价 {time_text}，不模拟即时成交")

    return TradingSessionState(True, False, "closed", f"已收盘 {time_text}")


def get_a_share_order_state(now: datetime | None = None) -> TradingSessionState:
    """普通买卖下单门禁。"""
    return get_a_share_trading_state(now, scan_start=MORNING_OPEN, afternoon_cutoff=ORDER_CUTOFF)
