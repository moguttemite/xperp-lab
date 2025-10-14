# -*- coding: utf-8 -*-
"""
配置文件 - 使用类管理配置
"""
import os
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

########################################################

def get_env(key: str, default: Optional[str] = None) -> str:
    """获取环境变量，如果不存在则使用默认值"""
    value = os.getenv(key, default)
    if value is None:
        raise ValueError(f"环境变量 {key} 未设置")
    return value


def get_bool(key: str, default: bool = False) -> bool:
    """获取布尔类型环境变量"""
    value = os.getenv(key, str(default)).lower()
    return value in ('true', '1', 'yes')


def get_int(key: str, default: int) -> int:
    """获取整数类型环境变量"""
    return int(os.getenv(key, str(default)))


def get_float(key: str, default: float) -> float:
    """获取浮点类型环境变量"""
    return float(os.getenv(key, str(default)))


########################################################



@dataclass
class BybitConfig:
    """Bybit 交易所配置"""
    testnet_url: str


# 实例化配置（开发环境）
bybit_config = BybitConfig(
    testnet_url=get_env("BYBIT_TESTNET_URL")
)