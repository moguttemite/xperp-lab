# -*- coding: utf-8 -*-
"""
bybit.py — Bybit 永续合约适配器（实现 BaseExchange 抽象接口）

用法：
    ex = BybitExchange(api_key="...", api_secret="...")
    ack = ex.place_order(OrderParams(symbol="BTCUSDT", side="buy", qty=0.001, order_type="market"))

说明：
- 基于 Bybit V5 API（统一账户）
- 方法签名/返回值结构与 base_exchange.py 严格一致
- 支持 USDT 永续合约（linear）
"""

from __future__ import annotations

import os
import time
import hmac
import hashlib
import logging
import threading
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Literal
from urllib.parse import urlencode

try:
    import requests
except Exception:
    requests = None

# —— 统一接口与类型：来自 base_exchange.py ——
from ..base_exchange import (
    BaseExchange,
    OrderParams, OrderAck, Position, Balance, FundingInfo, Ticker, Orderbook, Fill, SymbolInfo,
    ExchangeError, RetryableError, NonRetryableError,
)

# ============ 符号映射 ===============

# 工程标准符号 -> Bybit 符号（如果一致可以为空）
SYMBOL_ALIASES: Dict[str, str] = {
    # 示例：如果你的工程用 "BTCUSDT"，Bybit 也用 "BTCUSDT"，则不需要映射
    # "BTCUSDT": "BTCUSDT",
}

# 反向映射
REVERSE_ALIASES: Dict[str, str] = {v: k for k, v in SYMBOL_ALIASES.items()}


# ================== 配置 ==================
from dotenv import load_dotenv
# 获取当前目录
current_dir = Path(__file__).parent
dotenv_path = current_dir / '.env'
load_dotenv(dotenv_path=dotenv_path, override=False, verbose=True)

@dataclass
class BybitConfig:
    """Bybit 交易所配置"""
    # API 凭证
    api_key: os.getenv("BYBIT_API_KEY")
    api_secret: os.getenv("BYBIT_API_SECRET")
    testnet_url: str = os.getenv("BYBIT_TESTNET_URL")
    mainnet_url: str = os.getenv("BYBIT_MAINNET_URL")

    # 网络配置
    testnet: bool = False  # True 则使用测试网
    base_url: str = testnet_url if testnet else mainnet_url
    
# ================== 实现类 ==================

