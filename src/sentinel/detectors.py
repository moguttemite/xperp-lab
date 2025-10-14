"""
异常波动检测器模块
Anomaly Detection Module

实现各类市场异常检测器：
- 价格/成交量异常波动检测
- 跨所价差异常检测
- 资金费突变检测
- 盘口不均衡检测（占位）
- 链上鲸鱼活动检测（占位）
- 新闻事件检测（占位）
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, Any
import numpy as np
from collections import deque
from time import time


@dataclass
class VolSpikeCfg:
    """价格/成交量异常波动检测配置"""
    win: int = 120
    z: float = 4.0
    vol_z: float = 2.0


class VolSpikeDetector:
    """价格/成交量异常波动检测：|ret| 的 z 分数 + 成交量 z 分数同时超阈。"""
    
    def __init__(self, cfg: VolSpikeCfg):
        self.cfg = cfg
        self.prices = deque(maxlen=cfg.win)
        self.volumes = deque(maxlen=cfg.win)

    def update(self, px: float, vol: float) -> Optional[dict]:
        """更新价格和成交量数据，检测异常波动"""
        self.prices.append(px)
        self.volumes.append(vol)
        
        if len(self.prices) < self.cfg.win:
            return None
            
        # 计算价格收益率
        p = np.array(self.prices)
        r = np.diff(np.log(p))
        
        # 计算收益率z分数
        rz = (r[-1] - r.mean()) / (r.std(ddof=0) + 1e-12)
        
        # 计算成交量z分数
        v = np.array(self.volumes)
        vz = (v[-1] - v.mean()) / (v.std(ddof=0) + 1e-12)
        
        # 检测异常：收益率z分数和成交量z分数同时超阈值
        if abs(rz) >= self.cfg.z and vz >= self.cfg.vol_z:
            score = 70 + 10 * min(3, abs(rz) - self.cfg.z)
            return {
                "name": "vol_spike",
                "severity": "high",
                "score": score,
                "ts": int(time()),
                "detail": {
                    "rz": float(rz),
                    "vz": float(vz),
                    "price": float(px),
                    "volume": float(vol)
                }
            }
        return None


@dataclass
class SpreadBlowoutCfg:
    """跨所价差异常检测配置"""
    win: int = 60
    z: float = 3.5


class SpreadBlowoutDetector:
    """跨所价差异常：spread z-score 超阈。"""
    
    def __init__(self, cfg: SpreadBlowoutCfg):
        self.cfg = cfg
        self.spreads = deque(maxlen=cfg.win)

    def update(self, px_a: float, px_b: float) -> Optional[dict]:
        """更新两个交易所价格，检测价差异常"""
        mid = 0.5 * (px_a + px_b)
        sp = (px_a - px_b) / mid
        self.spreads.append(sp)
        
        if len(self.spreads) < self.cfg.win:
            return None
            
        # 计算价差z分数
        x = np.array(self.spreads)
        z = (x[-1] - x.mean()) / (x.std(ddof=0) + 1e-12)
        
        if abs(z) >= self.cfg.z:
            score = 65 + 8 * min(3, abs(z) - self.cfg.z)
            return {
                "name": "spread_blowout",
                "severity": "high",
                "score": score,
                "ts": int(time()),
                "detail": {
                    "z": float(z),
                    "spread": float(sp),
                    "price_a": float(px_a),
                    "price_b": float(px_b)
                }
            }
        return None


@dataclass
class FundingShockCfg:
    """资金费突变检测配置"""
    win: int = 24
    delta_bps: float = 3.0


class FundingShockDetector:
    """资金费突变：下一期资金费相对近 win 期中位数的变化超过阈值(bp)。"""
    
    def __init__(self, cfg: FundingShockCfg):
        self.cfg = cfg
        self.hist = deque(maxlen=cfg.win)

    def update(self, next_rate: float) -> Optional[dict]:
        """更新资金费数据，检测突变"""
        self.hist.append(next_rate)
        
        if len(self.hist) < self.cfg.win:
            return None
            
        # 计算历史中位数
        med = float(np.median(self.hist))
        
        # 计算变化幅度（基点）
        delta_bps = abs((next_rate - med) * 1e4)
        
        if delta_bps >= self.cfg.delta_bps:
            score = 50 + 5 * min(5, delta_bps - self.cfg.delta_bps)
            return {
                "name": "funding_shock",
                "severity": "warn",
                "score": score,
                "ts": int(time()),
                "detail": {
                    "median": med,
                    "next": next_rate,
                    "delta_bps": delta_bps
                }
            }
        return None


@dataclass
class OrderbookImbalanceCfg:
    """盘口不均衡检测配置"""
    depth: int = 10
    thresh: float = 0.65
    min_notional: float = 100000


class OrderbookImbalanceDetector:
    """盘口不均衡检测：买卖盘深度比例异常"""
    
    def __init__(self, cfg: OrderbookImbalanceCfg):
        self.cfg = cfg
        # 占位实现，后续接入真实订单簿数据
        pass

    def update(self, bids: list, asks: list) -> Optional[dict]:
        """更新订单簿数据，检测不均衡"""
        # 占位实现
        # 计算前N档买卖盘深度比例
        # 如果比例超过阈值，触发告警
        return None


@dataclass
class WhaleOnchainCfg:
    """链上鲸鱼活动检测配置"""
    min_btc: float = 1000
    cooldown_sec: int = 3600


class WhaleOnchainDetector:
    """链上鲸鱼活动检测：大额BTC转移"""
    
    def __init__(self, cfg: WhaleOnchainCfg):
        self.cfg = cfg
        self._last_alert_ts = 0
        # 占位实现，后续接入链上数据源

    def update(self, tx_data: dict) -> Optional[dict]:
        """更新链上交易数据，检测鲸鱼活动"""
        # 占位实现
        # 检查是否有>=min_btc的大额转移
        # 考虑冷却时间避免重复告警
        return None


@dataclass
class NewsCfg:
    """新闻事件检测配置"""
    keywords: list = None
    severity_map: dict = None

    def __post_init__(self):
        if self.keywords is None:
            self.keywords = ["war", "hack", "sanction", "exploit"]
        if self.severity_map is None:
            self.severity_map = {}


class NewsDetector:
    """新闻事件检测：关键词匹配和严重程度评估"""
    
    def __init__(self, cfg: NewsCfg):
        self.cfg = cfg
        # 占位实现，后续接入新闻数据源

    def update(self, news_data: dict) -> Optional[dict]:
        """更新新闻数据，检测相关事件"""
        # 占位实现
        # 关键词匹配
        # 严重程度评估
        return None
