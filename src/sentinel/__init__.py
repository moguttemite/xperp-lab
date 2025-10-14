"""
Sentinel 实时市场状况分析模块
Real-time Market Condition Analysis Module

提供异常波动检测和市场状态评估功能，用于在极端市场环境下
（如战争、交易所被盗、早期BTC钱包活动等）及时发现异常并采取相应措施。

主要组件：
- detectors: 各类异常检测器（波动、价差、盘口、资金费、链上、新闻）
- engine: 哨兵编排引擎，聚合各检测器结果并计算市场状态分数
"""

from .engine import SentinelEngine
from .detectors import (
    VolSpikeDetector,
    SpreadBlowoutDetector, 
    FundingShockDetector,
    OrderbookImbalanceDetector,
    WhaleOnchainDetector,
    NewsDetector
)

__all__ = [
    "SentinelEngine",
    "VolSpikeDetector",
    "SpreadBlowoutDetector",
    "FundingShockDetector", 
    "OrderbookImbalanceDetector",
    "WhaleOnchainDetector",
    "NewsDetector"
]
