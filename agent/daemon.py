# -*- coding: utf-8 -*-
"""
主调度器（Daemon）

架构：
  用户在 Harness UI 创建仓库（描述=需求）
    → 在 Agent Dashboard 点击 Trigger
  PlanAgent  → CodeAgent (LLM API) → ReviewAgent
    → [循环修复]
    → Delivery (git commit + push)
"""
import sys
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import threading
import time

import intake
import pipeline
import ws_server
from bus import EventBus, Event


def _handle_command(run_id: str, cmd: str) -> str:
    """处理来自 Agent 终端的用户命令。"""
    state = ws_server.get_run_state(run_id)
    parts = cmd.strip().split(None, 1)
    name = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if name == "/status":
        if not state:
            return "[Agent] 无活跃状态"
        files = state.get("changed_files", [])
        return (
            f"[Agent] run_id={run_id}\r\n"
            f"  plan: {'有' if state.get('plan') else '无'}\r\n"
            f"  changed_files: {len(files)} 个\r\n"
            f"  review_score: {state.get('review_score', '-')}\r\n"
        )

    if name in ("/code", "/review", "/plan"):
        if not state:
            return "[Agent] 错误: 当前无 Pipeline 状态，请先 Trigger 一个仓库"
        run_info = intake._runs.get(run_id, {})
        if run_info.get("status") == "running":
            return "[Agent] 错误: Pipeline 正在运行中，请先 /abort 再手动调用"

        def _invoke():
            bus = EventBus(run_id)
            run_info = intake._runs.get(run_id, {})
            run_info["status"] = "running"

            def on_chunk(stage, text):
                ws_server.broadcast_chunk(run_id, stage, text)

            try:
                result = pipeline.invoke_agent(name[1:], state, on_chunk=on_chunk, arg=arg)
                if result:
                    state.update(result)
                    ws_server.set_run_state(run_id, state)
                ws_server.broadcast_event(run_id, {"event_type": "invoke.done", "agent": name[1:]})
            except Exception as e:
                ws_server.broadcast_event(run_id, {"event_type": "invoke.failed", "error": str(e)})
            finally:
                run_info["status"] = "done"

        threading.Thread(target=_invoke, daemon=True).start()
        return f"[Agent] 正在调用 {name[1:]}Agent..."

    if name == "/chat":
        if not arg:
            return "[Agent] 用法: /chat <优化描述>  例如: /chat 优化性能并增加错误处理"
        if not state:
            return "[Agent] 错误: 当前无 Pipeline 状态，请先 Trigger 一个仓库"
        run_info = intake._runs.get(run_id, {})
        if run_info.get("status") == "running":
            return "[Agent] 错误: Pipeline 正在运行中，请先 /abort"

        def _chat():
            run_info = intake._runs.get(run_id, {})
            run_info["status"] = "running"

            def on_chunk(stage, text):
                ws_server.broadcast_chunk(run_id, stage, text)

            ws_server.broadcast_event(run_id, {
                "event_type": "chat.started",
                "text": f"[ChatAgent] 根据 Review 反馈开始优化: {arg}\r\n",
            })
            try:
                result = pipeline.invoke_agent("chat", state, on_chunk=on_chunk, arg=arg)
                if result and not result.get("error"):
                    state.update(result)
                    ws_server.set_run_state(run_id, state)
                    files = result.get("changed_files", [])
                    ws_server.broadcast_event(run_id, {
                        "event_type": "chat.done",
                        "text": f"[ChatAgent] 优化完成，修改了 {len(files)} 个文件: {files}\r\n",
                    })
                else:
                    err = result.get("error", "未知错误") if result else "未知错误"
                    ws_server.broadcast_event(run_id, {
                        "event_type": "chat.failed",
                        "text": f"[ChatAgent] 失败: {err}\r\n",
                    })
            except Exception as e:
                ws_server.broadcast_event(run_id, {
                    "event_type": "chat.failed",
                    "text": f"[ChatAgent] 异常: {e}\r\n",
                })
            finally:
                run_info["status"] = "done"

        threading.Thread(target=_chat, daemon=True).start()
        return "[Agent] ChatAgent 已启动，正在根据 Review 反馈优化代码..."

    return f"[Agent] 未知命令: {name}  (输入 /help 查看帮助)"


def run_pipeline(run_id: str, requirement: str, repo_path: str = ""):
    """由 intake 异步调用的管线入口。"""
    print(f"\n{'='*60}", flush=True)
    print(f"[PIPELINE] 开始运行 {run_id}", flush=True)
    print(f"[TASK]     {requirement}", flush=True)
    if repo_path:
        print(f"[REPO]     {repo_path}", flush=True)
    print(f"{'='*60}\n", flush=True)

    ws_server.clear_abort(run_id)

    bus = EventBus(run_id)
    intake._runs[run_id]["status"] = "running"

    def on_chunk(stage, text):
        ws_server.broadcast_chunk(run_id, stage, text)

    try:
        pr_url = pipeline.run_pipeline(
            bus, run_id, requirement, repo_path=repo_path,
            on_chunk=on_chunk,
            keep_workdir=True,
        )

        intake._runs[run_id]["status"] = "done"
        if pr_url:
            intake._runs[run_id]["pr_url"] = pr_url

        review_msg = bus.get_latest(Event.REVIEW_DONE)
        if review_msg and review_msg.data:
            intake._runs[run_id]["review_score"] = review_msg.data.get("score", 0)
            intake._runs[run_id]["review_text"] = review_msg.data.get("review_text", "")

    except Exception as e:
        bus.publish(Event.PIPELINE_FAILED, "system", f"Pipeline 异常: {e}")
        intake._runs[run_id]["status"] = "failed"
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    intake.set_daemon_callback(run_pipeline)
    ws_server.set_command_callback(_handle_command)
    ws_server.start_ws_server()

    print("\n" + "=" * 60, flush=True)
    print("  AI Agent 自动化开发系统已启动", flush=True)
    print("  Dashboard  : http://localhost:3001", flush=True)
    print("  WebSocket  : ws://localhost:3002", flush=True)
    print("  Harness    : http://localhost:3000", flush=True)
    print("=" * 60 + "\n", flush=True)

    intake.start_server()
