#!/usr/bin/env python3
"""Batch-create Yunxiao daily tasks under a parent requirement.

Built for the "paste a day-by-day work plan into tasks" workflow: the agent
summarizes each day's work into a short title (<=15 chars recommended), puts
the detailed items into the description, and sets 预计工时 / 计划开始时间 /
计划完成时间 per day.

Defaults follow the LMAV (SME) project conventions discovered in practice:
- task type ba102e46bc6a8483d9b7f25c, sprint auto-resolved by keyword
- title style 【MM-DD】关键词 (matches sibling tasks like 【10-19】需求对齐与AI契约定义)
- description is simple HTML (<p> lines) — the API accepts it for formatType RICHTEXT
- hours field 101586, planned start/end date fields 79/80 (auto-discovered from
  a sibling task unless overridden)

Permission note: creation hits POST /oapi/v1/projex/organizations/{org}/workitems.
A token without workitem WRITE scope fails with HTTP 403
{"errorCode":"Forbidden","errorMessage":"Current token has no permission to api."}
— read-only tokens work for every search/detail API but not here; the user must
enable the write scope on the personal access token.
"""
from __future__ import annotations

import argparse
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

# LMAV/SME defaults; override via CLI flags for other projects.
DEFAULT_TASK_TYPE_ID = "ba102e46bc6a8483d9b7f25c"
DEFAULT_HOURS_FIELD = "101586"
DEFAULT_DATE_START_FIELD = "79"
DEFAULT_DATE_END_FIELD = "80"
DEFAULT_HOURS = "8"


# ---------------------------------------------------------------------------
# Config and CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch-create daily tasks under a Yunxiao parent requirement.",
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH),
                        help=f"Config JSON path. Default: {DEFAULT_CONFIG_PATH}")
    parser.add_argument("--domain", help=f"Yunxiao domain. Default: {DEFAULT_DOMAIN}")
    parser.add_argument("--token", help="Yunxiao personal access token. Prefer YUNXIAO_TOKEN.")
    parser.add_argument("--organization-id", help="Yunxiao organizationId.")
    parser.add_argument("--project-id", help="Yunxiao projectId. Default: config value.")

    parser.add_argument("--parent", required=False, default="",
                        help="Parent workitem id or serialNumber (e.g. LMAV-73).")
    parser.add_argument("--sprint", default="",
                        help="Sprint id or name keyword to resolve (e.g. 1023).")
    parser.add_argument("--assignee", default="",
                        help="Assignee NAME (resolved via team.members or --dump-people). Default: first non-exempt member.")

    parser.add_argument("--plan-file",
                        help="JSON plan file: [{date, title, items:[{hours, work, output}]}]. "
                             "Mutually exclusive with inline --plan-json.")
    parser.add_argument("--plan-json",
                        help="Inline JSON plan with the same shape as --plan-file.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be created without creating.")

    parser.add_argument("--task-type-id", default=DEFAULT_TASK_TYPE_ID,
                        help=f"Task workitem type id. Default: {DEFAULT_TASK_TYPE_ID}")
    parser.add_argument("--hours", default=DEFAULT_HOURS,
                        help=f"预计工时 value for every task. Default: {DEFAULT_HOURS}")
    parser.add_argument("--hours-field", default=DEFAULT_HOURS_FIELD)
    parser.add_argument("--date-start-field", default=DEFAULT_DATE_START_FIELD)
    parser.add_argument("--date-end-field", default=DEFAULT_DATE_END_FIELD)
    parser.add_argument("--title-prefix", default="【{MMDD}】",
                        help="Title template; {MMDD} is replaced by the MM-DD from the plan date. Use '' to disable.")
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> dict[str, Any]:
    file_config = load_config_file(Path(args.config)) if args.config else {}
    cli_args = {
        "domain": args.domain,
        "token": args.token,
        "organization_id": args.organization_id,
        "project_id": args.project_id,
    }
    return build_config_from_sources(file_config, cli_args)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def resolve_parent(client: YunxiaoClient, project_id: str, parent_ref: str) -> dict[str, Any]:
    """Resolve --parent by serialNumber (prefix-aware, like get_requirement.py) or raw id."""
    ref = parent_ref.strip()
    if "-" in ref and not ref.isdigit():
        prefix, num = ref.split("-", 1)
        for cat in ("Req", "Task", "Bug"):
            for page in range(1, 6):
                items = client.search_workitems(project_id=project_id, category=cat, page=page, per_page=100)
                if not items:
                    break
                for it in items:
                    if str(it.get("serialNumber", "")).upper() == ref.upper():
                        return {"id": it["id"], "serialNumber": it.get("serialNumber"), "subject": it.get("subject")}
        raise SystemExit(f"ERROR: parent workitem '{ref}' not found in project {project_id} (tried Req/Task/Bug).")
    # raw id path: verify it exists
    detail = client.fetch_workitem_detail(ref)
    return {"id": detail["id"], "serialNumber": detail.get("serialNumber"), "subject": detail.get("subject")}


