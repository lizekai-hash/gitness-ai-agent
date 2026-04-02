# -*- coding: utf-8 -*-
"""
Harness 仓库监听器

每 60 秒轮询 Harness API，发现新仓库（description 非空）时自动触发 Agent 管线。
"""
import sys
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import json
import os
import time
import threading
import requests
from pathlib import Path

AGENT_DIR = Path(__file__).parent
PROCESSED_FILE = AGENT_DIR / "runs" / "processed_repos.json"
INTAKE_URL = "http://localhost:3001/api/submit"
POLL_INTERVAL = 60


def _load_processed() -> set:
    PROCESSED_FILE.parent.mkdir(parents=True, exist_ok=True)
    if PROCESSED_FILE.exists():
        try:
            data = json.loads(PROCESSED_FILE.read_text(encoding="utf-8"))
            return set(data)
        except Exception:
            pass
    return set()


def _save_processed(ids: set):
    PROCESSED_FILE.write_text(
        json.dumps(sorted(ids), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _fetch_repos(base_url: str, token: str, space: str) -> list[dict]:
    url = f"{base_url}/api/v1/spaces/{space}/repos"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.get(url, headers=headers, params={"limit": 50, "order": "desc"}, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                return data
            return data.get("repositories", data.get("repos", []))
        else:
            print(f"[Watcher] 获取仓库列表失败: {resp.status_code} {resp.text[:200]}", flush=True)
    except Exception as e:
        print(f"[Watcher] 请求异常: {e}", flush=True)
    return []


def _submit_to_intake(requirement: str, repo_path: str) -> bool:
    try:
        resp = requests.post(
            INTAKE_URL,
            json={"requirement": requirement, "repo_path": repo_path},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            run_id = data.get("run_id", "?")
            print(f"[Watcher] 已触发 Agent，run_id={run_id}，需求: {requirement[:60]}", flush=True)
            return True
        else:
            print(f"[Watcher] intake 返回错误: {resp.status_code}", flush=True)
    except Exception as e:
        print(f"[Watcher] 提交到 intake 失败: {e}", flush=True)
    return False


def watch_loop(
    base_url: str | None = None,
    token: str | None = None,
    space: str | None = None,
    poll_interval: int = POLL_INTERVAL,
):
    base_url = base_url or os.environ.get("HARNESS_BASE_URL", "http://localhost:3000")
    token = token or os.environ.get("HARNESS_TOKEN", "")
    space = space or os.environ.get("HARNESS_SPACE", "admin")

    if not token:
        print("[Watcher] 警告：未设置 HARNESS_TOKEN，仓库监听功能不可用", flush=True)
        return

    print(f"[Watcher] 启动，监听空间: {space}  轮询间隔: {poll_interval}s", flush=True)
    processed = _load_processed()

    while True:
        repos = _fetch_repos(base_url, token, space)

        for repo in repos:
            repo_id = repo.get("id") or repo.get("uid")
            description = (repo.get("description") or "").strip()
            repo_path = repo.get("path") or repo.get("identifier", "")

            if not repo_id or not description:
                continue

            repo_id_str = str(repo_id)
            if repo_id_str in processed:
                continue

            print(f"[Watcher] 发现新仓库: {repo_path}  描述: {description[:80]}", flush=True)
            if _submit_to_intake(description, repo_path):
                processed.add(repo_id_str)
                _save_processed(processed)

        time.sleep(poll_interval)


def start_watcher_thread(poll_interval: int = POLL_INTERVAL) -> threading.Thread:
    t = threading.Thread(
        target=watch_loop,
        kwargs={"poll_interval": poll_interval},
        daemon=True,
        name="harness-watcher",
    )
    t.start()
    return t


if __name__ == "__main__":
    watch_loop(poll_interval=10)
