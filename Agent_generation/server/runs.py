"""运行管理器（RunManager）—— 每个会话一个独立运行单元

隔离（Harness / 沙箱）：
- 每个会话独立 RunContext：取消令牌、私有工作区（workspaces/<user>/<conversation>）、调用预算、指标
- Agent 只能调用其 JSON 注册的工具（ToolHarness 白名单），不存在执行任意代码/命令的工具
- 用户输入只作为数据进入 Prompt（长度限制、控制字符清洗），不会被当作指令执行
- 资源配额：每用户 / 全局并发运行数限制；单次运行 LLM 调用次数与 Token 预算

停止 / 继续：
- stop：取消令牌立即触发 → 正在进行的 LLM 流被关闭、子线程尽快退出；未完成步骤不写入（原子性）；
  run_epoch 失效，残留线程的任何检查点写入都会被拒绝
- resume：bump epoch → 从数据库中最后一个完整检查点恢复 PipelineState → advance()

事件：
- 内存扇出到所有 SSE 订阅者（每个订阅者独立队列，慢消费者丢弃最旧 token 事件）
- 环形缓冲（最近 4000 条）支持 Last-Event-ID 断线重连
- 关键事件（步骤、工具、思考/回答的聚合文本、合同快照）批量落库，历史会话可回放
"""
import json
import logging
import re
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from agent.pipeline import ContractPipeline, PipelineFinished, PipelineState, WaitingForInput
from agent.runtime import Budget, RunCancelled, new_context, use_context
from agent.runtime.errors import BudgetExceeded

from . import db
from .config import settings

log = logging.getLogger("runs")

STREAM_TYPES = {"llm.token", "agent.reasoning.stream", "agent.think.stream", "agent.act.stream", "agent.answer.stream",
                "agent.outline.stream"}
PERSIST_TYPES = {"step.start", "step.complete", "step.degraded", "step.retry", "tool.call", "tool.result",
                 "llm.fallback", "llm.retry", "parallel.start", "parallel.agent_start", "parallel.agent_complete",
                 "parallel.complete", "artifact.user_need", "artifact.templates", "artifact.reviews", "system",
                 "retrieval.web", "workflow.complete", "run.stopped", "run.failed", "run.waiting", "agent.ask"}
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_input(text: str) -> str:
    text = CONTROL.sub("", str(text or "")).strip()
    return text[: settings.max_input_chars]


def _release_heap():
    """把 Python 已释放的内存归还操作系统（glibc 默认不归还小块内存，长期运行 RSS 只涨不降）"""
    import ctypes
    import gc
    import sys
    gc.collect()
    if sys.platform.startswith("linux"):
        try:
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except Exception:  # noqa: BLE001
            pass


class Subscriber:
    """单个 SSE 连接的事件队列。

    - 有界 deque：慢消费者丢弃最旧事件，绝不阻塞 Agent 线程
    - 新事件到达时通过回调唤醒 asyncio 事件循环（零轮询，连接再多也不空转 CPU）
    """

    def __init__(self, maxsize: int = 2000):
        self.buf: deque = deque(maxlen=maxsize)
        self.closed = False
        self._waker = None
        self._lock = threading.Lock()

    def bind(self, waker):
        """waker：线程安全的“有新事件”通知函数（由 SSE 协程提供）"""
        with self._lock:
            self._waker = waker
            pending = bool(self.buf)
        if pending:
            waker()

    def push(self, ev: dict):
        with self._lock:
            self.buf.append(ev)
            waker = self._waker
        if waker is not None:
            try:
                waker()
            except Exception:  # noqa: BLE001  事件循环已关闭（连接断开）
                pass

    def drain(self, limit: int = 500) -> List[dict]:
        out = []
        with self._lock:
            while self.buf and len(out) < limit:
                out.append(self.buf.popleft())
        return out


# 合同全文事件：缓冲区中只保留最新一条完整合同，旧的同类事件剥离 outline（断线重连只需最新文档）
HEAVY_TYPES = {"contract.update", "contract.snapshot"}


