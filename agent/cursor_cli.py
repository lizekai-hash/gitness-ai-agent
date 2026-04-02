# -*- coding: utf-8 -*-
"""
Cursor Agent CLI 封装层

通过 subprocess 调用 Cursor Agent CLI 的非交互模式，
替代 DeepSeek API 作为 LLM 后端。
"""
import sys
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

_AGENT_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "cursor-agent"
_VERSIONS_DIR = _AGENT_DIR / "versions"


def _find_agent_binary() -> tuple[str, str]:
    """定位 cursor-agent 的 node.exe 和 index.js 路径。"""
    if not _VERSIONS_DIR.exists():
        raise FileNotFoundError(
            f"Cursor Agent CLI 未安装。请在 PowerShell 中运行：\n"
            f"  irm 'https://cursor.com/install?win32=true' | iex\n"
            f"然后运行 agent login 完成认证。"
        )

    versions = sorted(_VERSIONS_DIR.iterdir(), reverse=True)
    if not versions:
        raise FileNotFoundError("Cursor Agent CLI 已安装但无可用版本。")

    version_dir = versions[0]
    node_exe = version_dir / "node.exe"
    index_js = version_dir / "index.js"

    if not node_exe.exists() or not index_js.exists():
        raise FileNotFoundError(
            f"Cursor Agent CLI 安装不完整 ({version_dir.name})。\n"
            f"请重新安装：irm 'https://cursor.com/install?win32=true' | iex"
        )

    return str(node_exe), str(index_js)


def call_cursor_agent(
    prompt: str,
    mode: str | None = None,
    model: str | None = None,
    workspace: str | None = None,
    force: bool = False,
    timeout: int = 600,
) -> dict:
    """
    调用 Cursor Agent CLI 非交互模式。

    Args:
        prompt: 发送给 Agent 的提示词
        mode: "plan" | "ask" | None(默认 agent 模式)
        model: 模型名称，如 "claude-4.5-sonnet"
        workspace: 工作目录，默认为仓库根目录
        force: 是否自动批准所有命令（--force）
        timeout: 超时秒数

    Returns:
        dict: {"result": str, "session_id": str, "usage": dict, "error": str | None}
    """
    node_exe, index_js = _find_agent_binary()

    cmd = [
        node_exe, index_js,
        "-p",
        "--output-format", "json",
        "--trust",
        "--workspace", workspace or str(REPO_ROOT),
    ]

    if mode:
        cmd.extend(["--mode", mode])
    if model:
        cmd.extend(["--model", model])
    if force:
        cmd.append("--force")

    cmd.append(prompt)

    import time as _time
    start_ts = _time.time()
    print(f"  [Cursor CLI] mode={mode or 'agent'}, model={model or 'default'}, timeout={timeout}s", flush=True)
    print(f"  [Cursor CLI] prompt ({len(prompt)} chars): {prompt[:150]}...", flush=True)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "NO_COLOR": "1"},
        )

        try:
            raw_stdout, raw_stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            elapsed = _time.time() - start_ts
            print(f"  [Cursor CLI] TIMEOUT after {elapsed:.0f}s, killing process (pid={proc.pid})...", flush=True)

            proc.kill()
            raw_stdout, raw_stderr = proc.communicate(timeout=30)

            partial_out = raw_stdout.decode("utf-8", errors="replace").strip() if raw_stdout else ""
            partial_err = raw_stderr.decode("utf-8", errors="replace").strip() if raw_stderr else ""

            diag = ""
            if partial_err:
                diag += f"\n  [CLI stderr tail]: {partial_err[-500:]}"
            if partial_out:
                diag += f"\n  [CLI stdout tail]: {partial_out[-500:]}"
            if diag:
                print(f"  [Cursor CLI] 超时诊断:{diag}", flush=True)

            partial_result = ""
            if partial_out:
                try:
                    data = json.loads(partial_out)
                    partial_result = data.get("result", "")
                except json.JSONDecodeError:
                    partial_result = partial_out[-2000:]

            return {
                "result": partial_result,
                "session_id": "",
                "usage": {},
                "error": f"Cursor Agent 超时 (>{timeout}s, elapsed={elapsed:.0f}s)",
            }

        elapsed = _time.time() - start_ts
        stdout = raw_stdout.decode("utf-8", errors="replace").strip()
        stderr = raw_stderr.decode("utf-8", errors="replace").strip()
        print(f"  [Cursor CLI] 完成, elapsed={elapsed:.0f}s, exit_code={proc.returncode}", flush=True)

        if proc.returncode != 0:
            print(f"  [Cursor CLI] stderr: {stderr[:300]}", flush=True)
            return {
                "result": "",
                "session_id": "",
                "usage": {},
                "error": f"CLI exit code {proc.returncode}: {stderr[:500]}",
            }

        try:
            data = json.loads(stdout)
            return {
                "result": data.get("result", ""),
                "session_id": data.get("session_id", ""),
                "usage": data.get("usage", {}),
                "error": data.get("error") if data.get("is_error") else None,
            }
        except json.JSONDecodeError:
            return {
                "result": stdout,
                "session_id": "",
                "usage": {},
                "error": None,
            }

    except Exception as e:
        elapsed = _time.time() - start_ts
        print(f"  [Cursor CLI] 异常: {e} (elapsed={elapsed:.0f}s)", flush=True)
        return {
            "result": "",
            "session_id": "",
            "usage": {},
            "error": str(e),
        }
