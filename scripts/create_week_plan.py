#!/usr/bin/env python3
"""Build one whole work week (Mon-Fri, 8h/day, 40h) of tasks under a requirement.

Workflow this encodes (孙磊 / SME-LMAV 口径):
  input  = 需求 id + 下周主要任务内容（自由文本）
  output = 5 天、每天合计 8h 的任务，全部挂在需求下

Week layout (fixed, do not renegotiate per run):

  周一 8h  可变  从需求内容拆
  周二 8h  可变  从需求内容拆
  周三 8h  可变  从需求内容拆
  周四 4h  可变  + 4h 固定「功能联调」
  周五 8h  固定「PRD评审&技术评审&演示与发布」

So the variable part is exactly 28h and the script enforces it.

Plan shape (only the VARIABLE blocks are supplied; the two fixed blocks are
injected by the script):

[
  {"weekday": 0, "hours": 8, "title": "MCP 公共接入层与连接适配",
   "items": [{"work": "连接适配层与统一鉴权", "output": "可复用接入层"}]},
  {"weekday": 0, "hours": 4, ...},              # same day may be split 4+4
  {"weekday": 3, "hours": 4, "title": "...", "desc": "<p>...</p>"}
]

`weekday` is 0=周一 .. 3=周四 (4=周五 is fully fixed and rejected); a
`date` (YYYY-MM-DD) is also accepted. Per block: `title`, `hours` in {4,8},
and either `desc` (HTML or plain text) or `items` for a bullet description.

Usage:
  python scripts/create_week_plan.py --parent LMAV-343 --assignee 孙磊 \
      --monday 2026-11-16 --plan-file week.json --dry-run

  python scripts/create_week_plan.py --parent LMAV-343 --assignee 孙磊 \
      --monday 2026-11-16 --plan-file week.json      # real create + read-back verify

Exit codes: 0 ok, 2 validation/config error (nothing created).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SKILL_DIR / "lib"))

from yunxiao_client import (  # noqa: E402
    DEFAULT_DOMAIN,
    YunxiaoClient,
    build_config_from_sources,
    load_config_file,
)

CONFIG_DIR = SKILL_DIR / "config"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "yunxiao_config.json"

DEFAULT_TASK_TYPE_ID = "ba102e46bc6a8483d9b7f25c"
DEFAULT_HOURS_FIELD = "101586"
DEFAULT_DATE_START_FIELD = "79"
DEFAULT_DATE_END_FIELD = "80"

DAY_HOURS = 8
WORKDAYS = 5
VARIABLE_HOURS = {0: 8, 1: 8, 2: 8, 3: 4}   # 周五 4 完全固定
FIXED_HOURS = {3: 4, 4: 8}
DAY_NAMES = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五"}

FIXED_TASKS: dict[int, dict[str, Any]] = {
    3: {
        "title": "功能联调",
        "hours": 4,
        "desc": (
            "<p>## 背景</p><p>本迭代功能模块开发完成后，前后端与外部依赖的联调窗口。</p>"
            "<p>## 改动点</p>"
            "<p>- 接口联调：按 PRD 与接口文档逐接口对齐入参、出参、错误码。<br/>"
            "- 端到端：主链路跑通，异常/超时/无权限分支逐一验证。<br/>"
            "- 问题闭环：联调中发现的问题当天记录并同步责任人。</p>"
            "<p>## 验收</p><p>主链路在测试环境完整跑通，遗留问题有清单与责任人。</p>"
        ),
    },
    4: {
        "title": "PRD评审&技术评审&演示与发布",
        "hours": 8,
        "desc": (
            "<p>## 背景</p><p>固定周五例行：本迭代验收 + 下个迭代方向确认 + 对外演示与发布。</p>"
            "<p>## 改动点</p>"
            "<p>- PRD 评审：下个迭代需求逐条过，明确范围、优先级与待澄清项。<br/>"
            "- 技术评审：方案、接口、数据模型与风险评估。<br/>"
            "- 演示与发布：本迭代成果演示，发布计划与回滚方案确认。</p>"
            "<p>## 验收</p><p>评审结论与会后行动项落纸；演示无阻断问题；发布窗口确认。</p>"
        ),
    },
}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Create a full 5-day, 8h/day task week under a requirement.")
    p.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    p.add_argument("--domain", help=f"Yunxiao domain. Default: {DEFAULT_DOMAIN}")
    p.add_argument("--token")
    p.add_argument("--organization-id")
    p.add_argument("--project-id")

    p.add_argument("--parent", required=True, help="Parent requirement id or serialNumber (e.g. LMAV-343).")
    p.add_argument("--assignee", default="孙磊", help="Assignee NAME. Default: 孙磊")
    p.add_argument("--monday", help="Monday of the target week (YYYY-MM-DD). Default: next Monday.")
    p.add_argument("--sprint", default="auto",
                   help="'auto' = the sprint whose date range covers --monday (default); "
                        "or an explicit sprint id / name keyword.")

    p.add_argument("--plan-file", help="JSON file with the VARIABLE blocks.")
    p.add_argument("--plan-json", help="Inline JSON with the VARIABLE blocks.")
    p.add_argument("--dry-run", action="store_true", help="Validate + print the plan; create nothing.")

    p.add_argument("--task-type-id", default=DEFAULT_TASK_TYPE_ID)
    p.add_argument("--hours-field", default=DEFAULT_HOURS_FIELD)
    p.add_argument("--date-start-field", default=DEFAULT_DATE_START_FIELD)
    p.add_argument("--date-end-field", default=DEFAULT_DATE_END_FIELD)
    return p.parse_args()


def build_config(args: argparse.Namespace) -> dict[str, Any]:
    file_config = load_config_file(Path(args.config)) if args.config else {}
    return build_config_from_sources(file_config, {
        "domain": args.domain,
        "token": args.token,
        "organization_id": args.organization_id,
        "project_id": args.project_id,
    })


# ---------------------------------------------------------------------------
# Resolvers
# ---------------------------------------------------------------------------

def next_monday(today: dt.date | None = None) -> dt.date:
    today = today or dt.date.today()
    return today + dt.timedelta(days=(7 - today.weekday()) % 7 or 7)


def resolve_parent(client: YunxiaoClient, project_id: str, ref: str) -> dict[str, Any]:
    ref = ref.strip()
    if "-" in ref and not ref.isdigit():
        for cat in ("Req", "Task", "Bug"):
            for page in range(1, 6):
                items = client.search_workitems(project_id=project_id, category=cat, page=page, per_page=100)
                if not items:
                    break
                for it in items:
                    if str(it.get("serialNumber", "")).upper() == ref.upper():
                        return {"id": it["id"], "serialNumber": it.get("serialNumber"),
                                "subject": it.get("subject")}
        raise SystemExit(f"ERROR: parent '{ref}' not found in project {project_id}.")
    detail = client.fetch_workitem_detail(ref)
    return {"id": detail["id"], "serialNumber": detail.get("serialNumber"), "subject": detail.get("subject")}


def _day(ms: Any) -> dt.date | None:
    try:
        return dt.datetime.utcfromtimestamp(int(ms) / 1000).date()
    except Exception:
        return None


def resolve_sprint(client: YunxiaoClient, ref: str, monday: dt.date) -> dict[str, Any]:
    sprints = client.list_sprints()
    if ref and ref != "auto":
        for s in sprints:
            if s.get("id") == ref:
                return s
        for s in sprints:
            if ref in str(s.get("name", "")):
                return s
        raise SystemExit(f"ERROR: sprint '{ref}' not found.")
    for s in sprints:
        start, end = _day(s.get("startDate")), _day(s.get("endDate"))
        if start and end and start <= monday <= end:
            return s
    names = ", ".join(f"{s.get('name')}({_day(s.get('startDate'))}~{_day(s.get('endDate'))})" for s in sprints)
    raise SystemExit(f"ERROR: no sprint covers {monday}. Existing: {names}. Pass --sprint explicitly.")


def resolve_assignee(client: YunxiaoClient, config_file: dict[str, Any], name: str) -> str:
    for m in (config_file.get("team") or {}).get("members") or []:
        if m.get("name") == name and m.get("yunxiao_id"):
            return m["yunxiao_id"]
    project_id = client.config["project_id"]
    seen: dict[str, str] = {}
    for cat in ("Req", "Task"):
        for page in range(1, 6):
            items = client.search_workitems(project_id=project_id, category=cat, page=page, per_page=100)
            if not items:
                break
            for it in items:
                for key in ("creator", "assignedTo"):
                    u = it.get(key) or {}
                    if u.get("name"):
                        seen.setdefault(u["name"], u.get("id", ""))
    if seen.get(name):
        return seen[name]
    raise SystemExit(f"ERROR: assignee '{name}' not found in team.members nor a live scan.")


# ---------------------------------------------------------------------------
# Plan building + validation
# ---------------------------------------------------------------------------

def load_plan(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.plan_file:
        raw = Path(args.plan_file).read_text(encoding="utf-8")
    elif args.plan_json:
        raw = args.plan_json
    else:
        raw = sys.stdin.read()
    plan = json.loads(raw)
    if not isinstance(plan, list):
        raise SystemExit("ERROR: plan must be a JSON array of variable blocks.")
    return plan


def normalize(plan: list[dict[str, Any]], monday: dt.date) -> list[dict[str, Any]]:
    """Attach a concrete date/weekday to every block and coerce hours to int."""
    out: list[dict[str, Any]] = []
    for b in plan:
        date = b.get("date")
        if date:
            d = dt.date.fromisoformat(str(date))
            weekday = (d - monday).days
        else:
            weekday = int(b.get("weekday", -1))
            d = monday + dt.timedelta(days=weekday)
        hours = int(str(b.get("hours", "")).rstrip("h") or 0)
        out.append({**b, "weekday": weekday, "date": d.isoformat(), "hours": hours})
    return out


def validate(blocks: list[dict[str, Any]]) -> None:
    errors: list[str] = []
    per_day: dict[int, int] = {i: 0 for i in range(WORKDAYS)}
    for b in blocks:
        wd, hours, title = b["weekday"], b["hours"], str(b.get("title", "")).strip()
        if wd not in VARIABLE_HOURS:
            errors.append(f"周五 / 越界日期不可放可变任务：{b['date']} {title}")
            continue
        if hours not in (4, 8):
            errors.append(f"{DAY_NAMES[wd]}「{title}」工时 {hours} 不合法：每块必须是 4 或 8")
        if not title:
            errors.append(f"{b['date']} 有一条任务缺标题")
        per_day[wd] += hours
    for wd, need in VARIABLE_HOURS.items():
        if per_day[wd] != need:
            errors.append(f"{DAY_NAMES[wd]} 可变工时 {per_day[wd]}h ≠ 要求 {need}h")
    fixed_total = sum(FIXED_HOURS.values())
    variable_total = sum(per_day.values())
    if variable_total + fixed_total != DAY_HOURS * WORKDAYS:
        errors.append(f"全周合计 {variable_total + fixed_total}h ≠ 40h")
    if errors:
        raise SystemExit("PLAN INVALID:\n  - " + "\n  - ".join(errors))


def to_description(block: dict[str, Any]) -> str:
    desc = block.get("desc")
    if desc:
        desc = str(desc)
        return desc if desc.lstrip().startswith("<") else f"<p>{desc}</p>"
    items = block.get("items") or []
    if not items:
        return f"<p>{block.get('title', '')}</p>"
    parts = []
    for i, it in enumerate(items, 1):
        work = it.get("work", "")
        output = it.get("output", "")
        parts.append(f"<p>{i}. {work}" + (f" → {output}" if output else "") + "</p>")
    return "".join(parts)


def build_week(blocks: list[dict[str, Any]], monday: dt.date) -> list[dict[str, Any]]:
    """Return the full ordered week: variable blocks first, then the fixed slot."""
    week: list[dict[str, Any]] = []
    for wd in range(WORKDAYS):
        day_blocks = sorted([b for b in blocks if b["weekday"] == wd], key=lambda b: -b["hours"])
        for b in day_blocks:
            week.append({**b, "kind": "variable", "desc_html": to_description(b)})
        if wd in FIXED_TASKS:
            f = FIXED_TASKS[wd]
            week.append({
                "weekday": wd,
                "date": (monday + dt.timedelta(days=wd)).isoformat(),
                "title": f["title"],
                "hours": f["hours"],
                "kind": "fixed",
                "desc_html": f["desc"],
            })
    return week


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()
    config = build_config(args)
    client = YunxiaoClient(config)
    project_id = config["project_id"]

    config_file = load_config_file(Path(args.config)) if args.config else {}
    monday = dt.date.fromisoformat(args.monday) if args.monday else next_monday()
    if monday.weekday() != 0:
        raise SystemExit(f"ERROR: --monday must be a Monday, got {monday} ({DAY_NAMES.get(monday.weekday(), '?')}).")

    blocks = normalize(load_plan(args), monday)
    validate(blocks)
    week = build_week(blocks, monday)

    parent = resolve_parent(client, project_id, args.parent)
    sprint = resolve_sprint(client, args.sprint, monday)
    assignee_id = resolve_assignee(client, config_file, args.assignee)

    print(f"需求: {parent.get('serialNumber')} {parent.get('subject')}")
    print(f"周次: {monday} ~ {monday + dt.timedelta(days=4)}  迭代: {sprint.get('name')} ({sprint.get('id')})")
    print(f"负责人: {args.assignee}\n")
    print("| 日期 | 任务 | 工时 | 类型 |")
    print("|---|---|---|---|")
    for t in week:
        print(f"| {t['date']} {DAY_NAMES[t['weekday']]} | {t['title']} | {t['hours']}h | "
              f"{'固定' if t['kind'] == 'fixed' else '拆解'} |")
    total = sum(t["hours"] for t in week)
    print(f"\n合计 {total}h（可变 {total - sum(FIXED_HOURS.values())}h + 固定 {sum(FIXED_HOURS.values())}h）\n")
    if total != DAY_HOURS * WORKDAYS:
        raise SystemExit("ERROR: internal total mismatch, aborting before any write.")

    # idempotency: same parent + same subject -> skip
    existing: set[str] = set()
    try:
        for it in client.fetch_all_workitems(category="Task", sprint_id="", project_id=project_id):
            if it.get("parentId") == parent["id"]:
                existing.add(str(it.get("subject", "")))
    except Exception:
        pass

    created: list[dict[str, Any]] = []
    for t in week:
        title = t["title"]
        if title in existing:
            print(f"SKIP (已存在): {t['date']} {title}")
            created.append({"skipped": True, "title": title})
            continue
        custom = {
            args.hours_field: str(t["hours"]),
            args.date_start_field: t["date"],
            args.date_end_field: t["date"],
        }
        if args.dry_run:
            print(f"DRY-RUN: {t['date']} {DAY_NAMES[t['weekday']]} {title} {t['hours']}h")
            created.append({"title": title, "dry": True})
            continue
        res = client.create_workitem(
            subject=title,
            assigned_to=assignee_id,
            parent_id=parent["id"],
            space_id=project_id,
            workitem_type_id=args.task_type_id,
            sprint_id=str(sprint.get("id") or ""),
            description=t["desc_html"],
            custom_field_values=custom,
        )
        wid = (res or {}).get("id")
        if not wid:
            print(f"FAILED {title}: {str(res)[:200]}")
            continue
        detail = client.fetch_workitem_detail(wid)
        cf = {}
        for f in detail.get("customFieldValues") or []:
            vals = f.get("values") or []
            cf[f.get("fieldName")] = str(vals[0].get("displayValue")) if vals else ""
        checks = {
            "subject": detail.get("subject") == title,
            "assignee": (detail.get("assignedTo") or {}).get("name") == args.assignee,
            "parent": detail.get("parentId") == parent["id"],
            "hours": cf.get("预计工时") == str(t["hours"]),
            "date": cf.get("计划开始时间", "").startswith(t["date"]),
            "sprint": (detail.get("sprint") or {}).get("id") == sprint.get("id"),
        }
        ok = all(checks.values())
        print(("OK " if ok else "MISMATCH " + json.dumps(checks, ensure_ascii=False)) +
              f"{detail.get('serialNumber')} {t['date']} {title} {t['hours']}h")
        created.append({"id": wid, "serialNumber": detail.get("serialNumber"), "title": title})
        time.sleep(0.3)

    real = sum(1 for c in created if c.get("id"))
    print(f"\nDone. 计划 {len(week)} 条，创建并校验 {real} 条，跳过 {len(week) - real - sum(1 for c in created if c.get('dry'))} 条"
          + ("（dry-run 未写入）" if args.dry_run else ""))


if __name__ == "__main__":
    main()