@dataclass
class Channel:
    cid: str
    seq: int = 0
    buffer: deque = field(default_factory=lambda: deque(maxlen=1500))
    subs: List[Subscriber] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    last_active: float = field(default_factory=time.time)
    _heavy: Optional[dict] = None

    def publish(self, ev: dict) -> dict:
        with self.lock:
            self.seq += 1
            ev["id"] = self.seq
            self.last_active = time.time()
            stored = ev
            if ev.get("event_type") in HEAVY_TYPES and "outline" in (ev.get("data") or {}):
                # 缓冲区保存独立的浅拷贝：之后剥离旧文档时不影响尚未被订阅者消费的原事件
                stored = dict(ev)
                if self._heavy is not None:
                    self._heavy["data"] = {k: v for k, v in self._heavy["data"].items() if k != "outline"}
                self._heavy = stored
            self.buffer.append(stored)
            subs = list(self.subs)
        for s in subs:
            s.push(ev)
        return ev

    def subscribe(self, last_id: Optional[int]) -> Subscriber:
        s = Subscriber()
        with self.lock:
            if last_id is not None:
                for ev in self.buffer:
                    if ev["id"] > last_id:
                        s.push(ev)
            self.subs.append(s)
            self.last_active = time.time()
        return s

    def unsubscribe(self, s: Subscriber):
        with self.lock:
            if s in self.subs:
                self.subs.remove(s)
            self.last_active = time.time()
        s.closed = True

    @property
    def idle(self) -> bool:
        return not self.subs

    def shrink(self, keep: int):
        with self.lock:
            if len(self.buffer) > keep:
                self.buffer = deque(list(self.buffer)[-keep:], maxlen=self.buffer.maxlen)
            if self._heavy is not None and self._heavy not in self.buffer:
                self._heavy = None


class EventRecorder:
    """把流式 token 聚合为片段，与关键事件一起批量落库（每 1.5s 或 200 条 flush 一次）"""

    def __init__(self, cid: str, epoch: int):
        self.cid, self.epoch = cid, epoch
        self.pending: List[dict] = []
        self.agg: Dict[tuple, str] = {}
        self.last_flush = time.time()
        self.lock = threading.Lock()

    def add(self, ev: dict):
        t = ev["event_type"]
        with self.lock:
            if t in STREAM_TYPES and t != "llm.token" and t != "agent.outline.stream":
                key = (t, ev.get("agent"), ev.get("step"))
                self.agg[key] = self.agg.get(key, "") + ev["data"].get("text", "")
            elif t in PERSIST_TYPES or t.startswith("contract."):
                self._flush_agg()
                data = ev.get("data") or {}
                if t.startswith("contract.") and "outline" in data:
                    data = {k: v for k, v in data.items() if k != "outline"}
                self.pending.append({**ev, "data": data})
            need = len(self.pending) >= 200 or time.time() - self.last_flush > 1.5
        if need:
            self.flush()

    def _flush_agg(self):
        for (t, agent, step), txt in self.agg.items():
            if txt:
                self.pending.append({"event_type": t.replace(".stream", ".text"), "agent": agent, "step": step,
                                     "data": {"text": txt}})
        self.agg.clear()

    def flush(self, final: bool = False):
        with self.lock:
            if final:
                self._flush_agg()
            batch, self.pending = self.pending, []
            self.last_flush = time.time()
        if batch:
            try:
                db.add_events(self.cid, self.epoch, batch)
            except Exception as e:  # noqa: BLE001  事件落库失败不影响运行
                log.warning("persist events failed: %s", e)


@dataclass
class Run:
    """一个会话的运行单元。

    线程只在“执行一段流水线”期间存在：流水线停在等待用户输入时线程退出、释放线程栈，
    下一条消息再起一个新线程从内存中的 pipeline（或数据库检查点）继续。闲置会话由 janitor 回收。
    """
    cid: str
    uid: int
    epoch: int
    ctx: object
    thread: Optional[threading.Thread] = None
    pipeline: Optional[ContractPipeline] = None
    recorder: Optional[EventRecorder] = None
    billing: str = "free"
    model: str = "deepseek-flash"
    last_active: float = field(default_factory=time.time)


class QuotaExhausted(PermissionError):
    """免费额度用尽（需配置自有 API）"""


