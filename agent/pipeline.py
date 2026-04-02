# -*- coding: utf-8 -*-
"""
多智能体编排器 — 混合引擎架构

  用户在 Harness UI 创建仓库（描述即需求）
    ↓ watcher 检测到新仓库
  PlanAgent  (Cursor CLI plan mode)  → 生成实现计划
  CodeAgent  (DeepSeek Chat API)     → 根据计划生成代码并写入文件
  ReviewAgent(Cursor CLI ask mode)   → 代码审查
                 ↑                          ↓
                 └───── 不通过时循环修复 ─────┘
  Deliver  → git commit + push 到目标仓库
"""
import sys
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import json
import os
import re
import requests
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import TypedDict

from bus import EventBus, Event
from deepseek_api import call_deepseek, parse_file_blocks, write_files
from skill_loader import load_skill

AGENT_DIR = Path(__file__).parent
CODE_TIMEOUT = 300
PLAN_TIMEOUT = 120
REVIEW_TIMEOUT = 120

# ── Abort 支持 ─────────────────────────────────────────────────
_abort_flags: dict[str, bool] = {}


def set_abort(run_id: str):
    _abort_flags[run_id] = True


def is_aborted(run_id: str) -> bool:
    return _abort_flags.get(run_id, False)


def clear_abort(run_id: str):
    _abort_flags.pop(run_id, None)


def _check_abort(run_id: str) -> bool:
    """检查是否应中断，同时兼容 ws_server 的 abort flag。"""
    if _abort_flags.get(run_id):
        return True
    try:
        from ws_server import is_aborted as ws_is_aborted
        return ws_is_aborted(run_id)
    except ImportError:
        return False

HARNESS_BASE = os.environ.get("HARNESS_BASE_URL", "http://localhost:3000")
HARNESS_TOKEN = os.environ.get("HARNESS_TOKEN", "")


# ── State 定义 ─────────────────────────────────────────────────
class PipelineState(TypedDict):
    run_id: str
    requirement: str
    repo_url: str
    work_dir: str
    plan: str
    code_output: str
    changed_files: list[str]
    review_text: str
    review_score: int
    review_approved: bool
    review_must_fix: list[str]
    fix_rounds: int
    pr_url: str
    error: str


def _build_auth_url(repo_url: str) -> str:
    """构造带认证信息的 git remote URL。"""
    if not HARNESS_TOKEN:
        return repo_url
    from urllib.parse import urlparse, urlunparse
    parsed = urlparse(repo_url)
    host = parsed.hostname + (f":{parsed.port}" if parsed.port else "")
    return urlunparse(parsed._replace(netloc=f"agent:{HARNESS_TOKEN}@{host}"))


def _git_env() -> dict:
    """返回绕过 Windows GCM 的 git 环境变量。"""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_PROVIDER": "generic"}
    return env


def _setup_workdir(work_dir: str) -> bool:
    """准备本地工作目录（用于代码生成，不需要 git remote）。"""
    os.makedirs(work_dir, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=work_dir, capture_output=True)
    subprocess.run(["git", "config", "user.email", "agent@harness.local"],
                   cwd=work_dir, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Harness Agent"],
                   cwd=work_dir, capture_output=True)
    print(f"  [Git] 工作目录准备完成: {work_dir}", flush=True)
    return True