class BybitExchange(BaseExchange):
    """Bybit 永续合约交易所适配器"""
    
    def __init__(
        self,
        testnet: bool = True,
        session: Optional["requests.Session"] = None
    ) -> None:
        """
        初始化 Bybit 交易所连接
        
        Args:
            testnet: 是否使用测试网
            session: 自定义 requests.Session（可选）
        """
        if requests is None:
            raise RuntimeError("requests library not installed")
        
        self.cfg = BybitConfig(
            testnet=testnet,
        )
        
        self._http = session or requests.Session()
        self._log = logging.getLogger(self.__class__.__name__)
        self._lock = threading.Lock()
        self._clock_skew_ms = 0
        
        # 交易所↔内部符号映射
        self._symbol_map = SYMBOL_ALIASES
    
    # ----------------- 私有工具方法 -----------------
    
    def _ts_ms(self) -> int:
        """获取当前时间戳（毫秒）"""
        return int(time.time() * 1000) + self._clock_skew_ms
    
    def _map_symbol_out(self, symbol: str) -> str:
        """内部标准符号 -> Bybit 符号"""
        # 若 symbol 不在映射表中，则返回原 symbol（支持空/部分映射，避免 None）
        return self._symbol_map.get(symbol, symbol)
    
    def _map_symbol_in(self, symbol: str) -> str:
        """Bybit 符号 -> 内部标准符号"""
        return REVERSE_ALIASES.get(symbol, symbol)
    
    def _sign(self, timestamp: int, params: str) -> str:
        """
        生成 Bybit V5 API 签名
        签名规则: HMAC-SHA256(timestamp + api_key + recv_window + params)
        """
        param_str = f"{timestamp}{self.cfg.api_key}{self.cfg.recv_window_ms}{params}"
        return hmac.new(
            self.cfg.api_secret.encode('utf-8'),
            param_str.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
    
    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        auth: bool = True
    ) -> Any:
        """
        发送 HTTP 请求到 Bybit API
        
        Args:
            method: HTTP 方法 (GET/POST)
            endpoint: API 端点路径
            params: 请求参数
            auth: 是否需要签名认证
        
        Returns:
            API 响应的 result 部分
        """
        url = self.cfg.base_url.rstrip("/") + endpoint
        params = params or {}
        
        # 移除 None 值
        params = {k: v for k, v in params.items() if v is not None}
        
        headers = {
            "Content-Type": "application/json",
            "User-Agent": self.cfg.user_agent,
        }
        
        # 添加签名
        if auth:
            timestamp = self._ts_ms()
            
            if method.upper() == "GET":
                param_str = urlencode(sorted(params.items()))
            else:
                import json
                param_str = json.dumps(params) if params else ""
            
            signature = self._sign(timestamp, param_str)
            
            headers.update({
                "X-BAPI-API-KEY": self.cfg.api_key,
                "X-BAPI-SIGN": signature,
                "X-BAPI-TIMESTAMP": str(timestamp),
                "X-BAPI-RECV-WINDOW": str(self.cfg.recv_window_ms),
            })
        
        # 重试逻辑
        for attempt in range(self.cfg.max_retries + 1):
            try:
                if method.upper() == "GET":
                    r = self._http.get(url, params=params, headers=headers, timeout=self.cfg.timeout_sec)
                else:
                    r = self._http.post(url, json=params, headers=headers, timeout=self.cfg.timeout_sec)
                
                # 解析响应
                data = r.json()
                ret_code = data.get("retCode", -1)
                
                # Bybit 返回码: 0 = 成功
                if ret_code == 0:
                    return data.get("result", {})
                
                # 错误处理
                ret_msg = data.get("retMsg", "Unknown error")
                
                # 可重试错误（如限流、超时等）
                if ret_code in [10006, 10016, 10018]:  # 限流相关
                    raise RetryableError(f"Bybit error {ret_code}: {ret_msg}")
                
                # 不可重试错误
                raise NonRetryableError(f"Bybit error {ret_code}: {ret_msg}")
                
            except RetryableError as e:
                if attempt >= self.cfg.max_retries:
                    raise
                self._log.warning(f"Retryable error: {e} (attempt {attempt+1}/{self.cfg.max_retries})")
                time.sleep(self.cfg.retry_backoff_sec * (attempt + 1))
                
            except NonRetryableError:
                raise
                
            except Exception as e:
                if attempt >= self.cfg.max_retries:
                    raise RetryableError(f"Network error: {e}")
                self._log.warning(f"Network error: {e} (attempt {attempt+1}/{self.cfg.max_retries})")
                time.sleep(self.cfg.retry_backoff_sec * (attempt + 1))
    
    # ----------------- 行情 / 资金费 -----------------
    
    def get_ticker(self, symbol: str) -> Ticker:
        """获取行情 Ticker"""
        ex_symbol = self._map_symbol_out(symbol)
        data = self._request("GET", "/v5/market/tickers", params={
            "category": "linear",
            "symbol": ex_symbol
        }, auth=False)
        
        if not data or "list" not in data or len(data["list"]) == 0:
            raise NonRetryableError(f"No ticker data for {symbol}")
        
        ticker = data["list"][0]
        
        return Ticker(
            symbol=symbol,
            last=float(ticker.get("lastPrice", 0)),
            mark=float(ticker.get("markPrice", 0)) if ticker.get("markPrice") else None,
            index=float(ticker.get("indexPrice", 0)) if ticker.get("indexPrice") else None,
            ts=int(ticker.get("time", self._ts_ms())),
            raw=ticker
        )
    
    def get_orderbook(self, symbol: str, depth: int = 50) -> Orderbook:
        """获取订单簿"""
        ex_symbol = self._map_symbol_out(symbol)
        
        # Bybit 支持的深度: 1, 25, 50, 100, 200, 500
        valid_depths = [1, 25, 50, 100, 200, 500]
        limit = min(valid_depths, key=lambda x: abs(x - depth))
        
        data = self._request("GET", "/v5/market/orderbook", params={
            "category": "linear",
            "symbol": ex_symbol,
            "limit": limit
        }, auth=False)
        
        bids = [[float(p), float(q)] for p, q in data.get("b", [])]
        asks = [[float(p), float(q)] for p, q in data.get("a", [])]
        
        return Orderbook(
            symbol=symbol,
            bids=bids,
            asks=asks,
            ts=int(data.get("ts", self._ts_ms())),
            raw=data
        )
    
    def get_funding_info(self, symbol: str) -> FundingInfo:
        """获取资金费率信息"""
        ex_symbol = self._map_symbol_out(symbol)
        data = self._request("GET", "/v5/market/tickers", params={
            "category": "linear",
            "symbol": ex_symbol
        }, auth=False)
        
        if not data or "list" not in data or len(data["list"]) == 0:
            raise NonRetryableError(f"No funding info for {symbol}")
        
        info = data["list"][0]
        
        return FundingInfo(
            symbol=symbol,
            funding_rate=float(info.get("fundingRate", 0)),
            next_funding_ts=int(info.get("nextFundingTime", 0)) if info.get("nextFundingTime") else None,
            raw=info
        )
    
    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        """获取合约信息"""
        ex_symbol = self._map_symbol_out(symbol)
        data = self._request("GET", "/v5/market/instruments-info", params={
            "category": "linear",
            "symbol": ex_symbol
        }, auth=False)
        
        if not data or "list" not in data or len(data["list"]) == 0:
            raise NonRetryableError(f"No symbol info for {symbol}")
        
        info = data["list"][0]
        lot_size = info.get("lotSizeFilter", {})
        price_filter = info.get("priceFilter", {})
        leverage_filter = info.get("leverageFilter", {})
        
        return SymbolInfo(
            symbol=symbol,
            price_tick_size=float(price_filter.get("tickSize", 0.5)),
            qty_step_size=float(lot_size.get("qtyStep", 0.001)),
            min_qty=float(lot_size.get("minOrderQty", 0.001)),
            min_notional=float(lot_size.get("minNotionalValue", 5)) if lot_size.get("minNotionalValue") else None,
            max_leverage=float(leverage_filter.get("maxLeverage", 50)) if leverage_filter.get("maxLeverage") else None,
            margin_tiers=None,  # Bybit 需要单独接口获取
            raw=info
        )
    
    # ----------------- 账户 / 持仓 -----------------
    
    def get_balances(self) -> List[Balance]:
        """获取账户余额"""
        data = self._request("GET", "/v5/account/wallet-balance", params={
            "accountType": "UNIFIED"  # 统一账户
        }, auth=True)
        
        out: List[Balance] = []
        
        for account in data.get("list", []):
            for coin in account.get("coin", []):
                out.append(Balance(
                    asset=coin.get("coin", "USDT"),
                    balance=float(coin.get("walletBalance", 0)),
                    available=float(coin.get("availableToWithdraw", 0)),
                    margin=float(coin.get("totalPositionIM", 0)) if coin.get("totalPositionIM") else None,
                    raw=coin
                ))
        
        return out
    
    def get_positions(self, symbol: Optional[str] = None) -> List[Position]:
        """获取持仓"""
        params: Dict[str, Any] = {"category": "linear", "settleCoin": "USDT"}
        
        if symbol:
            params["symbol"] = self._map_symbol_out(symbol)
        
        data = self._request("GET", "/v5/position/list", params=params, auth=True)
        
        out: List[Position] = []
        
        for p in data.get("list", []):
            size = float(p.get("size", 0))
            if size == 0:  # 跳过无持仓
                continue
            
            side_map = {"Buy": "long", "Sell": "short"}
            
            out.append(Position(
                symbol=self._map_symbol_in(p.get("symbol", "")),
                side=side_map.get(p.get("side", ""), None),
                size=size,
                entry_price=float(p.get("avgPrice", 0)),
                leverage=float(p.get("leverage", 1)),
                unrealized_pnl=float(p.get("unrealisedPnl", 0)),
                liquidation_px=float(p.get("liqPrice", 0)) if p.get("liqPrice") and float(p.get("liqPrice", 0)) > 0 else None,
                margin_mode=p.get("tradeMode", "").lower(),  # 0=cross, 1=isolated
                raw=p
            ))
        
        return out
    
    def set_leverage(self, symbol: str, x: float) -> bool:
        """设置杠杆"""
        ex_symbol = self._map_symbol_out(symbol)
        
        self._request("POST", "/v5/position/set-leverage", params={
            "category": "linear",
            "symbol": ex_symbol,
            "buyLeverage": str(x),
            "sellLeverage": str(x)
        }, auth=True)
        
        return True
    
    def set_margin_mode(self, symbol: str, mode: Literal["isolated", "cross"]) -> bool:
        """设置保证金模式"""
        ex_symbol = self._map_symbol_out(symbol)
        
        # Bybit: 0=cross, 1=isolated
        trade_mode = 1 if mode == "isolated" else 0
        
        self._request("POST", "/v5/position/switch-isolated", params={
            "category": "linear",
            "symbol": ex_symbol,
            "tradeMode": trade_mode,
            "buyLeverage": "10",  # Bybit 需要同时设置杠杆
            "sellLeverage": "10"
        }, auth=True)
        
        return True
    
    # ----------------- 订单：下单 / 撤单 / 查询 -----------------
    
    def place_order(self, p: OrderParams) -> OrderAck:
        """下单"""
        # 参数校验
        info = self.get_symbol_info(p.symbol)
        self.validate_order(p, info)
        
        ex_symbol = self._map_symbol_out(p.symbol)
        
        # 构建订单参数
        payload: Dict[str, Any] = {
            "category": "linear",
            "symbol": ex_symbol,
            "side": "Buy" if p.side == "buy" else "Sell",
            "orderType": "Market" if p.order_type == "market" else "Limit",
            "qty": str(p.qty),
            "timeInForce": p.tif.upper(),  # GTC/IOC/FOK
        }
        
        if p.order_type == "limit":
            if p.price is None:
                return OrderAck(ok=False, error="price required for limit order")
            payload["price"] = str(p.price)
        
        if p.post_only:
            payload["timeInForce"] = "PostOnly"
        
        if p.reduce_only:
            payload["reduceOnly"] = True
        
        if p.client_order_id:
            payload["orderLinkId"] = p.client_order_id
        
        # 发送请求
        try:
            data = self._request("POST", "/v5/order/create", params=payload, auth=True)
            
            return OrderAck(
                ok=True,
                order_id=data.get("orderId"),
                client_order_id=data.get("orderLinkId", p.client_order_id),
                status="new",
                filled_qty=0.0,
                avg_fill_price=None,
                raw=data
            )
        except Exception as e:
            return OrderAck(ok=False, error=str(e), raw=None)
    
    def amend_order(
        self,
        symbol: str,
        order_id: str,
        price: Optional[float] = None,
        qty: Optional[float] = None
    ) -> OrderAck:
        """改单"""
        if not price and not qty:
            return OrderAck(ok=False, error="nothing to amend")
        
        ex_symbol = self._map_symbol_out(symbol)
        
        payload: Dict[str, Any] = {
            "category": "linear",
            "symbol": ex_symbol,
            "orderId": order_id,
        }
        
        if price:
            payload["price"] = str(price)
        if qty:
            payload["qty"] = str(qty)
        
        try:
            data = self._request("POST", "/v5/order/amend", params=payload, auth=True)
            return OrderAck(ok=True, order_id=data.get("orderId"), status="amended", raw=data)
        except Exception as e:
            return OrderAck(ok=False, error=str(e), order_id=order_id)
    
    def cancel_order(
        self,
        symbol: str,
        order_id: Optional[str] = None,
        client_order_id: Optional[str] = None
    ) -> OrderAck:
        """撤单"""
        if not order_id and not client_order_id:
            return OrderAck(ok=False, error="order_id or client_order_id required")
        
        ex_symbol = self._map_symbol_out(symbol)
        
        payload: Dict[str, Any] = {
            "category": "linear",
            "symbol": ex_symbol,
        }
        
        if order_id:
            payload["orderId"] = order_id
        if client_order_id:
            payload["orderLinkId"] = client_order_id
        
        try:
            data = self._request("POST", "/v5/order/cancel", params=payload, auth=True)
            return OrderAck(
                ok=True,
                order_id=data.get("orderId", order_id),
                client_order_id=data.get("orderLinkId", client_order_id),
                status="canceled",
                raw=data
            )
        except Exception as e:
            return OrderAck(ok=False, error=str(e))
    
    def get_order(
        self,
        symbol: str,
        order_id: Optional[str] = None,
        client_order_id: Optional[str] = None
    ) -> OrderAck:
        """查询订单"""
        if not order_id and not client_order_id:
            return OrderAck(ok=False, error="order_id or client_order_id required")
        
        ex_symbol = self._map_symbol_out(symbol)
        
        params: Dict[str, Any] = {
            "category": "linear",
            "symbol": ex_symbol,
        }
        
        if order_id:
            params["orderId"] = order_id
        if client_order_id:
            params["orderLinkId"] = client_order_id
        
        try:
            data = self._request("GET", "/v5/order/realtime", params=params, auth=True)
            
            if not data or "list" not in data or len(data["list"]) == 0:
                return OrderAck(ok=False, error="Order not found")
            
            order = data["list"][0]
            
            # Bybit 订单状态映射
            status_map = {
                "New": "new",
                "PartiallyFilled": "partial",
                "Filled": "filled",
                "Cancelled": "canceled",
                "Rejected": "rejected",
            }
            
            return OrderAck(
                ok=True,
                order_id=order.get("orderId"),
                client_order_id=order.get("orderLinkId"),
                status=status_map.get(order.get("orderStatus", ""), "unknown"),
                filled_qty=float(order.get("cumExecQty", 0)),
                avg_fill_price=float(order.get("avgPrice", 0)) if order.get("avgPrice") and float(order.get("avgPrice", 0)) > 0 else None,
                raw=order
            )
        except Exception as e:
            return OrderAck(ok=False, error=str(e))
    
    def get_open_orders(self, symbol: Optional[str] = None) -> List[OrderAck]:
        """获取未成交订单"""
        params: Dict[str, Any] = {
            "category": "linear",
            "settleCoin": "USDT"
        }
        
        if symbol:
            params["symbol"] = self._map_symbol_out(symbol)
        
        try:
            data = self._request("GET", "/v5/order/realtime", params=params, auth=True)
            
            out: List[OrderAck] = []
            
            status_map = {
                "New": "new",
                "PartiallyFilled": "partial",
                "Filled": "filled",
                "Cancelled": "canceled",
                "Rejected": "rejected",
            }
            
            for order in data.get("list", []):
                out.append(OrderAck(
                    ok=True,
                    order_id=order.get("orderId"),
                    client_order_id=order.get("orderLinkId"),
                    status=status_map.get(order.get("orderStatus", ""), "unknown"),
                    filled_qty=float(order.get("cumExecQty", 0)),
                    avg_fill_price=float(order.get("avgPrice", 0)) if order.get("avgPrice") and float(order.get("avgPrice", 0)) > 0 else None,
                    raw=order
                ))
            
            return out
        except Exception as e:
            self._log.error(f"get_open_orders error: {e}")
            return []
    
    def get_fills(self, symbol: str, since: Optional[int] = None) -> List[Fill]:
        """获取成交历史"""
        ex_symbol = self._map_symbol_out(symbol)
        
        params: Dict[str, Any] = {
            "category": "linear",
            "symbol": ex_symbol,
        }
        
        if since:
            params["startTime"] = since
        
        try:
            data = self._request("GET", "/v5/execution/list", params=params, auth=True)
            
            out: List[Fill] = []
            
            for trade in data.get("list", []):
                out.append(Fill(
                    symbol=self._map_symbol_in(trade.get("symbol", "")),
                    order_id=trade.get("orderId"),
                    trade_id=trade.get("execId"),
                    side=trade.get("side", "").lower(),
                    price=float(trade.get("execPrice", 0)),
                    qty=float(trade.get("execQty", 0)),
                    fee=float(trade.get("execFee", 0)) if trade.get("execFee") else None,
                    liquidity="maker" if trade.get("isMaker") else "taker",
                    ts=int(trade.get("execTime", self._ts_ms())),
                    raw=trade
                ))
            
            return out
        except Exception as e:
            self._log.error(f"get_fills error: {e}")
            return []
    
    # ----------------- WebSocket（占位） -----------------
    
    def ws_sub_public(
        self,
        symbols: List[str],
        on_ticker: Optional[Callable[[Ticker], None]] = None,
        on_orderbook: Optional[Callable[[Orderbook], None]] = None,
    ) -> None:
        """订阅公共 WebSocket"""
        # TODO: 实现 Bybit WebSocket 公共频道订阅
        # wss://stream.bybit.com/v5/public/linear
        raise NotImplementedError("WS public not implemented yet")
    
    def ws_sub_private(
        self,
        on_order: Optional[Callable[[OrderAck], None]] = None,
        on_fill: Optional[Callable[[Fill], None]] = None,
        on_position: Optional[Callable[[Position], None]] = None,
        on_balance: Optional[Callable[[Balance], None]] = None,
    ) -> None:
        """订阅私有 WebSocket"""
        # TODO: 实现 Bybit WebSocket 私有频道订阅
        # wss://stream.bybit.com/v5/private
        raise NotImplementedError("WS private not implemented yet")
    
    # ----------------- 可选扩展 -----------------
    
    def place_orders_bulk(self, orders: List[OrderParams]) -> List[OrderAck]:
        """批量下单"""
        # Bybit V5 支持批量下单，最多 10 个
        if len(orders) > 10:
            # 分批处理
            results = []
            for i in range(0, len(orders), 10):
                batch = orders[i:i+10]
                results.extend(self.place_orders_bulk(batch))
            return results
        
        # 构建批量订单
        request_list = []
        for p in orders:
            info = self.get_symbol_info(p.symbol)
            self.validate_order(p, info)
            
            ex_symbol = self._map_symbol_out(p.symbol)
            
            order_params: Dict[str, Any] = {
                "symbol": ex_symbol,
                "side": "Buy" if p.side == "buy" else "Sell",
                "orderType": "Market" if p.order_type == "market" else "Limit",
                "qty": str(p.qty),
                "timeInForce": p.tif.upper(),
            }
            
            if p.order_type == "limit" and p.price:
                order_params["price"] = str(p.price)
            
            if p.post_only:
                order_params["timeInForce"] = "PostOnly"
            
            if p.reduce_only:
                order_params["reduceOnly"] = True
            
            if p.client_order_id:
                order_params["orderLinkId"] = p.client_order_id
            
            request_list.append(order_params)
        
        try:
            data = self._request("POST", "/v5/order/create-batch", params={
                "category": "linear",
                "request": request_list
            }, auth=True)
            
            results = []
            for item in data.get("result", {}).get("list", []):
                results.append(OrderAck(
                    ok=item.get("retCode") == 0,
                    order_id=item.get("orderId"),
                    client_order_id=item.get("orderLinkId"),
                    status="new" if item.get("retCode") == 0 else "rejected",
                    error=item.get("retMsg") if item.get("retCode") != 0 else None,
                    raw=item
                ))
            
            return results
        except Exception as e:
            self._log.error(f"place_orders_bulk error: {e}")
            return [OrderAck(ok=False, error=str(e)) for _ in orders]
    
    def cancel_all(self, symbol: Optional[str] = None) -> bool:
        """撤销所有订单"""
        try:
            params: Dict[str, Any] = {
                "category": "linear",
            }
            
            if symbol:
                params["symbol"] = self._map_symbol_out(symbol)
            else:
                params["settleCoin"] = "USDT"
            
            self._request("POST", "/v5/order/cancel-all", params=params, auth=True)
            return True
        except Exception as e:
            self._log.error(f"cancel_all error: {e}")
            return False
    
    def close(self) -> None:
        """关闭连接"""
        try:
            self._http.close()
        except Exception:
            pass


# ================== 使用示例 ==================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    pass