class RunManager:
    def __init__(self):
        self.channels: Dict[str, Channel] = {}
        self.runs: Dict[str, Run] = {}
        self.lock = threading.RLock()
        self._active_by_user: Dict[int, int] = {}

    # ------------------------------------------------------------ 事件
    def channel(self, cid: str) -> Channel:
        with self.lock:
            ch = self.channels.get(cid)
            if ch is None:
                ch = Channel(cid)
                self.channels[cid] = ch
            return ch

    def _sink(self, cid: str, recorder: EventRecorder, epoch: int):
        ch = self.channel(cid)

        def sink(event_type, step=None, agent=None, data=None):
            run = self.runs.get(cid)
            if run is None or run.epoch != epoch:   # 旧运行残留线程的事件直接丢弃
                return
            ev = {"event_type": event_type, "step": step, "agent": agent, "data": data or {}, "ts": time.time(),
                  "epoch": epoch}
            ch.publish(ev)
            recorder.add(ev)
            if event_type == "contract.snapshot" and (data or {}).get("outline") and not (data or {}).get("transient"):
                try:
                    db.add_contract_version(cid, step or 0, data.get("kind", "contract"), data["outline"])
                except Exception as e:  # noqa: BLE001
                    log.warning("save version failed: %s", e)
        return sink

    def emit(self, cid: str, event_type: str, data: dict = None, step=None, agent="system", persist: bool = False):
        ev = {"event_type": event_type, "step": step, "agent": agent, "data": data or {}, "ts": time.time()}
        self.channel(cid).publish(ev)
        if persist:   # 运行终止类事件落库：刷新页面后仍能看到“已停止 / 出错”状态
            try:
                run = self.runs.get(cid)
                db.add_events(cid, run.epoch if run else 0, [ev])
            except Exception as e:  # noqa: BLE001
                log.warning("persist control event failed: %s", e)

    # ------------------------------------------------------------ 运行控制
    def is_running(self, cid: str) -> bool:
        run = self.runs.get(cid)
        return bool(run and run.thread and run.thread.is_alive())

    def settle_waiting(self, cid: str, timeout: float = 3.0):
        """流水线已停在“等待用户输入”、运行线程正在收尾（落库事件 / 释放名额）时，稍等其退出。

        前端收到 run.waiting 后立即回复的消息不会因为这几毫秒的收尾窗口被判为“正在运行”而拒绝。
        """
        run = self.runs.get(cid)
        if run is None or run.thread is None or not run.thread.is_alive():
            return
        if run.pipeline is not None and run.pipeline.state.status == "waiting_input":
            run.thread.join(timeout)

    def is_waiting(self, cid: str) -> bool:
        run = self.runs.get(cid)
        return bool(run and not self.is_running(cid) and run.pipeline is not None
                    and run.pipeline.state.status == "waiting_input")

    def active_count(self, uid: Optional[int] = None) -> int:
        with self.lock:
            if uid is None:
                return sum(self._active_by_user.values())
            return self._active_by_user.get(uid, 0)

    def _admit(self, uid: int):
        """准入控制（内存计数，O(1)）：只统计正在执行的运行；等待用户输入的会话不占名额"""
        if self._active_by_user.get(uid, 0) >= settings.max_active_runs_per_user:
            raise PermissionError(f"同时运行的任务不能超过 {settings.max_active_runs_per_user} 个，请等待或停止其他任务")
        if sum(self._active_by_user.values()) >= settings.max_active_runs_total:
            raise PermissionError("系统繁忙，请稍后再试")
        self._active_by_user[uid] = self._active_by_user.get(uid, 0) + 1

    def _release(self, uid: int):
        with self.lock:
            n = self._active_by_user.get(uid, 0) - 1
            if n > 0:
                self._active_by_user[uid] = n
            else:
                self._active_by_user.pop(uid, None)

    def _spawn(self, run: Run, user_input: Optional[str]):
        run.last_active = time.time()
        run.thread = threading.Thread(target=self._loop, args=(run, user_input), daemon=True,
                                      name=f"run-{run.cid}-{run.epoch}")
        run.thread.start()

    def start(self, uid: int, conv: dict, user_input: Optional[str] = None):
        """开始/继续运行（新会话首条消息、等待输入时的回复、停止后的继续）"""
        cid = conv["id"]
        self.settle_waiting(cid)
        with self.lock:
            if self.is_running(cid):
                raise RuntimeError("该会话正在运行中")
            run = self.runs.get(cid)
            if user_input is not None and self.is_waiting(cid) and run.uid == uid:
                # 等待输入的会话：直接在内存中的 pipeline 上继续（同一 epoch，无需重读检查点）
                self._admit(uid)
                try:
                    db.set_status(cid, "running", run.epoch)
                    run.ctx.emit("run.resumed_input", {})
                    self._spawn(run, user_input)
                except BaseException:
                    self._release(uid)
                    raise
                return "queued"
            if run is not None and run.pipeline is not None:
                self._evict(cid, "restart")   # 内存中残留的暂歇运行（如等待输入时点了“继续”）：先记账释放
            self._admit(uid)
            try:
                return self._start_new(uid, conv, user_input)
            except BaseException:
                self._release(uid)
                raise

    def _start_new(self, uid: int, conv: dict, user_input: Optional[str]):
        cid = conv["id"]
        full = db.get_conversation(uid, cid, with_state=True)
        state = PipelineState.from_dict(full["state"]) if full.get("state") else PipelineState(cid)
        if state.status in ("completed", "closed"):
            raise PermissionError("会话已结束，不再接受输入")
        # 计费方式：用户启用了自有 API → own（不限额度）；否则使用平台免费额度（次数 + Token 双上限）
        from . import account
        provider = account.active_provider(uid)
        billing = "own" if provider is not None else "free"
        max_tokens = settings.run_max_tokens
        if billing == "free":
            q = account.get_quota(uid)
            if q["exhausted"]:
                raise QuotaExhausted(
                    f"免费额度已用完（完整生成 {q['free_runs_used']}/{q['free_runs_total']} 次，"
                    f"Token {q['free_tokens_used']:,}/{q['free_tokens_total']:,}）。请在个人主页配置自己的模型 API 后继续。")
            max_tokens = min(max_tokens, q["free_tokens_left"])
        epoch = db.bump_epoch(cid)
        recorder = EventRecorder(cid, epoch)
        ws = settings.workspaces_dir / str(uid) / cid
        budget = Budget(max_llm_calls=settings.run_max_llm_calls, max_total_tokens=max_tokens)
        ctx = new_context(run_id=f"{cid}:{epoch}", workspace=ws, sink=None, budget=budget,
                          tags={"provider": provider, "billing": billing})
        ctx = ctx.derive(sink=self._sink(cid, recorder, epoch))
        run = Run(cid=cid, uid=uid, epoch=epoch, ctx=ctx, recorder=recorder, billing=billing,
                  model=provider.model if provider else "deepseek-flash")

        def checkpoint(st: PipelineState):
            db.save_checkpoint(cid, epoch, st.to_dict(), st.status, st.current_step, st.error)

        run.pipeline = ContractPipeline(state, checkpoint=checkpoint)
        self.runs[cid] = run
        db.set_status(cid, "running", epoch)
        self._spawn(run, user_input)
        self.emit(cid, "run.started", {"epoch": epoch, "resumed": bool(state.step_done), "billing": billing,
                                       "model": run.model})
        return "started"

    def _loop(self, run: Run, pending: Optional[str]):
        cid, epoch, p = run.cid, run.epoch, run.pipeline
        settle = True
        try:
            with use_context(run.ctx):
                res = p.advance(pending)
            if isinstance(res, PipelineFinished):
                db.set_status(cid, "completed", epoch)
                self._assistant_message(cid, "合同已生成并确认完成，会话已关闭。您可以在右侧下载 Word 文档。", 8,
                                        {"kind": "final"})
                run.ctx.emit("run.completed", {"degraded": p.state.degraded})
                return
            assert isinstance(res, WaitingForInput)
            db.set_status(cid, "waiting_input", epoch)
            self._assistant_message(cid, res.prompt, res.step, {"kind": "ask"})
            run.ctx.emit("run.waiting", {"step": res.step, "prompt": res.prompt}, step=res.step)
            settle = False   # 只是暂歇（等待用户），Token 预算跨轮累计，运行结束时一次性记账
        except RunCancelled as e:
            db.set_status(cid, "paused", epoch, error=f"已停止（{e.reason}）")
            run.recorder.flush(final=True)
            self.emit(cid, "run.stopped", {"reason": e.reason, "step": p.state.current_step}, persist=True)
        except BudgetExceeded as e:
            db.set_status(cid, "paused", epoch, error=str(e))
            msg = (f"免费 Token 额度已用完：{e}。请在个人主页配置自己的模型 API 后点击“继续生成”，将从检查点继续。"
                   if run.billing == "free" else f"超出单次运行配额：{e}")
            self.emit(cid, "run.failed", {"error": msg, "recoverable": True, "quota": run.billing == "free"}, persist=True)
        except db.StaleWrite:
            log.info("stale run %s epoch %s exited", cid, epoch)
        except Exception as e:  # noqa: BLE001
            log.exception("run %s failed", cid)
            db.set_status(cid, "failed", epoch, error=f"{type(e).__name__}: {e}")
            self.emit(cid, "run.failed", {"error": f"{type(e).__name__}: {str(e)[:300]}", "recoverable": True},
                      persist=True)
        finally:
            run.recorder.flush(final=True)
            run.last_active = time.time()
            self._release(run.uid)
            if settle:
                self._settle(run)
                self._forget(run)

    def _forget(self, run: Run):
        """运行彻底结束（完成 / 停止 / 失败）：释放 pipeline 等大对象；事件通道由 gc() 按空闲回收"""
        with self.lock:
            if self.runs.get(run.cid) is run:
                run.pipeline = None
                run.recorder = None

    def _settle(self, run: "Run"):
        """运行结束记账：Token 按实际消耗计；完整流程走完才计 1 次免费生成次数"""
        try:
            from . import account
            b = run.ctx.budget
            completed = run.pipeline is not None and run.pipeline.state.status == "completed"
            account.charge(run.uid, run.cid, run.epoch, run.billing, run.model, b.total_tokens if b else 0,
                           b.llm_calls if b else 0, completed)
        except Exception as e:  # noqa: BLE001
            log.warning("settle usage failed: %s", e)

    def _assistant_message(self, cid: str, text: str, step: int, meta: dict):
        try:
            db.add_message(cid, "assistant", text, step=step, meta=meta)
        except Exception as e:  # noqa: BLE001
            log.warning("save message failed: %s", e)

    def submit_input(self, uid: int, conv: dict, text: str):
        """用户发送消息：等待输入 → 在内存 pipeline 上继续；未运行 → 启动运行（带上这条输入）"""
        return self.start(uid, conv, user_input=text)

    def stop(self, cid: str, reason: str = "user_stop", wait: float = 8.0) -> bool:
        run = self.runs.get(cid)
        if run is None:
            return False
        if not self.is_running(cid):
            if not self.is_waiting(cid):
                return False
            # 等待输入中的会话被停止 / 删除：没有线程需要取消，标记暂停、记账并释放内存
            db.set_status(cid, "paused", run.epoch, error=f"已停止（{reason}）")
            self.emit(cid, "run.stopped", {"reason": reason, "step": run.pipeline.state.current_step}, persist=True)
            self._evict(cid, reason)
            return True
        run.ctx.cancel.cancel(reason)
        run.thread.join(wait)
        if run.thread.is_alive():   # 线程仍在退出中：epoch 已作废（下次启动会 bump），其写入将被拒绝
            db.set_status(cid, "paused", run.epoch, error="已停止")
        return True

    def _evict(self, cid: str, reason: str = "idle"):
        """回收等待输入的会话：记账、释放内存（检查点已在数据库，下次发消息从检查点恢复）"""
        with self.lock:
            run = self.runs.get(cid)
            if run is None or self.is_running(cid):
                return
            self.runs.pop(cid, None)
        if run.pipeline is not None:
            self._settle(run)
        run.ctx.cancel.cancel(reason)
        log.info("evicted run %s (%s)", cid, reason)

    def gc(self, idle_seconds: float = None, channel_idle_seconds: float = 600) -> dict:
        """周期回收：等待输入超时的会话释放 pipeline；无订阅者且无运行的事件通道释放缓冲"""
        idle_seconds = settings.run_idle_evict_seconds if idle_seconds is None else idle_seconds
        now = time.time()
        evicted = 0
        for cid, run in list(self.runs.items()):
            if self.is_running(cid):
                continue
            if run.pipeline is None or now - run.last_active > idle_seconds:
                if run.pipeline is None:
                    with self.lock:
                        if self.runs.get(cid) is run:
                            self.runs.pop(cid, None)
                else:
                    self._evict(cid)
                evicted += 1
        dropped = 0
        with self.lock:
            for cid, ch in list(self.channels.items()):
                if cid in self.runs or not ch.idle or now - ch.last_active < channel_idle_seconds:
                    continue
                self.channels.pop(cid, None)
                dropped += 1
        # 已结束的会话：事件早已落库，缓冲区只需保留最近一小段供断线续传
        for cid, ch in list(self.channels.items()):
            if cid not in self.runs and now - ch.last_active > 60:
                ch.shrink(50)
        if evicted or dropped:
            _release_heap()
        return {"evicted": evicted, "channels_dropped": dropped, "runs": len(self.runs),
                "channels": len(self.channels)}

    def stats(self) -> dict:
        with self.lock:
            return {"running": sum(1 for c in self.runs if self.is_running(c)),
                    "waiting": sum(1 for c in self.runs if self.is_waiting(c)),
                    "channels": len(self.channels), "subscribers": sum(len(c.subs) for c in self.channels.values()),
                    "threads": threading.active_count()}

    def shutdown(self):
        for cid in list(self.runs):
            self.stop(cid, "server_shutdown", wait=3)


manager = RunManager()