def _init_harness_repo(repo_path: str) -> bool:
    """
    确保 Harness 仓库非空——通过 Gitness 数据库找到 bare repo 路径并初始化。
    如果已有 commit 则跳过。
    """
    import sqlite3
    db_path = Path(__file__).parent.parent / "database.sqlite3"
    if not db_path.exists():
        print(f"  [Git] 数据库不存在，跳过仓库初始化", flush=True)
        return True

    repo_identifier = repo_path.split("/")[-1] if "/" in repo_path else repo_path
    try:
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT repo_git_uid FROM repositories WHERE repo_uid = ? ORDER BY repo_id DESC LIMIT 1",
            (repo_identifier,),
        ).fetchall()
        conn.close()
    except Exception as e:
        print(f"  [Git] 数据库查询失败: {e}", flush=True)
        return True

    if not rows:
        print(f"  [Git] 未找到仓库 {repo_identifier} 的 git_uid", flush=True)
        return True

    git_uid = rows[0][0]
    gitness_home = Path.home() / ".gitness" / "repos"
    bare_path = gitness_home / git_uid[:2] / git_uid[2:4] / f"{git_uid[4:]}.git"

    if bare_path.exists() and (bare_path / "refs" / "heads" / "main").exists():
        print(f"  [Git] 仓库已有 main 分支，无需初始化", flush=True)
        return True

    print(f"  [Git] 初始化 bare repo: {bare_path}", flush=True)
    if not bare_path.exists():
        subprocess.run(["git", "init", "--bare", str(bare_path)], capture_output=True)

    tmp = tempfile.mkdtemp(prefix="git-init-")
    try:
        subprocess.run(["git", "init", "-b", "main"], cwd=tmp, capture_output=True)
        subprocess.run(["git", "config", "user.email", "agent@harness.local"], cwd=tmp, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Harness Agent"], cwd=tmp, capture_output=True)
        readme = Path(tmp) / "README.md"
        readme.write_text(f"# {repo_identifier}\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp, capture_output=True)
        r = subprocess.run(
            ["git", "push", f"file://{bare_path}", "main"],
            cwd=tmp, capture_output=True, text=True,
        )
        if r.returncode == 0:
            print(f"  [Git] 初始化 commit 推送成功", flush=True)
            try:
                conn2 = sqlite3.connect(str(db_path))
                conn2.execute(
                    "UPDATE repositories SET repo_is_empty = 0 WHERE repo_uid = ?",
                    (repo_identifier,),
                )
                conn2.commit()
                conn2.close()
                print(f"  [Git] 数据库已更新: is_empty=false", flush=True)
            except Exception as e2:
                print(f"  [Git] 数据库更新失败: {e2}", flush=True)
        else:
            print(f"  [Git] 初始化 push 失败: {r.stderr[:200]}", flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return True


# ── PlanAgent (DeepSeek，速度快) ───────────────────────────────
PLAN_SYSTEM_PROMPT = """You are a senior software architect. Given a requirement, create a concise step-by-step implementation plan.
For each step, specify the file path and what to implement.
Do NOT write code — only output the plan."""


def plan_node(state: PipelineState, on_chunk=None) -> dict:
    t0 = time.time()
    requirement = state["requirement"]

    prompt = f"Requirement: {requirement}"

    print(f"  [PlanAgent] 开始调用 DeepSeek (timeout={PLAN_TIMEOUT}s)", flush=True)
    result = call_deepseek(
        prompt,
        system=PLAN_SYSTEM_PROMPT,
        timeout=PLAN_TIMEOUT,
        on_chunk=on_chunk,
    )
    elapsed = time.time() - t0
    print(f"  [PlanAgent] 完成, elapsed={elapsed:.0f}s, error={result['error']}", flush=True)

    if result["error"]:
        return {"plan": "", "error": f"PlanAgent 失败: {result['error']}"}

    return {"plan": result["result"], "error": ""}


# ── CodeAgent (DeepSeek API) ───────────────────────────────────
CODE_SYSTEM_PROMPT = """You are an expert programmer. Output ONLY code, no explanations.

Format — one section per file:

### FILE: path/to/filename.ext
```language
complete file content here
```

Rules:
- Write COMPLETE, COMPILABLE file contents — every function must be fully implemented
- No stubs, no placeholders, no TODOs, no "..." — every line must be real code
- No text outside the file blocks — ONLY output ### FILE: sections
- Keep comments minimal to save space"""

CONTINUE_SYSTEM_PROMPT = """You are continuing a code file that was cut off. Output ONLY the remaining code starting from exactly where it left off. Do NOT repeat any code that was already written. Do NOT add ### FILE: headers — just output the raw remaining code."""

MAX_CONTINUATIONS = 3


def _is_truncated(content: str) -> bool:
    """检测代码是否被截断（没有正常结束）。"""
    stripped = content.rstrip()
    if not stripped:
        return False
    if stripped.endswith("```"):
        return False
    last_block = stripped.rfind("```")
    if last_block == -1:
        return True
    after = stripped[last_block + 3:].strip()
    return len(after) > 5


def code_node(state: PipelineState, on_chunk=None) -> dict:
    t0 = time.time()
    requirement = state["requirement"]
    plan = state["plan"]
    work_dir = Path(state["work_dir"])
    fix_rounds = state.get("fix_rounds", 0)

    if fix_rounds > 0:
        review_issues = "\n".join(f"- {issue}" for issue in state.get("review_must_fix", []))
        prompt = f"""Previous code review found issues that need to be fixed:

{review_issues}

Review feedback:
{state.get('review_text', '')[:3000]}

Please output the COMPLETE fixed files using the ### FILE: format.

Original requirement: {requirement}"""
    else:
        prompt = f"""Requirement: {requirement}

Implementation Plan:
{plan}

Please implement ALL steps. Output every file using the ### FILE: format."""

    print(f"  [CodeAgent] 开始调用 DeepSeek (timeout={CODE_TIMEOUT}s, round={fix_rounds})", flush=True)
    result = call_deepseek(
        prompt,
        system=CODE_SYSTEM_PROMPT,
        timeout=CODE_TIMEOUT,
        on_chunk=on_chunk,
    )
    elapsed = time.time() - t0
    print(f"  [CodeAgent] DeepSeek 完成, elapsed={elapsed:.0f}s, error={result['error']}", flush=True)

    if result["error"]:
        return {"code_output": "", "error": f"CodeAgent 失败: {result['error']}"}

    content = result["result"]

    # 续写：如果代码被截断，发起后续请求拼接
    for cont_round in range(1, MAX_CONTINUATIONS + 1):
        if not _is_truncated(content):
            break
        print(f"  [CodeAgent] 检测到截断，发起续写 ({cont_round}/{MAX_CONTINUATIONS})", flush=True)
        if on_chunk:
            on_chunk("\n")
        tail = content[-1500:]
        cont_prompt = f"The code was cut off. Here is the end of what was generated so far:\n\n```\n{tail}\n```\n\nContinue writing from exactly where it stopped. Output ONLY the remaining code, no repetition."
        cont_result = call_deepseek(
            cont_prompt,
            system=CONTINUE_SYSTEM_PROMPT,
            timeout=CODE_TIMEOUT,
            on_chunk=on_chunk,
        )
        if cont_result["error"]:
            print(f"  [CodeAgent] 续写失败: {cont_result['error']}", flush=True)
            break
        content += "\n" + cont_result["result"]

    total_elapsed = time.time() - t0
    print(f"  [CodeAgent] 总耗时={total_elapsed:.0f}s", flush=True)

    blocks = parse_file_blocks(content)

    if not blocks:
        print(f"  [CodeAgent] 警告: 未解析到文件块，原始输出:\n{content[:500]}", flush=True)
        return {"code_output": content, "changed_files": [], "error": "未能从 DeepSeek 输出中解析到代码文件"}

    if len(blocks) == 1 and blocks[0]["path"] == "__single_block__":
        blocks[0]["path"] = "main.py"

    written = write_files(blocks, work_dir)
    print(f"  [CodeAgent] 写入 {len(written)} 个文件: {written}", flush=True)

    changed = _detect_changed_files(work_dir)
    print(f"  [CodeAgent] git 检测到变更文件: {changed}", flush=True)

    return {
        "code_output": content,
        "changed_files": changed if changed else written,
        "error": "",
    }


# ── ReviewAgent（skill 驱动）────────────────────────────────────
def review_node(state: PipelineState, on_chunk=None) -> dict:
    requirement = state["requirement"]
    changed_files = state.get("changed_files", [])
    work_dir = state["work_dir"]

    if not changed_files:
        return {
            "review_text": "No files changed, skipping review.",
            "review_score": 100,
            "review_approved": True,
            "review_must_fix": [],
        }

    # 加载 code_review skill 作为 system prompt
    skill_text = load_skill("code_review")
    if skill_text:
        system_prompt = (
            "You are a senior code reviewer. "
            "Follow the skill instructions below precisely.\n\n"
            f"{skill_text}"
        )
        print("  [ReviewAgent] 已加载 code_review skill", flush=True)
    else:
        system_prompt = "You are a senior code reviewer. Provide thorough, actionable feedback."
        print("  [ReviewAgent] 未找到 skill，使用默认 prompt", flush=True)

    files_list = "\n".join(f"- {f}" for f in changed_files[:20])

    prompt = f"""Review the following code changes.

Requirement: {requirement}

Changed files:
{files_list}"""

    # 读取代码文件内容，让 Review 有实际代码可审
    max_total = 12000
    code_snippets = []
    files_to_review = [f for f in changed_files[:15]
                       if (Path(work_dir) / f).exists()]
    per_file = max_total // max(len(files_to_review), 1)
    for fpath in files_to_review:
        full_path = Path(work_dir) / fpath
        content = full_path.read_text(encoding="utf-8", errors="replace")
        if len(content) > per_file:
            half = per_file // 2
            content = content[:half] + "\n// ... (middle omitted) ...\n" + content[-half:]
        code_snippets.append(f"\n### {fpath}\n```\n{content}\n```")
    if code_snippets:
        prompt += "\n\nFile contents:" + "".join(code_snippets)

    t0 = time.time()
    print(f"  [ReviewAgent] 开始调用 LLM (timeout={REVIEW_TIMEOUT}s)", flush=True)
    result = call_deepseek(
        prompt,
        system=system_prompt,
        timeout=REVIEW_TIMEOUT,
        on_chunk=on_chunk,
    )
    elapsed = time.time() - t0
    print(f"  [ReviewAgent] 完成, elapsed={elapsed:.0f}s, error={result['error']}", flush=True)

    if result["error"]:
        return {
            "review_text": f"Review failed: {result['error']}",
            "review_score": 50,
            "review_approved": True,
            "review_must_fix": [],
        }

    review_text = result["result"]
    score, approved, must_fix = _parse_review(review_text)

    return {
        "review_text": review_text,
        "review_score": score,
        "review_approved": approved,
        "review_must_fix": must_fix,
    }


# ── Delivery 节点 ──────────────────────────────────────────────
def _get_repo_ref(state: PipelineState) -> str:
    repo_path = state.get("repo_url", "").replace(f"{HARNESS_BASE}/git/", "").replace(".git", "")
    if not repo_path:
        repo_path = state.get("repo_path_raw", "")
    return repo_path


def _commit_to_harness(repo_path: str, actions: list, title: str, message: str) -> tuple[str, str]:
    """提交文件到 Harness 仓库。返回 (commit_id, error)。"""
    repo_ref = repo_path.replace("/", "%2F")
    url = f"{HARNESS_BASE}/api/v1/repos/{repo_ref}/commits"
    headers = {
        "Authorization": f"Bearer {HARNESS_TOKEN}",
        "Content-Type": "application/json",
    }
    body = {
        "title": title,
        "message": message,
        "actions": actions,
        "branch": "main",
    }

    try:
        resp = requests.post(url, headers=headers, json=body, timeout=30)
        if resp.status_code in (200, 201):
            data = resp.json()
            commit_id = data.get("commit_id", "")[:8]
            return commit_id, ""
        else:
            return "", resp.text[:300]
    except Exception as e:
        return "", str(e)


def _file_exists_in_repo(repo_path: str, file_path: str) -> bool:
    """检查文件是否已存在于 Harness 仓库中。"""
    repo_ref = repo_path.replace("/", "%2F")
    url = f"{HARNESS_BASE}/api/v1/repos/{repo_ref}/content/{file_path}"
    headers = {"Authorization": f"Bearer {HARNESS_TOKEN}"}
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


def deliver_code_node(state: PipelineState) -> dict:
    """代码完成后立即提交到仓库，让用户可在 Gitness UI 查看。"""
    run_id = state["run_id"]
    requirement = state["requirement"]
    work_dir = Path(state["work_dir"])
    repo_path = _get_repo_ref(state)

    changed_files = state.get("changed_files", [])
    if not changed_files:
        return {"pr_url": "", "error": "No files to deliver"}

    import base64
    actions = []
    for fpath in changed_files:
        full_path = work_dir / fpath
        if not full_path.exists():
            continue
        content_b64 = base64.b64encode(full_path.read_bytes()).decode("ascii")
        action = "UPDATE" if _file_exists_in_repo(repo_path, fpath) else "CREATE"
        actions.append({
            "action": action,
            "path": fpath,
            "payload": content_b64,
            "encoding": "base64",
        })

    if not actions:
        return {"pr_url": "", "error": "No files to commit"}

    print(f"  [Delivery] 提交 {len(actions)} 个代码文件到 {repo_path}", flush=True)
    commit_id, err = _commit_to_harness(
        repo_path, actions,
        title=f"feat: {requirement[:60]}",
        message=f"Auto-generated by AI Agent\nRun ID: {run_id}",
    )

    if err:
        print(f"  [Delivery] 代码提交失败: {err}", flush=True)
        return {"pr_url": "", "error": f"Code commit failed: {err}"}

    print(f"  [Delivery] 代码提交成功, commit={commit_id}", flush=True)
    repo_url_display = f"{HARNESS_BASE}/{repo_path}/files/main/~/"
    return {"pr_url": repo_url_display, "error": ""}


def deliver_review_node(state: PipelineState) -> dict:
    """将审核报告作为 AGENT_REVIEW.md 提交到仓库。"""
    run_id = state["run_id"]
    repo_path = _get_repo_ref(state)
    review_text = state.get("review_text", "")
    review_score = state.get("review_score", 0)
    requirement = state.get("requirement", "")

    if not review_text or not repo_path:
        return {"error": ""}

    review_md = (
        f"# Agent Code Review\n\n"
        f"**Run ID**: `{run_id}`\n"
        f"**Score**: {review_score}/100\n"
        f"**Requirement**: {requirement}\n\n"
        f"---\n\n"
        f"{review_text}\n"
    )

    import base64
    review_action = "UPDATE" if _file_exists_in_repo(repo_path, "AGENT_REVIEW.md") else "CREATE"
    actions = [{
        "action": review_action,
        "path": "AGENT_REVIEW.md",
        "payload": base64.b64encode(review_md.encode("utf-8")).decode("ascii"),
        "encoding": "base64",
    }]

    print(f"  [Delivery] 提交审核报告到 {repo_path}", flush=True)
    commit_id, err = _commit_to_harness(
        repo_path, actions,
        title=f"docs: agent review (score {review_score}/100)",
        message=f"AI Agent code review report\nRun ID: {run_id}",
    )

    if err:
        print(f"  [Delivery] 审核报告提交失败: {err}", flush=True)
        return {"error": f"Review commit failed: {err}"}

    print(f"  [Delivery] 审核报告提交成功, commit={commit_id}", flush=True)
    return {"error": ""}


# ── 主入口 ────────────────────────────────────────────────────
def run_pipeline(
    bus: EventBus,
    run_id: str,
    requirement: str,
    repo_url: str = "",
    repo_path: str = "",
    on_chunk=None,
) -> str | None:
    """
    完整的多智能体管线。

    Args:
        repo_url:  Harness 仓库 git 克隆地址 (http://localhost:3000/git/test/my-repo.git)
        repo_path: Harness 仓库路径 (test/my-repo)，用于构造 repo_url
        on_chunk:  流式回调 (stage: str, text: str) -> None，实时推送生成内容
    """
    bus.publish(Event.USER_REQUIREMENT, "user", requirement,
                data={"requirement": requirement, "repo_path": repo_path})

    if not repo_url and repo_path:
        repo_url = f"{HARNESS_BASE}/git/{repo_path}.git"

    work_dir = str(Path(tempfile.mkdtemp(prefix=f"agent-{run_id}-")))

    if repo_path:
        bus.publish(Event.AGENT_LOG, "system", f"初始化仓库: {repo_path}")
        _init_harness_repo(repo_path)

    _setup_workdir(work_dir)

    initial_state = {
        "run_id": run_id,
        "requirement": requirement,
        "repo_url": repo_url,
        "repo_path_raw": repo_path,
        "work_dir": work_dir,
        "plan": "",
        "code_output": "",
        "changed_files": [],
        "review_text": "",
        "review_score": 0,
        "review_approved": False,
        "review_must_fix": [],
        "fix_rounds": 0,
        "pr_url": "",
        "error": "",
    }

    try:
        return _execute_pipeline(bus, run_id, requirement, initial_state, on_chunk)
    finally:
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
            print(f"  [Cleanup] 已清理临时目录: {work_dir}", flush=True)
        except Exception:
            pass


def _make_stage_cb(on_chunk, stage: str):
    """为某个阶段创建带标签的流式回调。"""
    if on_chunk is None:
        return None
    return lambda text: on_chunk(stage, text)


def _execute_pipeline(bus: EventBus, run_id: str, requirement: str,
                      state: dict, on_chunk=None) -> str | None:
    # 共享 state 到 ws_server，供手动调用时使用
    try:
        from ws_server import set_run_state
        set_run_state(run_id, state)
    except ImportError:
        pass

    # 1. Plan
    bus.publish(Event.PLAN_STARTED, "planner", f"PlanAgent 开始分析需求: {requirement[:80]}")
    plan_result = plan_node(state, on_chunk=_make_stage_cb(on_chunk, "plan"))
    state.update(plan_result)

    if _check_abort(run_id):
        bus.publish(Event.PIPELINE_FAILED, "system", "用户中断")
        return None

    if state["error"]:
        bus.publish(Event.PLAN_FAILED, "planner", state["error"])
        return None

    bus.publish(Event.PLAN_DONE, "planner", "计划已生成",
                data={"plan": state["plan"][:500]})

    if _check_abort(run_id):
        bus.publish(Event.PIPELINE_FAILED, "system", "用户中断")
        return None

    # 2. Code
    state["fix_rounds"] = 0
    bus.publish(Event.CODE_STARTED, "coder", "CodeAgent 开始实现代码")
    code_result = code_node(state, on_chunk=_make_stage_cb(on_chunk, "code"))
    state.update(code_result)

    if _check_abort(run_id):
        bus.publish(Event.PIPELINE_FAILED, "system", "用户中断")
        return None

    if state["error"]:
        bus.publish(Event.CODE_FAILED, "coder", state["error"])
        return None

    bus.publish(Event.CODE_DONE, "coder",
                f"代码完成，变更 {len(state['changed_files'])} 个文件",
                data={"changed_files": state["changed_files"]})

    # 3. Deliver Code
    bus.publish(Event.AGENT_LOG, "system", "提交代码到仓库...")
    deliver_result = deliver_code_node(state)
    state.update(deliver_result)

    pr_url = state.get("pr_url", "")
    if pr_url:
        bus.publish(Event.PR_CREATED, "system",
                    f"代码已提交到仓库，可在 Gitness UI 查看",
                    data={"pr_url": pr_url})
    elif state.get("error"):
        bus.publish(Event.AGENT_LOG, "system", f"代码提交失败: {state['error']}")

    if _check_abort(run_id):
        bus.publish(Event.PIPELINE_FAILED, "system", "用户中断")
        return pr_url

    # 4. Review
    bus.publish(Event.REVIEW_STARTED, "reviewer", "ReviewAgent 开始代码审查（仅审核，不修改）")
    review_result = review_node(state, on_chunk=_make_stage_cb(on_chunk, "review"))
    state.update(review_result)

    score = state["review_score"]
    bus.publish(Event.REVIEW_DONE, "reviewer",
                f"审查完成，评分 {score}/100",
                data={
                    "score": score,
                    "approved": state.get("review_approved", True),
                    "must_fix": state.get("review_must_fix", []),
                    "review_text": state["review_text"][:2000],
                })

    # 5. Deliver Review
    bus.publish(Event.AGENT_LOG, "system", "提交审核报告到仓库...")
    deliver_review_node(state)

    # Save local report
    report = bus.format_report()
    report_path = AGENT_DIR / "runs" / run_id / "report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")

    bus.publish(Event.PIPELINE_DONE, "system", "全流程完成！",
                data={
                    "run_id": run_id,
                    "pr_url": pr_url,
                    "review_score": score,
                    "report": str(report_path),
                })

    # 同步最终 state
    try:
        from ws_server import set_run_state
        set_run_state(run_id, state)
    except ImportError:
        pass

    return pr_url


# ── 手动调用单个 Agent ─────────────────────────────────────────
def invoke_agent(agent_name: str, state: dict, on_chunk=None, arg: str = "") -> dict:
    """
    手动调用单个 Agent。供 /code /review /plan 命令使用。

    Args:
        agent_name: "code", "review", "plan"
        state: 当前 pipeline state
        on_chunk: 流式回调
        arg: 额外参数（如自定义 prompt）

    Returns:
        agent 结果 dict
    """
    if arg:
        state = {**state, "requirement": arg}

    if agent_name == "plan":
        return plan_node(state, on_chunk=_make_stage_cb(on_chunk, "plan"))
    elif agent_name == "code":
        return code_node(state, on_chunk=_make_stage_cb(on_chunk, "code"))
    elif agent_name == "review":
        return review_node(state, on_chunk=_make_stage_cb(on_chunk, "review"))
    else:
        return {"error": f"Unknown agent: {agent_name}"}


# ── 工具函数 ──────────────────────────────────────────────────
def _detect_changed_files(work_dir: Path | str) -> list[str]:
    work_dir = str(work_dir)
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=work_dir, capture_output=True, text=True,
    )
    staged = subprocess.run(
        ["git", "diff", "--name-only", "--cached"],
        cwd=work_dir, capture_output=True, text=True,
    )
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=work_dir, capture_output=True, text=True,
    )

    files = set()
    for output in [result.stdout, staged.stdout, untracked.stdout]:
        for line in output.strip().splitlines():
            f = line.strip()
            if f:
                files.add(f)

    return sorted(files)


def _parse_review(text: str) -> tuple[int, bool, list[str]]:
    score = 70
    approved = True
    must_fix = []

    score_match = re.search(r"REVIEW_SCORE:\s*(\d+)", text)
    if score_match:
        score = int(score_match.group(1))

    approved_match = re.search(r"REVIEW_APPROVED:\s*(true|false)", text, re.IGNORECASE)
    if approved_match:
        approved = approved_match.group(1).lower() == "true"

    must_fix_match = re.search(r"MUST_FIX:\s*\n((?:\s*-\s*.+\n?)+)", text)
    if must_fix_match:
        lines = must_fix_match.group(1).strip().splitlines()
        must_fix = [re.sub(r"^\s*-\s*", "", line).strip() for line in lines if line.strip()]

    should_fix_match = re.search(r"SHOULD_FIX:\s*\n((?:\s*-\s*.+\n?)+)", text)
    if should_fix_match:
        lines = should_fix_match.group(1).strip().splitlines()
        should = [re.sub(r"^\s*-\s*", "", line).strip() for line in lines if line.strip()]
        must_fix.extend(f"[SHOULD_FIX] {item}" for item in should)

    return score, approved, must_fix
