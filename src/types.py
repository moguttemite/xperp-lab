"""
数据类型定义模块
Type definitions module

定义策略中使用的 TypedDict、Enums、数据结构
包括订单类型、仓位信息、资金费信息等核心数据结构
"""

from typing import Literal, TypedDict, Dict, Any, List

# Sentinel 模块相关类型定义
Severity = Literal["info", "warn", "high", "critical"]

class Alert(TypedDict):
    name: str
    severity: Severity
    score: float
    ts: int
    detail: Dict[str, Any]

class RegimeResult(TypedDict):
    score: float
    level: Literal["normal", "tighten", "pause"]
    alerts: List[Alert]