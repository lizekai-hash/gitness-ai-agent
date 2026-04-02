# -*- coding: utf-8 -*-
"""
一体化 CLI 入口 — 输入需求，自动建仓库，流式生成，推送到 Harness

用法:
    python run.py                            # 交互式输入需求，自动创建仓库
    python run.py "写一个光追 C++ 程序"       # 直接传入需求
    python run.py -n my-raytracer "需求..."  # 指定仓库名
    python run.py --no-repo "纯生成"         # 仅本地生成，不推送
    python run.py --no-stream "需求..."      # 关闭流式（只看最终结果）
"""
import sys
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import argparse
import os
import re
import requests
import time
import uuid

from bus import EventBus, Event
import pipeline

HARNESS_BASE = os.environ.get("HARNESS_BASE_URL", "http://localhost:3000")
HARNESS_TOKEN = os.environ.get("HARNESS_TOKEN", "")
HARNESS_SPACE = os.environ.get("HARNESS_SPACE", "test")

STAGE_STYLES = {
    "plan":   ("\033[96m", "PLAN"),
    "code":   ("\033[93m", "CODE"),
    "review": ("\033[95m", "REVIEW"),
}
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[92m"
RED = "\033[91m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
MAGENTA = "\033[95m"


# ── Harness 仓库管理 ──────────────────────────────────────────

def _harness_headers():
    return {
        "Authorization": f"Bearer {HARNESS_TOKEN}",
        "Content-Type": "application/json",
    }


def _slug_from_requirement(requirement: str) -> str:
    """从需求文本生成一个短的 repo 名称。"""
    text = requirement.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    words = text.split()[:4]
    slug = "-".join(words) if words else "agent-project"
    slug = re.sub(r"-+", "-", slug).strip("-")
    if len(slug) > 40:
        slug = slug[:40].rsplit("-", 1)[0]
    return slug or "agent-project"


def _repo_exists(space: str, repo_id: str) -> bool:
    """检查仓库是否已存在。"""
    try:
        resp = requests.get(
            f"{HARNESS_BASE}/api/v1/repos/{space}%2F{repo_id}",
            headers=_harness_headers(),
            timeout=5,
        )
        return resp.status_code == 200
    except Exception:
        return False


def _create_repo(space: str, repo_id: str, description: str) -> tuple[str, str]:
    """
    在 Harness 中创建仓库。
    返回 (repo_path, error)。
    """
    body = {
        "default_branch": "main",
        "description": description[:255],
        "identifier": repo_id,
        "is_public": True,
        "readme": True,
        "parent_ref": space,
    }
    try:
        resp = requests.post(
            f"{HARNESS_BASE}/api/v1/repos",
            headers=_harness_headers(),
            json=body,
            timeout=10,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            return data.get("path", f"{space}/{repo_id}"), ""
        elif resp.status_code == 409:
            return f"{space}/{repo_id}", ""
        else:
            return "", resp.text[:200]
    except Exception as e:
        return "", str(e)


def _ensure_repo(name_hint: str, requirement: str) -> tuple[str, str]:
    """
    确保仓库存在。返回 (repo_path, repo_id)。
    如果不存在就自动创建。
    """
    repo_id = name_hint or _slug_from_requirement(requirement)

    if _repo_exists(HARNESS_SPACE, repo_id):
        counter = 2
        base = repo_id
        while _repo_exists(HARNESS_SPACE, f"{base}-{counter}"):
            counter += 1
        repo_id = f"{base}-{counter}"

    print(f"  {MAGENTA}[Harness]{RESET} 创建仓库: {HARNESS_SPACE}/{repo_id}", flush=True)
    repo_path, err = _create_repo(HARNESS_SPACE, repo_id, requirement)
    if err:
        print(f"  {RED}[Harness] 创建失败: {err}{RESET}", flush=True)
        return "", repo_id

    print(f"  {GREEN}[Harness] 仓库就绪: {repo_path}{RESET}", flush=True)
    return repo_path, repo_id


# ── 终端 UI ──────────────────────────────────────────────────

def _stage_header(stage: str, label: str):
    color = STAGE_STYLES.get(stage, ("\033[97m", stage.upper()))[0]
    width = 60
    print(f"\n{color}{'─' * width}", flush=True)
    print(f"  ▶ {label}", flush=True)
    print(f"{'─' * width}{RESET}\n", flush=True)


def _on_chunk(stage: str, text: str):
    color = STAGE_STYLES.get(stage, ("\033[97m",))[0]
    sys.stdout.write(f"{color}{text}{RESET}")
    sys.stdout.flush()


def _on_event(msg):
    et = msg.event_type
    ts = time.strftime("%H:%M:%S", time.localtime(msg.timestamp))
    prefix = f"{DIM}[{ts}]{RESET}"

    if et == Event.PLAN_STARTED:
        _stage_header("plan", "PlanAgent — 分析需求，生成实现计划")
    elif et == Event.PLAN_DONE:
        print(f"\n\n{prefix} {GREEN}✓ 计划生成完毕{RESET}", flush=True)
    elif et == Event.PLAN_FAILED:
        print(f"\n{prefix} {RED}✗ Plan 失败: {msg.content}{RESET}", flush=True)
    elif et == Event.CODE_STARTED:
        _stage_header("code", "CodeAgent — 根据计划生成代码")
    elif et == Event.CODE_DONE:
        files = msg.data.get("changed_files", [])
        print(f"\n\n{prefix} {GREEN}✓ 代码完成，生成 {len(files)} 个文件:{RESET}", flush=True)
        for f in files:
            print(f"    {CYAN}•{RESET} {f}", flush=True)
    elif et == Event.CODE_FAILED:
        print(f"\n{prefix} {RED}✗ Code 失败: {msg.content}{RESET}", flush=True)
    elif et == Event.PR_CREATED:
        url = msg.data.get("pr_url", "")
        print(f"\n{prefix} {GREEN}✓ 代码已推送到 Harness 仓库{RESET}", flush=True)
        if url:
            print(f"    {CYAN}→ {url}{RESET}", flush=True)
    elif et == Event.REVIEW_STARTED:
        _stage_header("review", "ReviewAgent — 代码审查")
    elif et == Event.REVIEW_DONE:
        score = msg.data.get("score", "?")
        approved = msg.data.get("approved", True)
        status = f"{GREEN}通过{RESET}" if approved else f"{RED}未通过{RESET}"
        print(f"\n\n{prefix} {GREEN}✓ 审查完毕 — 评分 {BOLD}{score}/100{RESET} ({status})", flush=True)
        must_fix = msg.data.get("must_fix", [])
        if must_fix:
            print(f"    {YELLOW}需修复:{RESET}", flush=True)
            for issue in must_fix:
                print(f"    {RED}•{RESET} {issue}", flush=True)
    elif et == Event.PIPELINE_DONE:
        pr_url = msg.data.get("pr_url", "")
        score = msg.data.get("review_score", "?")
        report = msg.data.get("report", "")
        print(f"\n{'=' * 60}", flush=True)
        print(f"{GREEN}{BOLD}  ✓ 全流程完成！{RESET}", flush=True)
        print(f"    评分: {score}/100", flush=True)
        if pr_url:
            print(f"    仓库: {CYAN}{pr_url}{RESET}", flush=True)
        if report:
            print(f"    报告: {DIM}{report}{RESET}", flush=True)
        print(f"{'=' * 60}\n", flush=True)
    elif et == Event.PIPELINE_FAILED:
        print(f"\n{prefix} {RED}{BOLD}✗ Pipeline 失败: {msg.content}{RESET}", flush=True)


def _read_multiline() -> str:
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == "":
            if lines:
                break
            continue
        lines.append(line)
    return "\n".join(lines)


# ── 主入口 ────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AI Agent — 输入需求，自动建仓库 + 生成代码 + 推送",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("requirement", nargs="?", default="",
                        help="需求描述（省略则交互式输入）")
    parser.add_argument("-n", "--name", default="",
                        help="仓库名（省略则从需求自动生成）")
    parser.add_argument("-r", "--repo", default="",
                        help="已有 Harness 仓库路径，如 test/my-app")
    parser.add_argument("--no-repo", action="store_true",
                        help="仅本地生成，不推送到 Harness")
    parser.add_argument("--no-stream", action="store_true",
                        help="关闭流式输出")
    args = parser.parse_args()

    requirement = args.requirement.strip()

    if not requirement:
        print(f"\n{BOLD}AI Agent Pipeline — 一体化模式{RESET}")
        print(f"{DIM}输入你的需求描述，写完后按两次回车开始生成:{RESET}\n")
        requirement = _read_multiline()

    if not requirement:
        print(f"{RED}错误: 需求不能为空{RESET}")
        sys.exit(1)

    # 确定仓库
    repo_path = ""
    if args.no_repo:
        pass
    elif args.repo:
        repo_path = args.repo
    else:
        if not HARNESS_TOKEN:
            print(f"{RED}错误: 未设置 HARNESS_TOKEN 环境变量{RESET}")
            print(f"{DIM}设置方法: export HARNESS_TOKEN='your-pat-token'{RESET}")
            sys.exit(1)
        repo_path, repo_id = _ensure_repo(args.name, requirement)
        if not repo_path:
            print(f"{YELLOW}警告: 仓库创建失败，将仅在本地生成{RESET}")

    run_id = str(uuid.uuid4())[:8]

    print(f"\n{BOLD}{'=' * 60}{RESET}")
    print(f"  {BOLD}Run ID{RESET}    : {CYAN}{run_id}{RESET}")
    print(f"  {BOLD}需求{RESET}      : {requirement[:80]}{'...' if len(requirement) > 80 else ''}")
    if repo_path:
        print(f"  {BOLD}仓库{RESET}      : {CYAN}{HARNESS_BASE}/{repo_path}{RESET}")
    else:
        print(f"  {BOLD}仓库{RESET}      : {DIM}(仅本地生成){RESET}")
    print(f"  {BOLD}流式输出{RESET}  : {'开启' if not args.no_stream else '关闭'}")
    print(f"{BOLD}{'=' * 60}{RESET}\n")

    bus = EventBus(run_id)
    bus.subscribe("*", _on_event)

    chunk_cb = _on_chunk if not args.no_stream else None

    try:
        pr_url = pipeline.run_pipeline(
            bus, run_id, requirement,
            repo_path=repo_path,
            on_chunk=chunk_cb,
        )
    except KeyboardInterrupt:
        print(f"\n{YELLOW}已中断{RESET}")
        sys.exit(130)
    except Exception as e:
        print(f"\n{RED}Pipeline 异常: {e}{RESET}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
