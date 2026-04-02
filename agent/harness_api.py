# -*- coding: utf-8 -*-
"""Harness Open Source REST API 封装"""
import os
import subprocess
import requests
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
HARNESS_BASE = os.environ.get("HARNESS_BASE_URL", "http://localhost:3000")
HARNESS_TOKEN = os.environ.get("HARNESS_TOKEN", "")


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {HARNESS_TOKEN}",
        "Content-Type": "application/json",
    }


def create_pr(
    space: str,
    repo: str,
    branch: str,
    title: str,
    description: str,
    base_branch: str = "main",
) -> str:
    url = f"{HARNESS_BASE}/api/v1/repos/{space}/{repo}/pullreq"
    body = {
        "title": title,
        "description": description,
        "source_branch": branch,
        "target_branch": base_branch,
        "is_draft": False,
    }
    resp = requests.post(url, json=body, headers=_headers())
    if resp.status_code in (200, 201):
        pr = resp.json()
        pr_number = pr.get("number", "?")
        return f"{HARNESS_BASE}/{space}/{repo}/pulls/{pr_number}"
    raise RuntimeError(f"创建 PR 失败: {resp.status_code} {resp.text}")


def add_pr_comment(space: str, repo: str, pr_number: int, body: str):
    url = f"{HARNESS_BASE}/api/v1/repos/{space}/{repo}/pullreq/{pr_number}/comments"
    resp = requests.post(url, json={"text": body}, headers=_headers())
    return resp.json()
