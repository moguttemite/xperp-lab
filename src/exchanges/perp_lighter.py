# -*- coding: utf-8 -*-
"""
perp_lighter.py — Lighter 永续合约适配器（实现 BaseExchange 抽象接口）

用法：
    ex = LighterExchange(api_key="...", api_secret="...")
    ack = ex.place_order(OrderParams(symbol="BTCUSDT", side="buy", qty=0.001, order_type="market"))

说明：
- 这是“接口对齐版”：方法签名/返回值结构与 base_exchange.py 严格一致。
- 具体 API 路径 / 字段名需结合 Lighter 实际文档替换（TODO 标注）。
- 可选择“官方 SDK”或“纯 REST”两种落地方式；本文件默认给出 REST 占位。
"""

from __future__ import annotations

import configparser
import time
import hmac
import hashlib
import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Literal

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

# —— 统一接口与类型：来自 base_exchange.py ——
from base_exchange import (           # 如果你把它们放到包里，可改为 from .base_exchange import ...
    BaseExchange,
    OrderParams, OrderAck, Position, Balance, FundingInfo, Ticker, Orderbook, Fill, SymbolInfo,
    ExchangeError, RetryableError, NonRetryableError,
)

# ============ 自定义实现函数 ===============

SYMBOL_ALIASES: Dict[str, str] = {
        "BTCUSDT": "BTC-PERP",
        "ETHUSDT": "ETH-PERP"
    }

# 反向映射用于把交易所返回的符号翻回工程标准符号
REVERSE_ALIASES: dict[str, str] = {v: k for k, v in SYMBOL_ALIASES.items()}



# ================== 配置 ==================

@dataclass
class LighterConfig:
    ## 传入参数
    api_key_private: str = None
    l1_address: str = None
    
    ## 自动获取参数/官方参数
    account_index: str = None
    mainnet_url: str = "https://mainnet.zklighter.elliot.ai/api/v1/"
    testnet_url: str = None
    
    ## 配置参数
    timeout_sec: int = 6
    recv_window_ms: int = 5000
    user_agent: str = "lighter-exchange-adapter/0.1"
    max_retries: int = 2
    retry_backoff_sec: float = 0.25

    def __post_init__(self):
        """在实例创建后自动调用"""
        # 现在 self 存在了，可以调用实例方法
        self.account_index = self.accountsByL1Address(self.l1_address)

    def accountsByL1Address(self, l1_address: str) -> Dict[str, Any]:
        url = self.mainnet_url + "accountsByL1Address" + "?l1_address=" + l1_address
        headers = {"accept": "application/json"}
        response = requests.get(url, headers=headers).json()

        if response:
            index = response.get("sub_accounts", [])[0].get("index")
            return index
        else:
            return None

# ================== 实现类 ==================

