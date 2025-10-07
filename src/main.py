"""
CLI 入口 - 策略主程序
Strategy main entry point

支持三种运行模式：
- test: 使用测试网数据 两所撮合，演示 S0→S5 状态迁移
- backtest: 回测模式（历史数据重放）
- live: 实盘交易模式

使用方法：
python src/main.py --mode test --config configs/example.yml
"""


import argparse

def main():
    pass


if __name__ == "__main__":
    main()