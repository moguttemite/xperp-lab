# -*- coding: utf-8 -*-
"""
base_exchange.py — 永续合约交易所接口基类
把“必须实现”的功能抽象成统一接口，具体交易所类继承并重写。
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Literal, TypedDict, Any, Dict, List, Callable

# ========== 通用类型 ==========
Side = Literal["buy", "sell"]
OrderType = Literal["limit", "market"]
TIF = Literal["gtc", "ioc", "fok"]
PositionSide = Literal["long", "short"]

# ---- 下单参数（统一入参） ----
@dataclass
class OrderParams:
    symbol: str
    side: Side
    qty: float
    order_type: OrderType = "limit"
    price: Optional[float] = None
    tif: TIF = "gtc"
    post_only: bool = False
    reduce_only: bool = False
    client_order_id: Optional[str] = None
    # 下面两个给“编排器/风控”参考：交易所不一定直接使用
    max_slippage_bp: Optional[float] = None
    timeout_ms: int = 1500

# ---- 统一返回结构 ----
class OrderAck(TypedDict, total=False):
    ok: bool
    order_id: Optional[str]
    client_order_id: Optional[str]
    status: Optional[str]          # new/filled/partial/canceled/rejected/...
    filled_qty: Optional[float]
    avg_fill_price: Optional[float]
    error: Optional[str]
    error_code: Optional[str]
    raw: Any                       # 保存原始返回便于调试

class Position(TypedDict, total=False):
    symbol: str
    side: Optional[PositionSide]
    size: float
    entry_price: float
    leverage: Optional[float]
    unrealized_pnl: Optional[float]
    liquidation_px: Optional[float]
    margin_mode: Optional[str]     # isolated/cross
    raw: Any

class Balance(TypedDict, total=False):
    asset: str
    balance: float
    available: float
    margin: Optional[float]
    raw: Any

class FundingInfo(TypedDict, total=False):
    symbol: str
    funding_rate: float
    next_funding_ts: Optional[int]
    raw: Any

class Ticker(TypedDict, total=False):
    symbol: str
    last: float
    mark: Optional[float]
    index: Optional[float]
    ts: int
    raw: Any

class Orderbook(TypedDict, total=False):
    symbol: str
    bids: List[List[float]]        # [[price, qty], ...]
    asks: List[List[float]]        # [[price, qty], ...]
    ts: int
    raw: Any

class Fill(TypedDict, total=False):
    symbol: str
    order_id: Optional[str]
    trade_id: Optional[str]
    side: Side
    price: float
    qty: float
    fee: Optional[float]
    liquidity: Optional[Literal["maker", "taker"]]
    ts: int
    raw: Any

class SymbolInfo(TypedDict, total=False):
    symbol: str
    price_tick_size: float
    qty_step_size: float
    min_qty: Optional[float]
    min_notional: Optional[float]
    max_leverage: Optional[float]
    margin_tiers: Optional[Any]    # 如有维持保证金阶梯，可挂原始结构
    raw: Any

# ---- 自定义异常（可扩展重试/分类） ----
class ExchangeError(Exception): ...
class RetryableError(ExchangeError): ...
class NonRetryableError(ExchangeError): ...

# ========== 抽象基类 ==========
class BaseExchange(ABC):
    """
    统一的交易所接口（必须实现）：
      - 行情/资金费
      - 账户/持仓
      - 下单/撤单/查询
      - WebSocket 公私域订阅
    说明：
      - 所有方法的 symbol 用你工程的“标准符号”，适配层负责与交易所符号映射。
      - 返回结构统一为上面的 TypedDict/Dataclass，便于跨所无感替换。
    """

    # ---- 行情 / 资金费 ----
    @abstractmethod
    def get_ticker(self, symbol: str) -> Ticker:
        """最新价/标记价/指数价（至少 last 有值）"""

    @abstractmethod
    def get_orderbook(self, symbol: str, depth: int = 50) -> Orderbook:
        """L2 盘口，用于滑点预算与委托校验"""

    @abstractmethod
    def get_funding_info(self, symbol: str) -> FundingInfo:
        """当前资金费与下一结算时间"""

    @abstractmethod
    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        """合约精度、阈值、最大杠杆、保证金档等"""

    # ---- 账户 / 持仓 ----
    @abstractmethod
    def get_balances(self) -> List[Balance]:
        """余额/可用保证金"""

    @abstractmethod
    def get_positions(self, symbol: Optional[str] = None) -> List[Position]:
        """持仓列表（可按 symbol 过滤）"""

    @abstractmethod
    def set_leverage(self, symbol: str, x: float) -> bool:
        """设置杠杆；不支持则抛 NonRetryableError"""

    @abstractmethod
    def set_margin_mode(self, symbol: str, mode: Literal["isolated", "cross"]) -> bool:
        """设置保证金模式；不支持则抛 NonRetryableError"""

    # ---- 订单：下单 / 撤单 / 查询 ----
    @abstractmethod
    def place_order(self, p: OrderParams) -> OrderAck:
        """统一下单入口（limit/market, gtc/ioc/fok, post_only, reduce_only, client_order_id）"""

    @abstractmethod
    def amend_order(
        self, symbol: str, order_id: str, price: Optional[float] = None, qty: Optional[float] = None
    ) -> OrderAck:
        """改单；不支持则由实现方执行“撤-下”模拟"""

    @abstractmethod
    def cancel_order(
        self, symbol: str, order_id: Optional[str] = None, client_order_id: Optional[str] = None
    ) -> OrderAck:
        """撤单；至少支持二选一的标识"""

    @abstractmethod
    def get_order(
        self, symbol: str, order_id: Optional[str] = None, client_order_id: Optional[str] = None
    ) -> OrderAck:
        """查询订单状态"""

    @abstractmethod
    def get_open_orders(self, symbol: Optional[str] = None) -> List[OrderAck]:
        """活动委托列表"""

    @abstractmethod
    def get_fills(self, symbol: str, since: Optional[int] = None) -> List[Fill]:
        """成交明细；since 可为毫秒时间戳或内部游标"""

    # ---- WebSocket（低延迟必需）----
    @abstractmethod
    def ws_sub_public(
        self,
        symbols: List[str],
        on_ticker: Optional[Callable[[Ticker], None]] = None,
        on_orderbook: Optional[Callable[[Orderbook], None]] = None,
    ) -> None:
        """订阅公共频道（ticker/mark/index/orderbook 等），内部应支持自动重连与心跳"""

    @abstractmethod
    def ws_sub_private(
        self,
        on_order: Optional[Callable[[OrderAck], None]] = None,
        on_fill: Optional[Callable[[Fill], None]] = None,
        on_position: Optional[Callable[[Position], None]] = None,
        on_balance: Optional[Callable[[Balance], None]] = None,
    ) -> None:
        """订阅私有频道（订单/成交/持仓/余额推送），需做断线状态补偿"""

    # ---- 可选：批量/便捷/资源管理（非必须，可在子类按需实现）----
    def place_orders_bulk(self, orders: List[OrderParams]) -> List[OrderAck]:
        """批量下单（可选），不实现可直接抛 NotImplementedError"""
        raise NotImplementedError

    def cancel_all(self, symbol: Optional[str] = None) -> bool:
        """撤所有（可选）"""
        raise NotImplementedError

    def close(self) -> None:
        """资源回收（会话/WS 关闭等）。可选。"""
        pass

    # ---- 通用辅助：统一的精度/最小值校验（可选）----
    def validate_order(self, p: OrderParams, info: SymbolInfo) -> None:
        """
        通用校验（可在调用 place_order 前使用）：
          - 最小数量/名义
          - 步进精度
          - 限价单必须给 price
        交易所侧仍需二次校验。
        """
        if p.order_type == "limit" and p.price is None:
            raise NonRetryableError("limit order requires price")
        if info.get("min_qty") and p.qty < float(info["min_qty"]):  # type: ignore[arg-type]
            raise NonRetryableError(f"qty<{info['min_qty']}")
        step = float(info["qty_step_size"])
        if round(p.qty / step) * step != p.qty:
            raise NonRetryableError(f"qty not aligned to stepSize={step}")
        if p.price is not None:
            tick = float(info["price_tick_size"])
            if round(p.price / tick) * tick != p.price:
                raise NonRetryableError(f"price not aligned to tickSize={tick}")