def resolve_sprint_id(client: YunxiaoClient, project_id: str, sprint_ref: str) -> str:
    """Accept a sprint id directly, or resolve a name keyword by scanning sibling tasks."""
    if not sprint_ref:
        return ""
    # if any task in the project carries this exact sprint id, accept it
    items = client.fetch_all_workitems(category="Task", sprint_id="", project_id=project_id)
    for it in items:
        sp = it.get("sprint") or {}
        if sp.get("id") == sprint_ref or sp.get("name") == sprint_ref:
            return sp["id"]
    # keyword match on name
    for it in items:
        sp = it.get("sprint") or {}
        if sprint_ref in str(sp.get("name", "")):
            return sp["id"]
    raise SystemExit(f"ERROR: sprint '{sprint_ref}' not found among tasks in project {project_id}.")


def resolve_assignee_id(client: YunxiaoClient, config_file: dict[str, Any], name: str) -> str:
    """Resolve an assignee NAME to a yunxiao_id via team.members, falling back to a live scan."""
    members = (config_file.get("team") or {}).get("members") or []
    for m in members:
        if m.get("name") == name and m.get("yunxiao_id"):
            return m["yunxiao_id"]
    # live scan: requirement creators + task assignees
    seen: dict[str, str] = {}
    project_id = client.config["project_id"]
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
    if name in seen and seen[name]:
        return seen[name]
    raise SystemExit(f"ERROR: assignee '{name}' not found in team.members nor in a live scan of the project.")


def discover_field_ids(client: YunxiaoClient, project_id: str, task_type_id: str) -> dict[str, str]:
    """Discover 预计工时/计划开始/计划结束 field ids from an existing task (LMAV-specific ids may differ per org)."""
    try:
        items = client.fetch_all_workitems(category="Task", sprint_id="", project_id=project_id)
        for it in items:
            if (it.get("workitemType") or {}).get("id") != task_type_id:
                continue
            detail = client.fetch_workitem_detail(it["id"])
            found: dict[str, str] = {}
            for f in detail.get("customFieldValues", []):
                fname = f.get("fieldName", "")
                fid = f.get("fieldId", "")
                if fname == "预计工时":
                    found["hours"] = fid
                elif fname == "计划开始时间":
                    found["start"] = fid
                elif fname == "计划完成时间":
                    found["end"] = fid
            if {"hours", "start", "end"} <= found.keys():
                return found
    except Exception:
        pass
    return {}


def build_description(date: str, items: list[dict[str, Any]]) -> str:
    parts = [f"<p><b>{date}（预计 8h）</b></p>"]
    for i, item in enumerate(items, 1):
        hours = item.get("hours", "")
        work = item.get("work", "")
        output = item.get("output", "")
        prefix = f"【{hours}】" if hours else ""
        suffix = f" → {output}" if output else ""
        parts.append(f"<p>{i}. {prefix}{work}{suffix}</p>")
    return "".join(parts)


def build_title(prefix_tpl: str, date: str, title: str) -> str:
    if not prefix_tpl:
        return title
    mmdd = date[5:] if len(date) >= 10 and date[4] == "-" else date
    return prefix_tpl.replace("{MMDD}", mmdd) + title