class LighterExchange(BaseExchange):
    def __init__(self, api_key: str, api_secret: str, *, base_url: Optional[str] = None, session: Optional["requests.Session"] = None) -> None:
        if requests is None:
            raise RuntimeError("requests not installed")
        self.cfg = LighterConfig(api_key=api_key, api_secret=api_secret, base_url=base_url or LighterConfig.base_url)
        self._http = session or requests.Session()
        self._log = logging.getLogger(self.__class__.__name__)
        self._lock = threading.Lock()
        self._clock_skew_ms = 0

        # 可选：SDK 客户端
        self._sdk = None
        # try:
        #     from lighter_python import Client as LighterSDK
        #     self._sdk = LighterSDK(api_key=api_key, api_secret=api_secret, base_url=self.cfg.base_url)
        # except Exception:
        #     self._sdk = None

        # 交易所↔内部 符号映射（如需）
        self._symbol_map: Dict[str, str] = {}  # 例如 {"BTCUSDT": "BTC-PERP"}，没差异可不填

    # ----------------- 公共工具 -----------------

    def _ts_ms(self) -> int:
        return int(time.time() * 1000) + self._clock_skew_ms

    def _map_symbol_out(self, symbol: str) -> str:
        """内部标准符号 -> 交易所符号"""
        return self._symbol_map.get(symbol, symbol)

    def _map_symbol_in(self, symbol: str) -> str:
        """交易所符号 -> 内部标准符号"""
        inv = {v: k for k, v in self._symbol_map.items()}
        return inv.get(symbol, symbol)

    def _sign(self, payload: str) -> str:
        """HMAC-SHA256（占位；按 Lighter 文档调整签名串格式）"""
        return hmac.new(self.cfg.api_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

    def _headers(self, payload: str, auth: bool) -> Dict[str, str]:
        if not auth:
            return {"User-Agent": self.cfg.user_agent}
        return {
            "X-API-KEY": self.cfg.api_key,
            "X-API-SIGN": self._sign(payload),
            "X-API-TS": str(self._ts_ms()),
            "User-Agent": self.cfg.user_agent,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None,
                 json: Optional[Dict[str, Any]] = None, auth: bool = True) -> Any:
        """基础请求 + 简单重试。把错误转换成自家异常类型。"""
        url = self.cfg.base_url.rstrip("/") + "/" + path.lstrip("/")
        payload_for_sig = str(json or params or "")
        headers = self._headers(payload_for_sig, auth=auth)

        for attempt in range(self.cfg.max_retries + 1):
            try:
                r = self._http.request(
                    method=method.upper(), url=url, params=params, json=json,
                    headers=headers, timeout=self.cfg.timeout_sec
                )
                if r.status_code >= 500:
                    raise RetryableError(f"HTTP {r.status_code}: {r.text}")
                if r.status_code >= 400:
                    # 细分错误码可在此做
                    raise NonRetryableError(f"HTTP {r.status_code}: {r.text}")
                return r.json()
            except RetryableError as e:
                if attempt >= self.cfg.max_retries:
                    raise
                self._log.warning("retryable http error: %s (attempt %s/%s)", e, attempt+1, self.cfg.max_retries)
                time.sleep(self.cfg.retry_backoff_sec)
            except NonRetryableError:
                raise
            except Exception as e:
                # 网络等异常视为可重试
                if attempt >= self.cfg.max_retries:
                    raise RetryableError(str(e))
                self._log.warning("network error: %s (attempt %s/%s)", e, attempt+1, self.cfg.max_retries)
                time.sleep(self.cfg.retry_backoff_sec)

    # ----------------- 行情 / 资金费 -----------------

    def get_ticker(self, symbol: str) -> Ticker:
        market_id = SYMBOL_ALIASES.get(symbol, symbol)
        


        return Ticker(
            symbol=symbol,
            last=last,
            mark=mark,
            index=index,
            ts=last_ts if last_ts is not None else self._ts_ms(),
            raw=raw_pack,
        )

    def get_orderbook(self, symbol: str, depth: int = 50) -> Orderbook:
        ex_symbol = self._map_symbol_out(symbol)
        if self._sdk:
            data = {}
        else:
            # TODO: 替换为实际深度接口 & 增量/完整
            # data = self._request("GET", "/v1/market/depth", params={"symbol": ex_symbol, "limit": depth}, auth=False)
            data = {}
        bids = data.get("bids", []) if isinstance(data, dict) else []
        asks = data.get("asks", []) if isinstance(data, dict) else []
        return Orderbook(symbol=symbol, bids=bids, asks=asks, ts=self._ts_ms(), raw=data)

    def get_funding_info(self, symbol: str) -> FundingInfo:
        ex_symbol = self._map_symbol_out(symbol)
        if self._sdk:
            data = {}
        else:
            # TODO: 替换为资金费接口
            # data = self._request("GET", "/v1/funding", params={"symbol": ex_symbol}, auth=False)
            data = {}
        rate = float(data.get("fundingRate", "nan")) if data else float("nan")
        nxt  = int(data.get("nextFundingTs")) if data and data.get("nextFundingTs") is not None else None
        return FundingInfo(symbol=symbol, funding_rate=rate, next_funding_ts=nxt, raw=data)

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        ex_symbol = self._map_symbol_out(symbol)
        if self._sdk:
            data = {}
        else:
            # TODO: 替换为合约规格接口
            # data = self._request("GET", "/v1/instruments", params={"symbol": ex_symbol}, auth=False)
            data = {}
        return SymbolInfo(
            symbol=symbol,
            price_tick_size=float(data.get("priceTickSize", 0.5)),  # 默认给个合理 tick，接入后替换
            qty_step_size=float(data.get("qtyStepSize", 0.001)),
            min_qty=float(data.get("minQty", 0.001)) if data.get("minQty") is not None else None,
            min_notional=float(data.get("minNotional", 5)) if data.get("minNotional") is not None else None,
            max_leverage=float(data.get("maxLeverage", 50)) if data.get("maxLeverage") is not None else None,
            margin_tiers=data.get("marginTiers"),
            raw=data,
        )

    # ----------------- 账户 / 持仓 -----------------

    def get_balances(self) -> List[Balance]:
        if self._sdk:
            data = {}
        else:
            # TODO: 替换为账户余额接口
            # data = self._request("GET", "/v1/account/balances", auth=True)
            data = {"balances":[{"asset":"USDT","balance":None,"available":None}]}
        out: List[Balance] = []
        for b in data.get("balances", []):
            out.append(Balance(
                asset=b.get("asset", "USDT"),
                balance=float(b.get("balance")) if b.get("balance") is not None else float("nan"),
                available=float(b.get("available")) if b.get("available") is not None else float("nan"),
                margin=None,
                raw=b
            ))
        return out

    def get_positions(self, symbol: Optional[str] = None) -> List[Position]:
        if self._sdk:
            data = {}
        else:
            # TODO: 替换为持仓接口
            # params = {"symbol": self._map_symbol_out(symbol)} if symbol else None
            # data = self._request("GET", "/v1/positions", params=params, auth=True)
            data = {"positions": []}
        out: List[Position] = []
        for p in data.get("positions", []):
            sym_std = self._map_symbol_in(p.get("symbol", ""))
            if symbol and sym_std != symbol:
                continue
            out.append(Position(
                symbol=sym_std,
                side=p.get("side"),
                size=float(p.get("size", 0.0)),
                entry_price=float(p.get("entryPrice", 0.0)),
                leverage=float(p.get("leverage")) if p.get("leverage") is not None else None,
                unrealized_pnl=float(p.get("uPnL")) if p.get("uPnL") is not None else None,
                liquidation_px=float(p.get("liqPx")) if p.get("liqPx") is not None else None,
                margin_mode=p.get("marginMode"),
                raw=p
            ))
        return out

    def set_leverage(self, symbol: str, x: float) -> bool:
        ex_symbol = self._map_symbol_out(symbol)
        if self._sdk:
            # TODO: SDK 调整杠杆
            return True
        # TODO: 替换为杠杆接口
        # _ = self._request("POST", "/v1/position/leverage", json={"symbol": ex_symbol, "leverage": x}, auth=True)
        return True

    def set_margin_mode(self, symbol: str, mode: Literal["isolated", "cross"]) -> bool:
        ex_symbol = self._map_symbol_out(symbol)
        if self._sdk:
            return True
        # TODO: 替换为保证金模式接口
        # _ = self._request("POST", "/v1/position/margin-mode", json={"symbol": ex_symbol, "mode": mode}, auth=True)
        return True

    # ----------------- 订单：下单 / 撤单 / 查询 -----------------

    def place_order(self, p: OrderParams) -> OrderAck:
        info = self.get_symbol_info(p.symbol)
        self.validate_order(p, info)

        ex_symbol = self._map_symbol_out(p.symbol)
        payload: Dict[str, Any] = {
            "symbol": ex_symbol,
            "side": p.side.upper(),                  # BUY/SELL
            "type": p.order_type.upper(),           # LIMIT/MARKET
            "qty": p.qty,
            "timeInForce": p.tif.upper(),           # GTC/IOC/FOK
            "postOnly": p.post_only,
            "reduceOnly": p.reduce_only,
        }
        if p.order_type == "limit":
            if p.price is None:
                return OrderAck(ok=False, error="price required for limit order")
            payload["price"] = p.price
        if p.client_order_id:
            payload["clientOrderId"] = p.client_order_id

        if self._sdk:
            raw = {"status":"NEW"}  # TODO: SDK 下单 + 回执
        else:
            # TODO: 替换为下单接口
            # raw = self._request("POST", "/v1/orders", json=payload, auth=True)
            raw = {"status":"NEW", "orderId": None, "filledQty": 0, "avgFillPrice": None}

        return OrderAck(
            ok=True,
            order_id=raw.get("orderId"),
            client_order_id=p.client_order_id,
            status=str(raw.get("status","new")).lower(),
            filled_qty=float(raw.get("filledQty", 0) or 0),
            avg_fill_price=(float(raw.get("avgFillPrice")) if raw.get("avgFillPrice") is not None else None),
            raw=raw,
        )

    def amend_order(self, symbol: str, order_id: str, price: Optional[float] = None, qty: Optional[float] = None) -> OrderAck:
        ex_symbol = self._map_symbol_out(symbol)
        if not price and not qty:
            return OrderAck(ok=False, error="nothing to amend")
        if self._sdk:
            raw = {"status":"NEW"}  # TODO
        else:
            # TODO: 若交易所不支持改单，则在上层以“撤-下”模拟；这里给出直连接口占位
            # raw = self._request("POST", "/v1/orders/amend", json={"symbol": ex_symbol, "orderId": order_id, "price": price, "qty": qty}, auth=True)
            raw = {"status":"NEW"}
        return OrderAck(ok=True, order_id=order_id, status=str(raw.get("status","new")).lower(), raw=raw)

    def cancel_order(self, symbol: str, order_id: Optional[str] = None, client_order_id: Optional[str] = None) -> OrderAck:
        if not order_id and not client_order_id:
            return OrderAck(ok=False, error="order_id or client_order_id required")
        ex_symbol = self._map_symbol_out(symbol)
        if self._sdk:
            raw = {"status":"CANCELED"}  # TODO
        else:
            # raw = self._request("DELETE", "/v1/orders", json={"symbol": ex_symbol, "orderId": order_id, "clientOrderId": client_order_id}, auth=True)
            raw = {"status":"CANCELED"}
        return OrderAck(ok=True, order_id=order_id, client_order_id=client_order_id, status="canceled", raw=raw)

    def get_order(self, symbol: str, order_id: Optional[str] = None, client_order_id: Optional[str] = None) -> OrderAck:
        if not order_id and not client_order_id:
            return OrderAck(ok=False, error="order_id or client_order_id required")
        ex_symbol = self._map_symbol_out(symbol)
        if self._sdk:
            raw = {"status":"NEW"}  # TODO
        else:
            # raw = self._request("GET", "/v1/orders", params={"symbol": ex_symbol, "orderId": order_id, "clientOrderId": client_order_id}, auth=True)
            raw = {"status":"NEW", "orderId": order_id, "filledQty": 0, "avgFillPrice": None}
        return OrderAck(
            ok=True,
            order_id=raw.get("orderId", order_id),
            client_order_id=client_order_id,
            status=str(raw.get("status","new")).lower(),
            filled_qty=float(raw.get("filledQty", 0) or 0),
            avg_fill_price=(float(raw.get("avgFillPrice")) if raw.get("avgFillPrice") is not None else None),
            raw=raw,
        )

    def get_open_orders(self, symbol: Optional[str] = None) -> List[OrderAck]:
        if self._sdk:
            rows = []  # TODO
        else:
            # params = {"symbol": self._map_symbol_out(symbol)} if symbol else None
            # rows = self._request("GET", "/v1/openOrders", params=params, auth=True)
            rows = []
        out: List[OrderAck] = []
        for o in rows:
            out.append(OrderAck(
                ok=True,
                order_id=o.get("orderId"),
                client_order_id=o.get("clientOrderId"),
                status=str(o.get("status","new")).lower(),
                filled_qty=float(o.get("filledQty", 0) or 0),
                avg_fill_price=(float(o.get("avgFillPrice")) if o.get("avgFillPrice") is not None else None),
                raw=o,
            ))
        return out

    def get_fills(self, symbol: str, since: Optional[int] = None) -> List[Fill]:
        ex_symbol = self._map_symbol_out(symbol)
        if self._sdk:
            rows = []  # TODO
        else:
            # params = {"symbol": ex_symbol}
            # if since is not None: params["since"] = since
            # rows = self._request("GET", "/v1/myTrades", params=params, auth=True)
            rows = []
        out: List[Fill] = []
        for r in rows:
            out.append(Fill(
                symbol=symbol,
                order_id=r.get("orderId"),
                trade_id=r.get("tradeId"),
                side=str(r.get("side","buy")).lower(),     # buy/sell
                price=float(r.get("price", 0.0)),
                qty=float(r.get("qty", 0.0)),
                fee=float(r.get("fee")) if r.get("fee") is not None else None,
                liquidity=r.get("liquidity"),              # maker/taker
                ts=int(r.get("ts", self._ts_ms())),
                raw=r
            ))
        return out

    # ----------------- WebSocket（占位） -----------------

    def ws_sub_public(
        self,
        symbols: List[str],
        on_ticker: Optional[Callable[[Ticker], None]] = None,
        on_orderbook: Optional[Callable[[Orderbook], None]] = None,
    ) -> None:
        # TODO: 按 Lighter 的 WS 文档实现，包含自动重连与心跳
        raise NotImplementedError("WS public not implemented")

    def ws_sub_private(
        self,
        on_order: Optional[Callable[[OrderAck], None]] = None,
        on_fill: Optional[Callable[[Fill], None]] = None,
        on_position: Optional[Callable[[Position], None]] = None,
        on_balance: Optional[Callable[[Balance], None]] = None,
    ) -> None:
        # TODO: 私有 WS 登录、签名、推送回调、断线补偿（重连后拉增量）
        raise NotImplementedError("WS private not implemented")

    # ----------------- 可选扩展 -----------------

    def place_orders_bulk(self, orders: List[OrderParams]) -> List[OrderAck]:
        # TODO: 如果 Lighter 提供批量下单接口，按其实现；否则循环 + 并发
        return [self.place_order(p) for p in orders]

    def cancel_all(self, symbol: Optional[str] = None) -> bool:
        # TODO: 如果有撤所有接口就直连，否则遍历 openOrders 撤单
        # for o in self.get_open_orders(symbol):
        #     self.cancel_order(symbol or o["raw"]["symbol"], order_id=o.get("order_id"))
        return True

    def close(self) -> None:
        try:
            self._http.close()
        except Exception:
            pass

# ================== 自检（可删） ==================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    configparser = LighterConfig(api_key_private="", l1_address="0x05f6ae0D35234986b9821E479fEDcd81873ec850")
    print(configparser)
