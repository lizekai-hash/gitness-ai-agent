---
name: code-review
description: >
  Multi-perspective automated code review with confidence-based scoring.
  Analyzes correctness, security, performance, and maintainability.
  Outputs structured findings compatible with the agent pipeline.
version: 1.0.0
---

# Code Review Skill

Automated code review using multiple analysis perspectives with confidence-based
scoring to minimize false positives.

## Overview

This skill guides the ReviewAgent to perform thorough, actionable code reviews.
Each review examines code from **four independent perspectives**, scores issues
by confidence, and filters low-confidence noise.

## Review Perspectives

### 1. Correctness & Bugs
- Syntax errors, type mismatches, missing imports, unresolved references
- Logic errors that produce wrong results regardless of input
- Off-by-one errors, null/undefined dereferences, race conditions
- Incorrect API usage or broken contracts

### 2. Security
- Injection vulnerabilities (SQL, command, path traversal)
- Hardcoded secrets, credentials, or tokens
- Missing input validation or sanitization
- Insecure defaults (e.g., disabled TLS, permissive CORS)

### 3. Performance
- O(n²) or worse algorithms where O(n) is possible
- Unbounded memory allocation or resource leaks
- Missing connection/resource cleanup
- N+1 query patterns or redundant I/O

### 4. Maintainability & Design
- Functions exceeding ~50 lines or doing multiple unrelated things
- Deep nesting (>3 levels) that hurts readability
- Duplicated logic that should be extracted
- Missing error handling or silent error swallowing

## Confidence Scoring

Each issue MUST be scored 0–100:

| Score | Meaning                     | Action       |
|-------|-----------------------------|--------------|
| 90-100| Certain — will definitely break | MUST FIX  |
| 70-89 | Highly likely — strong evidence | SHOULD FIX |
| 50-69 | Moderate — real but minor      | SUGGEST    |
| 0-49  | Low confidence — possibly false positive | SKIP |

**Threshold: only report issues with confidence ≥ 70.**

## False Positive Filter

Do NOT flag:
- Pre-existing issues not introduced in the current change
- Code style preferences (unless explicitly in project rules)
- Issues that linters or formatters will catch
- Subjective "I would do it differently" suggestions
- Working code that could theoretically be marginally improved

## Output Format

The review MUST end with this exact structured block:

```
REVIEW_SCORE: <0-100 overall quality score>
REVIEW_APPROVED: <true or false>
MUST_FIX:
- <critical issue 1, confidence ≥ 90>
- <critical issue 2, confidence ≥ 90>
SHOULD_FIX:
- <important issue 1, confidence 70-89>
- <important issue 2, confidence 70-89>
```

### Scoring Guidelines
- 90-100: Excellent — no critical issues, clean design
- 70-89:  Good — minor issues only, safe to ship
- 50-69:  Fair — some issues need attention before production
- 30-49:  Poor — significant problems found
- 0-29:   Critical — fundamental flaws, do not ship

### Approval Criteria
- APPROVED if score ≥ 70 AND no MUST_FIX items
- NOT APPROVED otherwise

## Review Template

```
## Code Review Summary

**Files reviewed**: <count>
**Overall assessment**: <one sentence>

### Critical Issues (MUST FIX)
1. **[Correctness]** <description> (confidence: <score>)
   - File: `<path>`, line ~<number>
   - Why: <explanation>
   - Fix: <concrete suggestion>

### Important Issues (SHOULD FIX)
1. **[Security]** <description> (confidence: <score>)
   - File: `<path>`, line ~<number>
   - Why: <explanation>
   - Fix: <concrete suggestion>

### What Was Done Well
- <positive observation 1>
- <positive observation 2>

REVIEW_SCORE: <score>
REVIEW_APPROVED: <true/false>
MUST_FIX:
- <issue>
SHOULD_FIX:
- <issue>
```
