# -*- coding: utf-8 -*-
"""
WebSocket 服务器 — Agent 终端 + Shell 终端

  /ws/agent/{run_id}  — LLM 流式输出广播 + 用户命令
  /ws/shell            — 完整 bash PTY
"""
import sys
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import asyncio
import json
import os
import subprocess
import threading
from pathlib import Path

import websockets
from websockets.asyncio.server import serve

AGENT_DIR = Path(__file__).parent

# ── Agent 终端状态 ──────────────────────────────────────────────
_agent_clients: dict[str, set] = {}
_abort_flags: dict[str, bool] = {}
_run_states: dict[str, dict] = {}
_command_callback = None
_loop: asyncio.AbstractEventLoop | None = None


def set_command_callback(fn):
    """注册命令处理回调: fn(run_id, cmd_str) -> str"""
    global _command_callback
    _command_callback = fn


def broadcast_chunk(run_id: str, stage: str, text: str):
    """由 pipeline on_chunk 回调调用，广播 LLM 输出到所有连接的客户端。"""
    if not _loop or not _agent_clients.get(run_id):
        return
    msg = json.dumps({"type": "chunk", "stage": stage, "text": text}, ensure_ascii=False)
    for ws in list(_agent_clients.get(run_id, [])):
        try:
            asyncio.run_coroutine_threadsafe(ws.send(msg), _loop)
        except Exception:
            pass


def broadcast_event(run_id: str, event: dict):
    """广播结构化事件到 agent 终端。"""
    if not _loop or not _agent_clients.get(run_id):
        return
    msg = json.dumps({"type": "event", **event}, ensure_ascii=False)
    for ws in list(_agent_clients.get(run_id, [])):
        try:
            asyncio.run_coroutine_threadsafe(ws.send(msg), _loop)
        except Exception:
            pass


def set_abort(run_id: str):
    _abort_flags[run_id] = True


def is_aborted(run_id: str) -> bool:
    return _abort_flags.get(run_id, False)


def clear_abort(run_id: str):
    _abort_flags.pop(run_id, None)


def set_run_state(run_id: str, state: dict):
    _run_states[run_id] = state


def get_run_state(run_id: str) -> dict | None:
    return _run_states.get(run_id)


# ── Agent WebSocket Handler ────────────────────────────────────
async def _agent_handler(ws, run_id: str):
    _agent_clients.setdefault(run_id, set()).add(ws)
    welcome = json.dumps({
        "type": "system",
        "text": f"[Agent Terminal] 已连接到 Pipeline {run_id}\r\n"
                "可用命令: /code /review /plan /abort /status /help\r\n"
    }, ensure_ascii=False)
    await ws.send(welcome)
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                msg = {"type": "cmd", "cmd": raw.strip()}

            if msg.get("type") == "cmd":
                cmd = msg.get("cmd", "").strip()
                if not cmd:
                    continue
                if cmd == "/abort":
                    set_abort(run_id)
                    resp = json.dumps({"type": "system", "text": "[Agent] 正在中断 Pipeline...\r\n"}, ensure_ascii=False)
                    await ws.send(resp)
                elif cmd == "/help":
                    help_text = (
                        "[Agent] 可用命令:\r\n"
                        "  /code [prompt]  — 手动触发 CodeAgent\r\n"
                        "  /review         — 手动触发 ReviewAgent\r\n"
                        "  /plan [req]     — 手动触发 PlanAgent\r\n"
                        "  /abort          — 中断当前 Pipeline\r\n"
                        "  /status         — 查看当前状态\r\n"
                    )
                    await ws.send(json.dumps({"type": "system", "text": help_text}, ensure_ascii=False))
                elif _command_callback:
                    result = _command_callback(run_id, cmd)
                    if result:
                        await ws.send(json.dumps({"type": "system", "text": result + "\r\n"}, ensure_ascii=False))
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        _agent_clients.get(run_id, set()).discard(ws)


# ── Shell WebSocket Handler（命令行模式）──────────────────────
async def _shell_handler(ws):
    cwd = str(AGENT_DIR)
    input_buf = ""

    async def _send(text: str):
        await ws.send(json.dumps({"type": "shell_output", "text": text}, ensure_ascii=False))

    await _send(f"\x1b[32m{cwd}\x1b[0m\r\n$ ")

    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                msg = {"type": "shell_input", "text": raw}

            if msg.get("type") != "shell_input":
                continue

            char = msg.get("text", "")

            if char == "\r" or char == "\n":
                await _send("\r\n")
                cmd = input_buf.strip()
                input_buf = ""

                if not cmd:
                    await _send(f"\x1b[32m{cwd}\x1b[0m\r\n$ ")
                    continue

                if cmd.startswith("cd "):
                    target = cmd[3:].strip().strip('"').strip("'")
                    new_cwd = os.path.normpath(os.path.join(cwd, target)) if not os.path.isabs(target) else target
                    if os.path.isdir(new_cwd):
                        cwd = new_cwd
                        await _send(f"\x1b[32m{cwd}\x1b[0m\r\n$ ")
                    else:
                        await _send(f"\x1b[31mcd: no such directory: {target}\x1b[0m\r\n$ ")
                    continue

                if cmd == "cd":
                    cwd = str(Path.home())
                    await _send(f"\x1b[32m{cwd}\x1b[0m\r\n$ ")
                    continue

                try:
                    proc = await asyncio.create_subprocess_shell(
                        cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT,
                        cwd=cwd,
                        env={**os.environ, "TERM": "xterm-256color"},
                    )
                    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
                    if stdout:
                        text = stdout.decode("utf-8", errors="replace")
                        text = text.replace("\n", "\r\n")
                        await _send(text)
                except asyncio.TimeoutError:
                    proc.kill()
                    await _send("\x1b[31m[timeout: 30s exceeded]\x1b[0m\r\n")
                except Exception as e:
                    await _send(f"\x1b[31m{e}\x1b[0m\r\n")

                await _send(f"\x1b[32m{cwd}\x1b[0m\r\n$ ")

            elif char == "\x7f" or char == "\b":
                if input_buf:
                    input_buf = input_buf[:-1]
                    await _send("\b \b")
            elif char == "\x03":
                input_buf = ""
                await _send("^C\r\n$ ")
            else:
                input_buf += char
                await _send(char)

    except websockets.exceptions.ConnectionClosed:
        pass


# ── Router ─────────────────────────────────────────────────────
async def _router(ws):
    path = ws.request.path if hasattr(ws, 'request') else ""
    if path.startswith("/ws/agent/"):
        run_id = path.split("/")[-1]
        await _agent_handler(ws, run_id)
    elif path == "/ws/shell":
        await _shell_handler(ws)
    else:
        await ws.close(4004, "Unknown path")


# ── Server Startup ─────────────────────────────────────────────
async def _run_server(host: str, port: int):
    global _loop
    _loop = asyncio.get_event_loop()
    async with serve(_router, host, port, ping_interval=30, ping_timeout=10) as server:
        print(f"  [WebSocket] 服务启动: ws://localhost:{port}", flush=True)
        await asyncio.Future()


def start_ws_server(host: str = "0.0.0.0", port: int = 3002):
    """在新线程中启动 WebSocket 服务器。"""
    def _run():
        asyncio.run(_run_server(host, port))

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t
