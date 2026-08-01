import logging
import sys
from pathlib import Path


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """获取或创建带模块前缀的logger

    Args:
        name: 模块名称（通常传 __name__）
        level: 日志级别，默认 INFO

    Returns:
        配置好的 Logger 实例
    """
    logger = logging.getLogger(name)

    if not logger.handlers:
        logger.setLevel(level)

        # 控制台 Handler（输出到 stderr）
        console = logging.StreamHandler(sys.stderr)
        console.setLevel(level)

        fmt = logging.Formatter(
            "[%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S"
        )
        console.setFormatter(fmt)
        logger.addHandler(console)

        # 防止日志向上传播到根 Logger
        logger.propagate = False

    return logger


def set_level(level: int):
    """全局统一设置日志级别"""
    for name in logging.root.manager.loggerDict:
        logging.getLogger(name).setLevel(level)
