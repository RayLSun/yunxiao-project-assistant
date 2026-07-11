#!/usr/bin/env python3
"""
Auto-create tasks to fill workload gaps for team members.

For each member in task_creation.members_to_fill whose current sprint
workload is below required hours, find requirements where they are a
participant, create child tasks under those requirements until the
workload gap is filled.

Task names include a role prefix (e.g. 【后端】).
Estimated hours per task are chosen from available_hours [2, 4, 8].

Usage:
    python scripts/create_tasks.py --sprint-name "Sprint260728"
    python scripts/create_tasks.py --sprint-name "Sprint260728" --dry-run
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

# Add lib/ to path
SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SKILL_DIR / "lib"))

from yunxiao_client import (
    DEFAULT_DOMAIN,
    DEFAULT_SPRINT_STATUSES,
    YunxiaoClient,
    build_config_from_sources,
    first_real,
    load_config_file,
    load_team,
    resolve_sprint,
    object_name,
)

CONFIG_DIR = SKILL_DIR / "config"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "yunxiao_config.json"
EXAMPLE_CONFIG_PATH = CONFIG_DIR / "yunxiao_config.example.json"


# ---------------------------------------------------------------------------
# Config and CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto-create tasks to fill workload gaps.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--domain", help=f"Yunxiao domain. Default: {DEFAULT_DOMAIN}")
    parser.add_argument("--token", help="Yunxiao personal access token.")
    parser.add_argument("--organization-id", help="Yunxiao organizationId.")
    parser.add_argument("--project-id", help="Yunxiao projectId.")
    parser.add_argument("--sprint-name", required=True, help="Sprint name (exact or keyword match).")
    parser.add_argument("--sprint-statuses", default=DEFAULT_SPRINT_STATUSES)
    parser.add_argument("--today", help="Override today, format YYYY-MM-DD.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be created without actually creating tasks.")
    parser.add_argument("--show-config-path", action="store_true")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_hours(item: dict[str, Any], field_name: str = "预计工时") -> float:
    for cf in item.get("customFieldValues") or []:
        if cf.get("fieldName") == field_name:
            vals = cf.get("values") or []
            if vals:
                v = vals[0]
                if isinstance(v, dict):
                    h = v.get("displayValue") or v.get("identifier")
                else:
                    h = v
                try:
                    return float(str(h).strip())
                except (ValueError, TypeError):
                    pass
    return 0.0


def extract_assignee_id(item: dict[str, Any]) -> str:
    assigned = item.get("assignedTo")
    if isinstance(assigned, dict):
        return str(assigned.get("id") or assigned.get("identifier") or "")
    return ""


def get_participant_ids(req: dict[str, Any]) -> set[str]:
    """Extract participant user IDs from a requirement."""
    ids: set[str] = set()
    participants = req.get("participants")
    if isinstance(participants, list):
        for p in participants:
            if isinstance(p, dict):
                uid = str(p.get("id") or p.get("identifier") or "")
                if uid:
                    ids.add(uid)
    return ids


def pick_task_hours(remaining: float, available_hours: list[int]) -> int:
    """Pick the largest available hour value that fits the remaining gap.
    If remaining > max, return max. If remaining < min, return min.
    """
    if remaining <= 0:
        return 0
    sorted_hours = sorted(available_hours, reverse=True)
    for h in sorted_hours:
        if h <= remaining:
            return h
    return sorted_hours[-1]  # smallest available


def resolve_sprint_by_name(client: YunxiaoClient, project_id: str, sprint_name: str, statuses: str) -> dict[str, Any]:
    sprints = client.list_sprints(project_id, statuses)
    needle = sprint_name.lower()
    exact = [s for s in sprints if str(s.get("name") or s.get("displayName") or "").lower() == needle]
    if exact:
        return exact[0]
    matches = [s for s in sprints if needle in str(s.get("name") or s.get("displayName") or "").lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = [str(s.get("name") or s.get("displayName") or "") for s in matches]
        raise RuntimeError(f"Multiple sprints match '{sprint_name}': {', '.join(names)}")
    raise RuntimeError(f"No sprint found matching '{sprint_name}'")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()

    if args.show_config_path:
        config_file = Path(args.config) if args.config else DEFAULT_CONFIG_PATH
        print(f"Config file: {config_file}")
        print(f"Exists:      {'yes' if config_file.exists() else 'no'}")
        return 0

    config_file = Path(args.config) if args.config else DEFAULT_CONFIG_PATH
    file_config = load_config_file(config_file)

    cli_args = {
        "domain": args.domain,
        "token": args.token,
        "organization_id": args.organization_id,
        "project_id": args.project_id,
    }
    config = build_config_from_sources(file_config, cli_args)
    client = YunxiaoClient(config)
    team_members = load_team(file_config)

    # Load task_creation config
    tc_config = file_config.get("task_creation") or {}
    workitem_type_id = tc_config.get("workitem_type_id", "")
    hours_field_id = tc_config.get("hours_field_id", "")
    available_hours: list[int] = tc_config.get("available_hours", [2, 4, 8])
    role_prefixes: dict[str, str] = tc_config.get("role_prefixes", {})
    members_to_fill: list[str] = tc_config.get("members_to_fill", [])

    if not workitem_type_id or not hours_field_id:
        print("ERROR: task_creation.workitem_type_id and hours_field_id must be set in config.", file=sys.stderr)
        return 1
    if not members_to_fill:
        print("ERROR: task_creation.members_to_fill is empty. Add member names to fill.", file=sys.stderr)
        return 1

    # Build team lookup by name
    team_by_name: dict[str, dict[str, Any]] = {m["name"]: m for m in team_members}
    team_by_id: dict[str, dict[str, Any]] = {m["yunxiao_id"]: m for m in team_members if m["yunxiao_id"]}

    # Resolve members_to_fill to member dicts
    fill_members: list[dict[str, Any]] = []
    for name in members_to_fill:
        m = team_by_name.get(name)
        if not m:
            print(f"WARNING: '{name}' not found in team.members, skipping.", file=sys.stderr)
            continue
        if not m["yunxiao_id"]:
            print(f"WARNING: '{name}' has no yunxiao_id, skipping.", file=sys.stderr)
            continue
        if m["exempt"]:
            print(f"INFO: '{name}' is exempt (产品), skipping.", file=sys.stderr)
            continue
        fill_members.append(m)

    if not fill_members:
        print("ERROR: No valid members to fill.", file=sys.stderr)
        return 1

    print(f"Members to fill: {', '.join(m['name'] for m in fill_members)}", file=sys.stderr)

    # Resolve sprint
    sprint = resolve_sprint_by_name(client, config["project_id"], args.sprint_name, args.sprint_statuses)
    sprint_id = str(sprint.get("id") or sprint.get("identifier") or "")
    sprint_name = str(sprint.get("name") or sprint.get("displayName") or sprint_id)
    if not sprint_id:
        print(f"ERROR: Cannot determine sprint id.", file=sys.stderr)
        return 1
    print(f"Sprint: {sprint_name} ({sprint_id})", file=sys.stderr)

    # Fetch requirements in this sprint
    print("Fetching requirements...", file=sys.stderr)
    reqs = client.fetch_all_workitems(config["project_id"], sprint_id, "Req")
    print(f"  {len(reqs)} requirements found.", file=sys.stderr)

    # Fetch tasks in this sprint
    print("Fetching tasks...", file=sys.stderr)
    tasks = client.fetch_all_workitems(config["project_id"], sprint_id, "Task")
    print(f"  {len(tasks)} tasks found.", file=sys.stderr)

    # Extract task custom field IDs from an existing task (for inheritance filter)
    task_field_ids: set[str] = set()
    if tasks:
        for cf in tasks[0].get("customFieldValues") or []:
            fid = cf.get("fieldId") or ""
            if fid and fid != "sumPlanedLaborHour":
                task_field_ids.add(fid)
    print(f"  Task custom field IDs for inheritance: {task_field_ids}", file=sys.stderr)

    # Build req_id -> req lookup
    req_by_id: dict[str, dict[str, Any]] = {str(r.get("id") or ""): r for r in reqs}

    # Calculate current hours per team member (from tasks whose parent is a req in this sprint)
    current_hours: dict[str, float] = {}  # yunxiao_id -> hours
    for t in tasks:
        pid = str(t.get("parentId") or "")
        if pid not in req_by_id:
            continue
        uid = extract_assignee_id(t)
        if uid not in team_by_id:
            continue
        current_hours[uid] = current_hours.get(uid, 0.0) + extract_hours(t)

    # For each member to fill, calculate the gap
    print(f"\n{'Name':<8} {'Role':<12} {'Current':<8} {'Required':<8} {'Gap':<8}", file=sys.stderr)
    print("-" * 50, file=sys.stderr)

    fill_plan: list[dict[str, Any]] = []
    for m in fill_members:
        uid = m["yunxiao_id"]
        name = m["name"]
        role = m["role"]
        current = current_hours.get(uid, 0.0)
        required = m["required_hours"]
        gap = required - current

        print(f"{name:<8} {role:<12} {current:<8.0f} {required:<8} {gap:<8.0f}", file=sys.stderr)

        if gap <= 0:
            print(f"  -> {name} already meets workload, skipping.", file=sys.stderr)
            continue

        # Find requirements where this person is a participant
        candidate_reqs: list[dict[str, Any]] = []
        for r in reqs:
            participants = get_participant_ids(r)
            if uid in participants:
                candidate_reqs.append(r)

        if not candidate_reqs:
            # Fallback: find requirements assigned to this person
            for r in reqs:
                if extract_assignee_id(r) == uid:
                    candidate_reqs.append(r)

        if not candidate_reqs:
            print(f"  WARNING: No requirements found where {name} is participant or assignee. Cannot create tasks.", file=sys.stderr)
            continue

        # Plan task creation: greedily fill the gap
        remaining = gap
        task_plans: list[dict[str, Any]] = []
        req_index = 0

        while remaining > 0 and candidate_reqs:
            req = candidate_reqs[req_index % len(candidate_reqs)]
            req_id = str(req.get("id") or "")
            req_serial = str(req.get("serialNumber") or "")
            req_subject = str(req.get("subject") or "")

            hours = pick_task_hours(remaining, available_hours)
            if hours <= 0:
                break

            prefix = role_prefixes.get(role, f"【{role}】")
            base_name = f"{prefix}{req_subject[:40]}"
            # Count existing tasks under this requirement that share the same base_name
            existing_same_base = 0
            for t in tasks:
                if str(t.get("parentId") or "") != req_id:
                    continue
                t_subject = str(t.get("subject") or "")
                if t_subject == base_name or t_subject.startswith(base_name + "-"):
                    existing_same_base += 1
            # Also count tasks already in this plan for the same requirement
            planned_same_base = sum(
                1 for tp in task_plans
                if tp["req_id"] == req_id and (tp["task_subject"] == base_name or tp["task_subject"].startswith(base_name + "-"))
            )
            total_same_base = existing_same_base + planned_same_base
            if total_same_base == 0:
                task_subject = base_name
            else:
                task_subject = f"{base_name}-{total_same_base + 1}"

            task_plans.append({
                "req_id": req_id,
                "req_serial": req_serial,
                "req_subject": req_subject,
                "task_subject": task_subject,
                "hours": hours,
                "assigned_to": uid,
                "assigned_to_name": name,
                "role": role,
            })

            remaining -= hours
            req_index += 1  # rotate to next requirement

        if task_plans:
            fill_plan.extend(task_plans)
            print(f"  -> Will create {len(task_plans)} tasks totaling {sum(tp['hours'] for tp in task_plans)}h", file=sys.stderr)
        else:
            print(f"  -> Could not plan any tasks for {name}", file=sys.stderr)

    if not fill_plan:
        print("\nNo tasks need to be created. All members meet workload requirements.", file=sys.stderr)
        return 0

    # Show plan
    print(f"\n=== Task Creation Plan ({'DRY RUN' if args.dry_run else 'LIVE'}) ===\n")
    print(f"{'Member':<8} {'Role':<12} {'Hours':<6} {'Task Subject':<50} {'Parent Req'}")
    print("-" * 120)
    for tp in fill_plan:
        print(f"{tp['assigned_to_name']:<8} {tp['role']:<12} {tp['hours']:<6} {tp['task_subject'][:50]:<50} {tp['req_serial']}")
    total_hours = sum(tp["hours"] for tp in fill_plan)
    total_tasks = len(fill_plan)
    print(f"\nTotal: {total_tasks} tasks, {total_hours}h")

    if args.dry_run:
        print("\n--dry-run: no tasks created. Remove --dry-run to execute.")
        return 0

    # Execute: create tasks
    print("\nCreating tasks...", file=sys.stderr)
    created: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    for tp in fill_plan:
        try:
            # Inherit custom fields from parent requirement
            parent_req = req_by_id.get(tp["req_id"], {})
            custom_values: dict[str, str] = {}
            for cf in parent_req.get("customFieldValues") or []:
                fid = cf.get("fieldId") or ""
                if fid in task_field_ids:
                    vals = cf.get("values") or []
                    if vals:
                        v = vals[0]
                        if isinstance(v, dict):
                            ident = str(v.get("identifier") or v.get("displayValue") or "")
                        else:
                            ident = str(v)
                        if ident:
                            custom_values[fid] = ident
            # Set hours
            custom_values[hours_field_id] = str(tp["hours"])

            result = client.create_workitem(
                subject=tp["task_subject"],
                assigned_to=tp["assigned_to"],
                parent_id=tp["req_id"],
                space_id=config["project_id"],
                workitem_type_id=workitem_type_id,
                sprint_id=sprint_id,
                custom_field_values=custom_values,
            )
            new_id = str(result.get("id") or result.get("identifier") or "")
            new_serial = str(result.get("serialNumber") or "")

            if new_id:
                created.append({
                    "serialNumber": new_serial,
                    "id": new_id,
                    "subject": tp["task_subject"],
                    "assignee": tp["assigned_to_name"],
                    "hours": tp["hours"],
                    "parent": tp["req_serial"],
                })
                print(f"  Created: {new_serial} | {tp['task_subject'][:50]} | {tp['hours']}h | {tp['assigned_to_name']}", file=sys.stderr)
            else:
                failed.append({"plan": tp, "error": "No ID in response"})
                print(f"  FAILED: {tp['task_subject'][:50]} - no ID returned", file=sys.stderr)
        except Exception as e:
            failed.append({"plan": tp, "error": str(e)})
            print(f"  FAILED: {tp['task_subject'][:50]} - {e}", file=sys.stderr)

    # Summary
    print(f"\n=== Result ===")
    print(f"Created: {len(created)} tasks ({sum(c['hours'] for c in created)}h)")
    if failed:
        print(f"Failed:  {len(failed)} tasks")
        for f in failed:
            print(f"  {f['plan']['task_subject'][:50]} - {f['error']}")

    if created:
        print("\nCreated tasks:")
        for c in created:
            print(f"  {c['serialNumber']} | {c['subject'][:50]} | {c['hours']}h | {c['assignee']} | parent={c['parent']}")

    return 0 if not failed else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
