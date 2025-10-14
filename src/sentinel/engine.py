"""
哨兵编排引擎
Sentinel Orchestration Engine

聚合各检测器结果，计算 RegimeScore，产出告警和动作建议
"""

from __future__ import annotations
from typing import Dict, List, Optional
from time import time
import logging

from .detectors import (
    VolSpikeDetector, VolSpikeCfg,
    SpreadBlowoutDetector, SpreadBlowoutCfg,
    FundingShockDetector, FundingShockCfg,
    OrderbookImbalanceDetector, OrderbookImbalanceCfg,
    WhaleOnchainDetector, WhaleOnchainCfg,
    NewsDetector, NewsCfg
)
from ..types import RegimeResult, Alert


class SentinelEngine:
    """哨兵编排引擎：聚合各检测器，计算市场状态分数"""
    
    def __init__(self, cfg: dict):
        """
        初始化哨兵引擎
        
        Args:
            cfg: 配置字典，包含检测器配置、权重、阈值等
        """
        self.cfg = cfg
        self.logger = logging.getLogger(__name__)
        
        # 初始化各检测器
        self._init_detectors()
        
        # 配置参数
        self.weights = cfg.get("weights", {})
        self.cooldown_sec = int(cfg.get("cooldown_sec", 300))
        self.thresholds = cfg.get("score_thresholds", {"tighten": 60, "pause": 80})
        self.outputs = cfg.get("outputs", {"log": True, "webhook": ""})
        
        # 冷却机制
        self._last_fire_ts = 0
        
        self.logger.info(f"SentinelEngine initialized with thresholds: {self.thresholds}")

    def _init_detectors(self):
        """初始化各检测器"""
        d = self.cfg.get("detectors", {})
        
        # 价格/成交量异常检测器
        vol_cfg = VolSpikeCfg(**d.get("vol_spike", {}))
        self.vol_detector = VolSpikeDetector(vol_cfg)
        
        # 价差异常检测器
        spread_cfg = SpreadBlowoutCfg(**d.get("spread_blowout", {}))
        self.spread_detector = SpreadBlowoutDetector(spread_cfg)
        
        # 资金费突变检测器
        funding_cfg = FundingShockCfg(**d.get("funding_shock", {}))
        self.funding_detector = FundingShockDetector(funding_cfg)
        
        # 盘口不均衡检测器（占位）
        ob_cfg = OrderbookImbalanceCfg(**d.get("ob_imbalance", {}))
        self.ob_detector = OrderbookImbalanceDetector(ob_cfg)
        
        # 链上鲸鱼检测器（占位）
        whale_cfg = WhaleOnchainCfg(**d.get("whale_onchain", {}))
        self.whale_detector = WhaleOnchainDetector(whale_cfg)
        
        # 新闻事件检测器（占位）
        news_cfg = NewsCfg(**d.get("news", {}))
        self.news_detector = NewsDetector(news_cfg)

    def update(self, px_a: float, px_b: float, vol_a: float, next_rate_a: float, 
               bids: Optional[list] = None, asks: Optional[list] = None,
               tx_data: Optional[dict] = None, news_data: Optional[dict] = None) -> RegimeResult:
        """
        更新市场数据并计算市场状态
        
        Args:
            px_a: 交易所A价格
            px_b: 交易所B价格
            vol_a: 交易所A成交量
            next_rate_a: 交易所A下一期资金费
            bids: 买盘数据（可选）
            asks: 卖盘数据（可选）
            tx_data: 链上交易数据（可选）
            news_data: 新闻数据（可选）
            
        Returns:
            RegimeResult: 包含分数、等级和告警列表的结果
        """
        alerts: List[Alert] = []
        
        # 运行各检测器
        detector_results = [
            self.vol_detector.update(px_a, vol_a),
            self.spread_detector.update(px_a, px_b),
            self.funding_detector.update(next_rate_a),
        ]
        
        # 可选检测器
        if bids is not None and asks is not None:
            detector_results.append(self.ob_detector.update(bids, asks))
        
        if tx_data is not None:
            detector_results.append(self.whale_detector.update(tx_data))
            
        if news_data is not None:
            detector_results.append(self.news_detector.update(news_data))
        
        # 处理检测结果
        for alert in filter(None, detector_results):
            if alert is not None:
                # 应用权重
                w = float(self.weights.get(alert["name"], 1.0))
                alert["score"] = float(alert["score"]) * w
                alerts.append(alert)  # type: ignore
        
        # 计算总分数
        score = sum(a["score"] for a in alerts)
        
        # 确定市场状态等级
        level = "normal"
        if score >= self.thresholds.get("pause", 80):
            level = "pause"
        elif score >= self.thresholds.get("tighten", 60):
            level = "tighten"
        
        # 冷却机制：避免频繁触发高等级动作
        now = int(time())
        if level in ("tighten", "pause") and now - self._last_fire_ts < self.cooldown_sec:
            level = "normal"
            self.logger.debug(f"Action {level} suppressed due to cooldown")
        elif level in ("tighten", "pause"):
            self._last_fire_ts = now
            self.logger.warning(f"Market regime changed to {level} with score {score:.2f}")
        
        # 输出告警
        if alerts and self.outputs.get("log", True):
            self._log_alerts(alerts, score, level)
        
        if alerts and self.outputs.get("webhook"):
            self._send_webhook(alerts, score, level)
        
        return {
            "score": float(score),
            "level": level,
            "alerts": alerts
        }

    def _log_alerts(self, alerts: List[Alert], score: float, level: str):
        """记录告警日志"""
        self.logger.warning(f"Sentinel Alert - Score: {score:.2f}, Level: {level}")
        for alert in alerts:
            self.logger.warning(
                f"  {alert['name']} [{alert['severity']}] - Score: {alert['score']:.2f} - "
                f"Detail: {alert['detail']}"
            )

    def _send_webhook(self, alerts: List[Alert], score: float, level: str):
        """发送webhook告警（占位实现）"""
        webhook_url = self.outputs.get("webhook")
        if webhook_url:
            # 占位实现，后续接入Slack/Discord等
            self.logger.info(f"Would send webhook to {webhook_url}")

    def get_status(self) -> dict:
        """获取哨兵引擎状态"""
        return {
            "enabled": self.cfg.get("enabled", True),
            "cooldown_sec": self.cooldown_sec,
            "thresholds": self.thresholds,
            "weights": self.weights,
            "last_fire_ts": self._last_fire_ts,
            "outputs": self.outputs
        }

    def reset_cooldown(self):
        """重置冷却时间（用于测试或手动干预）"""
        self._last_fire_ts = 0
        self.logger.info("Sentinel cooldown reset")
