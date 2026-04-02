# -*- coding: utf-8 -*-
"""
LLM Chat API 封装层（兼容 OpenAI / DeepSeek / Claude 代理）

用于各 Agent 节点：发送 prompt，接收生成结果，
解析 markdown 代码块并写入对应文件。
"""
import sys
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import json
import os
import re
import time
import requests
from pathlib import Path

LLM_API_KEY = os.environ.get(
    "LLM_API_KEY",
    os.environ.get("DEEPSEEK_API_KEY", "sk-4iUHMupX3cHmG3NuUM2qYA"),
)
LLM_BASE_URL = os.environ.get(
    "LLM_BASE_URL",
    os.environ.get("DEEPSEEK_BASE_URL", "https://llm-proxy.tapsvc.com"),
)
LLM_MODEL = os.environ.get(
    "LLM_MODEL",
    os.environ.get("DEEPSEEK_MODEL", "claude-sonnet-4-6"),
)

_TAG = "LLM"


def call_deepseek(
    prompt: str,
    system: str = "",
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 8192,
    timeout: int = 300,
    on_chunk: "Callable[[str], None] | None" = None,
) -> dict:
    """
    调用 LLM Chat Completions API（OpenAI 兼容格式）。

    Args:
        on_chunk: 流式回调，每收到一段文本就调用一次。传入后自动启用 stream 模式。

    Returns:
        {"result": str, "usage": dict, "error": str | None}
    """
    model = model or LLM_MODEL
    base = LLM_BASE_URL.rstrip("/")
    url = f"{base}/v1/chat/completions" if "/v1" not in base else f"{base}/chat/completions"
    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    }

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    use_stream = on_chunk is not None
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": use_stream,
    }

    t0 = time.time()
    print(f"  [{_TAG}] model={model}, timeout={timeout}s, stream={use_stream}", flush=True)
    print(f"  [{_TAG}] prompt ({len(prompt)} chars): {prompt[:150]}...", flush=True)

    try:
        resp = requests.post(url, headers=headers, json=payload,
                             timeout=timeout, stream=use_stream)
        elapsed_status = time.time() - t0
        print(f"  [{_TAG}] 连接建立, elapsed={elapsed_status:.0f}s, status={resp.status_code}", flush=True)

        if resp.status_code != 200:
            return {
                "result": "",
                "usage": {},
                "error": f"LLM API {resp.status_code}: {resp.text[:500]}",
            }

        if use_stream:
            return _consume_stream(resp, on_chunk, t0)

        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        elapsed = time.time() - t0
        print(f"  [{_TAG}] 完成, elapsed={elapsed:.0f}s", flush=True)

        return {"result": content, "usage": usage, "error": None}

    except requests.exceptions.Timeout:
        elapsed = time.time() - t0
        return {
            "result": "",
            "usage": {},
            "error": f"LLM API 超时 (>{timeout}s, elapsed={elapsed:.0f}s)",
        }
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  [{_TAG}] 异常: {e} (elapsed={elapsed:.0f}s)", flush=True)
        return {"result": "", "usage": {}, "error": str(e)}


def _consume_stream(resp, on_chunk, t0: float) -> dict:
    """逐行读取 SSE 流并拼接完整结果。"""
    chunks = []
    usage = {}
    for raw_line in resp.iter_lines(decode_unicode=True):
        if not raw_line:
            continue
        line = raw_line
        if line.startswith("data: "):
            line = line[6:]
        if line.strip() == "[DONE]":
            break
        try:
            obj = json.loads(line)
            delta = obj["choices"][0].get("delta", {})
            text = delta.get("content", "")
            if text:
                chunks.append(text)
                on_chunk(text)
            if "usage" in obj:
                usage = obj["usage"]
        except (json.JSONDecodeError, KeyError, IndexError):
            continue

    elapsed = time.time() - t0
    full_text = "".join(chunks)
    print(f"\n  [{_TAG}] 流式完成, elapsed={elapsed:.0f}s, chars={len(full_text)}", flush=True)
    return {"result": full_text, "usage": usage, "error": None}


def parse_file_blocks(text: str) -> list[dict]:
    """
    从 LLM 输出中解析代码块，支持续写拼接。

    支持的格式:
        ### FILE: path/to/file.py
        ```python
        code here
        ```

    如果代码被截断后续写，续写的裸代码会被追加到最后一个 block。
    """
    blocks = []

    # Pattern 1: ### FILE: path 后跟代码块（可能没有结尾 ```）
    file_header_pattern = re.compile(
        r"(?:###?\s*(?:FILE|File|file)[:\s]+)([^\n]+)\s*\n"
        r"```[a-zA-Z]*\n(.*?)(?:```|$)",
        re.DOTALL,
    )
    for m in file_header_pattern.finditer(text):
        filepath = m.group(1).strip().strip("`").strip()
        code = m.group(2)
        if filepath and code.strip():
            blocks.append({"path": filepath, "content": code})

    if blocks:
        # 检查是否有续写的裸代码在最后一个 block 之后
        last_match_end = 0
        for m in file_header_pattern.finditer(text):
            last_match_end = m.end()
        remainder = text[last_match_end:].strip()
        # 去掉续写中可能重复的 ``` 开头
        remainder = re.sub(r"^```[a-zA-Z]*\n?", "", remainder)
        remainder = re.sub(r"```\s*$", "", remainder)
        if remainder.strip():
            blocks[-1]["content"] += "\n" + remainder
        return blocks

    # Pattern 2: ```filename.ext\ncode```
    inline_pattern = re.compile(
        r"```([a-zA-Z0-9_./-]+\.[a-zA-Z0-9]+)\n(.*?)(?:```|$)",
        re.DOTALL,
    )
    for m in inline_pattern.finditer(text):
        filepath = m.group(1).strip()
        code = m.group(2)
        if "/" in filepath or filepath.endswith((".py", ".go", ".js", ".ts", ".html", ".css", ".json", ".yaml", ".yml", ".toml", ".md", ".txt", ".sh")):
            blocks.append({"path": filepath, "content": code})

    if blocks:
        return blocks

    # Pattern 3: 单个代码块
    single_block = re.compile(r"```[a-zA-Z]*\n(.*?)(?:```|$)", re.DOTALL)
    matches = single_block.findall(text)
    if len(matches) == 1 and matches[0].strip():
        blocks.append({"path": "__single_block__", "content": matches[0]})

    return blocks


def write_files(blocks: list[dict], repo_root: Path) -> list[str]:
    """将解析出的代码块写入文件系统，返回写入的文件路径列表。"""
    repo_root = Path(repo_root)
    written = []
    for block in blocks:
        filepath = block["path"]
        if filepath == "__single_block__":
            continue

        fp = Path(filepath)
        if fp.is_absolute():
            filepath = fp.name
        filepath = filepath.lstrip("/\\")

        full_path = repo_root / filepath
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(block["content"], encoding="utf-8")
        written.append(filepath)
        print(f"  [{_TAG}] 写入文件: {filepath} ({len(block['content'])} chars)", flush=True)

    return written
