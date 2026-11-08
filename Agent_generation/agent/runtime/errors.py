"""运行时统一异常定义

设计要点：
- RunCancelled 继承 BaseException：保证 Agent 内部大量存在的 `except Exception` 不会把“用户停止/超时取消”吞掉，
  取消信号一定能冒泡到编排层（与 asyncio.CancelledError 的设计思路一致）。
- LLM 错误区分可重试 / 不可重试，供网关做重试、降级与熔断决策。
"""


class RunCancelled(BaseException):
    """运行被取消（用户点击停止、会话关闭、步骤超时等）"""

    def __init__(self, reason: str = "cancelled"):
        super().__init__(reason)
        self.reason = reason


class LLMError(Exception):
    """LLM 调用失败（对外统一异常）"""


class LLMRetryableError(LLMError):
    """临时性故障：超时、连接错误、429、5xx、空响应、流中断"""


class LLMFatalError(LLMError):
    """不可恢复故障：鉴权失败、参数错误等，重试无意义"""


class CircuitOpenError(LLMRetryableError):
    """熔断器打开，快速失败"""


class BudgetExceeded(BaseException):
    """超出单次运行的调用次数 / Token / 时长预算

    与 RunCancelled 一样继承 BaseException：预算耗尽必须中止整个运行（由上层转为“暂停，可恢复”），
    不能被步骤级兜底（except Exception）当作普通失败而降级/重试，否则会继续消耗额度。
    """


class StructuredOutputError(LLMError):
    """LLM 结构化输出在修复重试后仍不合法"""
