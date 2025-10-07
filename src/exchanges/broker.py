"""
交易门面（Facade）模块
Trading facade module

作为统一的交易接口门面，对外暴露标准化的交易方法：
- query_price: 查询价格
- query_balance: 查询余额
- query_funding: 查询资金费率
- place_limit: 下限价单
- place_market: 下市价单
- cancel: 撤销订单

内部实现：
- 只依赖 exchanges/base.py 的接口协议
- 将具体实现转发给对应的交易所实例（Lighter/GRVT）
- 提供统一的错误处理和重试机制
- 支持多交易所的负载均衡和故障转移
"""

from __future__ import annotations
from typing import Optional, Literal
from dataclasses import dataclass
from tenacity import retry, stop_after_attempt, wait_exponential
from .base import PerpExchange, OrderReq, OrderAck, Balance, FeeSchedule

# 基础重试策略：网络/超时的温和重试
retryable = retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.2, max=2.0))



Role = Literal["long","short"]          # 也可用 name: Literal["lighter","grvt"]

@dataclass
class OrderParams:
    symbol: str
    side: Literal["buy","sell"]
    qty: float
    price: Optional[float] = None       # 市价单可为 None
    tif: Literal["gtc","ioc","fok"] = "gtc"
    post_only: bool = False
    reduce_only: bool = False
    client_order_id: Optional[str] = None
    max_slippage_bp: Optional[float] = None  # 给原子执行器/风控参考
    timeout_ms: int = 1500


class Broker:
    """对外唯一入口：统一的查询与下单接口（exchange-agnostic）。"""
    def __init__(self, long_leg: PerpExchange, short_leg: PerpExchange):
        self.long = long_leg
        self.short = short_leg

    # ============ 查询类 ============
    @retryable
    def query_price(self, ex: PerpExchange, symbol: str) -> float:
        """查询标记价"""
        return ex.mark_price(symbol)

    @retryable
    def query_balance(self, ex: PerpExchange) -> Balance:
        """查询余额"""
        return ex.get_balance()

    @retryable
    def query_funding(self, ex: PerpExchange, symbol: str):
        """查询资金费率"""
        return ex.funding(symbol)

    @retryable
    def query_fee_schedule(self, ex: PerpExchange, symbol: str) -> FeeSchedule:
        """查询手续费"""
        return ex.get_fee_schedule(symbol)

    # ============ 下单类 ============
    @retryable
    def place_limit(self, ex: PerpExchange, symbol: str, side: str, qty: float,
                    price: float, post_only: bool = False) -> OrderAck:
        """下限价单"""
        req: OrderReq = {
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "type": "limit",
            "price": price,
            "post_only": post_only,
        }
        return ex.place_order(req)

    @retryable
    def place_market(self, ex: PerpExchange, symbol: str, side: str, qty: float) -> OrderAck:
        """下市价单"""
        req: OrderReq = {
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "type": "market",
            "price": None,
            "post_only": False,
        }
        return ex.place_order(req)

    @retryable
    def cancel(self, ex: PerpExchange, order_id: str) -> None:
        """撤销订单"""
        return ex.cancel_order(order_id)



if __name__ == "__main__":
    broker = Broker()