def validate_plan(plan: list[dict[str, Any]]) -> None:
    if not plan:
        raise SystemExit("ERROR: empty plan.")
    for entry in plan:
        for key in ("date", "title"):
            if not entry.get(key):
                raise SystemExit(f"ERROR: plan entry missing '{key}': {entry}")
        if not entry.get("items"):
            raise SystemExit(f"ERROR: plan entry missing 'items': {entry['title']}")


def load_plan(args: argparse.Namespace) -> list[dict[str, Any]]:
    raw = ""
    if args.plan_file:
        raw = Path(args.plan_file).read_text(encoding="utf-8")
    elif args.plan_json:
        raw = args.plan_json
    else:
        raw = sys.stdin.read()
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()
    config = build_config(args)
    client = YunxiaoClient(config)
    project_id = config["project_id"]

    config_file = load_config_file(Path(args.config)) if args.config else {}
    plan = load_plan(args)
    validate_plan(plan)

    parent = resolve_parent(client, project_id, args.parent)
    sprint_id = resolve_sprint_id(client, project_id, args.sprint)
    assignee_id = resolve_assignee_id(client, config_file, args.assignee) if args.assignee else ""
    if not assignee_id:
        raise SystemExit("ERROR: --assignee is required (a NAME resolvable via team.members or a live scan).")

    discovered = discover_field_ids(client, project_id, args.task_type_id)
    hours_field = args.hours_field or discovered.get("hours", "")
    start_field = args.date_start_field or discovered.get("start", "")
    end_field = args.date_end_field or discovered.get("end", "")

    # Guard: existing tasks under the same parent with the same date prefix -> skip (idempotent re-runs)
    existing_subjects: set[str] = set()
    try:
        for it in client.fetch_all_workitems(category="Task", sprint_id="", project_id=project_id):
            if it.get("parentId") == parent["id"]:
                existing_subjects.add(str(it.get("subject", "")))
    except Exception:
        pass

    created: list[dict[str, Any]] = []
    for entry in plan:
        date = entry["date"]
        title = build_title(args.title_prefix, date, entry["title"])
        desc = build_description(date, entry["items"])
        if title in existing_subjects:
            print(f"SKIP (already exists): {title}")
            continue
        custom_fields: dict[str, str] = {}
        if hours_field:
            custom_fields[hours_field] = str(args.hours)
        if start_field:
            custom_fields[start_field] = date
        if end_field:
            custom_fields[end_field] = date
        payload = {
            "date": date,
            "title": title,
            "assignee_id": assignee_id,
            "sprint_id": sprint_id,
            "custom_fields": custom_fields,
        }
        if args.dry_run:
            print(f"DRY-RUN create: {title} @ {date} (sprint={sprint_id or '(inherit)'})")
            created.append(payload)
            continue
        result = client.create_workitem(
            subject=title,
            assigned_to=assignee_id,
            parent_id=parent["id"],
            space_id=project_id,
            workitem_type_id=args.task_type_id,
            sprint_id=sprint_id,
            description=desc,
            custom_field_values=custom_fields or None,
        )
        wid = result.get("id")
        # verify by reading back
        detail = client.fetch_workitem_detail(wid)
        cf = {f.get("fieldName"): (f.get("values") or [{}])[0].get("displayValue") if f.get("values") else None
              for f in detail.get("customFieldValues", [])}
        checks = {
            "subject": detail.get("subject") == title,
            "assignee": (detail.get("assignedTo") or {}).get("name") == args.assignee,
            "parent": detail.get("parentId") == parent["id"],
            "hours": str(cf.get("预计工时")) == str(args.hours),
            "date": str(cf.get("计划开始时间", "")).startswith(date),
        }
        ok = all(checks.values())
        status = "OK" if ok else "MISMATCH " + json.dumps(checks, ensure_ascii=False)
        url = f"https://devops.aliyun.com/projex/project/{project_id}/task/{wid}"
        print(f"{status} {detail.get('serialNumber', '')} {title} | url {url}")
        created.append({"id": wid, "serialNumber": detail.get("serialNumber"), "title": title, "url": url})
        time.sleep(0.3)

    print(f"\nDone. plan={len(plan)} created/verified={sum(1 for c in created if c.get('id'))} skipped={len(plan) - len(created)}")


if __name__ == "__main__":
    main()
