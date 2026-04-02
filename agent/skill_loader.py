# -*- coding: utf-8 -*-
"""
Skill 加载器 — 从 skills/ 目录读取 SKILL.md + rules/ 规则文件，
拼装成可注入 LLM system prompt 的文本。
"""
import re
from pathlib import Path
from typing import Optional

SKILLS_DIR = Path(__file__).parent / "skills"

_cache: dict[str, str] = {}


def load_skill(skill_name: str, *, force_reload: bool = False) -> Optional[str]:
    """
    加载指定 skill 的完整内容（SKILL.md + rules/*.md）。

    Args:
        skill_name: skills/ 下的子目录名，如 "code_review"
        force_reload: 强制重新读取，忽略缓存

    Returns:
        拼装后的完整 skill 文本，找不到则返回 None
    """
    if not force_reload and skill_name in _cache:
        return _cache[skill_name]

    skill_dir = SKILLS_DIR / skill_name
    skill_md = skill_dir / "SKILL.md"

    if not skill_md.exists():
        return None

    parts = []

    raw = skill_md.read_text(encoding="utf-8")
    body = _strip_frontmatter(raw)
    parts.append(body.strip())

    rules_dir = skill_dir / "rules"
    if rules_dir.is_dir():
        for rule_file in sorted(rules_dir.glob("*.md")):
            rule_text = rule_file.read_text(encoding="utf-8").strip()
            parts.append(f"\n---\n{rule_text}")

    combined = "\n\n".join(parts)
    _cache[skill_name] = combined
    return combined


def list_skills() -> list[dict]:
    """列出所有可用 skill 及其元信息。"""
    skills = []
    if not SKILLS_DIR.exists():
        return skills

    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue

        raw = skill_md.read_text(encoding="utf-8")
        meta = _parse_frontmatter(raw)
        meta["id"] = skill_dir.name
        meta.setdefault("name", skill_dir.name)
        skills.append(meta)

    return skills


def _strip_frontmatter(text: str) -> str:
    """去除 YAML frontmatter (--- ... ---)。"""
    m = re.match(r"^---\s*\n.*?\n---\s*\n", text, re.DOTALL)
    if m:
        return text[m.end():]
    return text


def _parse_frontmatter(text: str) -> dict:
    """简易解析 YAML frontmatter 为 dict。"""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return {}
    result = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if ":" in line and not line.startswith("#"):
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if val.startswith(">"):
                continue
            if val:
                result[key] = val
    return result
