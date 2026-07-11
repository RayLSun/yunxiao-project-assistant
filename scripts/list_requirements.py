#!/usr/bin/env python3
"""
List requirements from a Yunxiao project, filtered by creator and/or sprint.

Uses the shared yunxiao_client library. Output is JSON on stdout (or a
formatted table with --format table).

Common usage:
    # List all requirements in a project created by specific users
    python scripts/list_requirements.py --creator-id abc123 --creator-id def456

    # List requirements in a specific sprint by a creator
    python scripts/list_requirements.py --sprint-id 06175757e51bfd3f2aa47f3e60 --creator-id abc123

    # List by sprint keyword
    python scripts/list_requirements.py --sprint-keyword "招聘" --creator-id abc123

    # List ALL requirements in the project (no creator filter)
    python scripts/list_requirements.py

    # Filter by team role (uses config team.members)
    python scripts/list_requirements.py --role 后端研发

    # Filter by all team members (uses config team.members)
    python scripts/list_requirements.py --team

    # Show as table instead of JSON
    python scripts/list_requirements.py --format table

    # Show team roster with workload expectations
    python scripts/list_requirements.py --show-team

Note on creator IDs:
    Yunxiao uses internal user identifiers (not usernames) in the creator
    filter. To discover creator IDs, run with --dump-creators to list
    unique creators found in the project's workitems. Then fill in
    team.members[].yunxiao_id in config/yunxiao_config.json.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
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
    DEFAULT_SEARCH_STATUSES,
    YunxiaoClient,
    build_config_from_sources,
    first_real,
    load_config_file,
    load_team,
    resolve_sprint,
    team_creator_ids,
    team_creator_ids_by_role,
    team_members_by_role,
)

CONFIG_DIR = SKILL_DIR / "config"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "yunxiao_config.json"
EXAMPLE_CONFIG_PATH = CONFIG_DIR / "yunxiao_config.example.json"


# ---------------------------------------------------------------------------
# Config and CLI
# ---------------------------------------------------------------------------

def build_config(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]], Path]:
    """Return (api_config, team_members, config_file_path) tuple."""
    config_file = Path(args.config) if args.config else DEFAULT_CONFIG_PATH
    file_config = load_config_file(config_file)

    cli_args = {
        "domain": args.domain,
        "token": args.token,
        "organization_id": args.organization_id,
        "project_id": args.project_id,
        "sprint_id": args.sprint_id,
        "sprint_keyword": args.sprint_keyword,
    }
    config = build_config_from_sources(file_config, cli_args)
    config["_config_file_path"] = str(config_file)
    team_members = load_team(file_config)
    return config, team_members, config_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List Yunxiao requirements filtered by creator and/or sprint.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help=f"Config JSON path. Default: {DEFAULT_CONFIG_PATH}")
    parser.add_argument("--domain", help=f"Yunxiao domain. Default: {DEFAULT_DOMAIN}")
    parser.add_argument("--token", help="Yunxiao personal access token. Prefer YUNXIAO_TOKEN.")
    parser.add_argument("--organization-id", help="Yunxiao organizationId.")
    parser.add_argument("--project-id", help="Yunxiao projectId.")
    parser.add_argument("--sprint-id", help="Direct sprint id to filter by.")
    parser.add_argument("--sprint-keyword", help="Keyword to filter sprint names (contains match).")
    parser.add_argument("--sprint-statuses", default=DEFAULT_SPRINT_STATUSES, help=f"Comma-separated sprint statuses. Default: {DEFAULT_SPRINT_STATUSES}")
    parser.add_argument("--today", help="Override today for date-based sprint selection, format YYYY-MM-DD.")
    parser.add_argument(
        "--creator-id",
        action="append",
        default=[],
        help="Creator user ID to filter by. Can be specified multiple times.",
    )
    parser.add_argument("--role", help="Filter by team role (e.g. 后端研发, 前端研发, 产品, RPA研发). Uses config team.members.")
    parser.add_argument("--team", action="store_true", help="Filter by all team members in config. Shorthand for all yunxiao_ids.")
    parser.add_argument("--category", default="Req", choices=["Req", "Task", "Bug", "Risk"], help="Workitem category. Default: Req")
    parser.add_argument("--max-results", type=int, default=200, help="Cap result count. Default: 200")
    parser.add_argument("--format", choices=["json", "table"], default="json", help="Output format. Default: json")
    parser.add_argument("--dump-creators", action="store_true", help="List unique creators found in workitems and exit.")
    parser.add_argument("--show-team", action="store_true", help="Print the team roster with workload expectations and exit.")
    parser.add_argument("--show-config-path", action="store_true", help="Print the absolute config file path and exit.")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Creator condition builder
# ---------------------------------------------------------------------------

def build_creator_conditions(creator_ids: list[str]) -> list[dict[str, Any]]:
    if not creator_ids:
        return []
    return [{
        "fieldIdentifier": "creator",
        "operator": "CONTAINS",
        "value": creator_ids,
        "toValue": None,
        "className": "user",
        "format": "list",
    }]


# ---------------------------------------------------------------------------
# Dump creators (discovery mode)
# ---------------------------------------------------------------------------

def _extract_people(items: list[dict[str, Any]], field: str) -> dict[str, str]:
    """Extract unique {id: name} from a person field (creator/assignedTo) across items."""
    people: dict[str, str] = {}
    for item in items:
        person = item.get(field)
        if isinstance(person, dict):
            uid = str(person.get("id") or person.get("identifier") or "")
            name = str(person.get("displayName") or person.get("name") or "")
            if uid:
                people[uid] = name or uid
        elif isinstance(person, list):
            for p in person:
                if isinstance(p, dict):
                    uid = str(p.get("id") or p.get("identifier") or "")
                    name = str(p.get("displayName") or p.get("name") or "")
                    if uid:
                        people[uid] = name or uid
        elif person and str(person).strip():
            people[str(person)] = str(person)
    return people


def dump_creators(client: YunxiaoClient, config: dict[str, Any], team_members: list[dict[str, Any]] | None = None) -> int:
    """Scan requirements (creators) and tasks (assignees) to discover user IDs.

    Developers often don't create requirements, so we also scan task assignees
    to find everyone. If team_members is provided, auto-match names and write
    yunxiao_id back to config.
    """
    import json as _json
    from yunxiao_client import team_member_by_name

    # Fetch all requirements for creators
    print("Fetching all requirements...", file=sys.stderr)
    req_items = client.fetch_all_workitems(
        project_id=config["project_id"],
        sprint_id="",
        category="Req",
    )
    print(f"  {len(req_items)} requirements found.", file=sys.stderr)

    # Fetch all tasks for assignees
    print("Fetching all tasks...", file=sys.stderr)
    task_items = client.fetch_all_workitems(
        project_id=config["project_id"],
        sprint_id="",
        category="Task",
    )
    print(f"  {len(task_items)} tasks found.", file=sys.stderr)

    creators = _extract_people(req_items, "creator")
    assignees = _extract_people(task_items, "assignedTo")

    # Merge: assignees may include people who never created requirements
    all_people: dict[str, str] = {}
    all_people.update(creators)
    all_people.update(assignees)

    if not all_people:
        print("No people found in requirements or tasks.")
        return 0

    print(f"\nFound {len(all_people)} unique people "
          f"({len(creators)} creators + {len(assignees)} assignees):\n")
    print(f"{'User ID':<50} {'Name':<12} {'Found via'}")
    print("-" * 80)
    for uid, name in sorted(all_people.items(), key=lambda x: x[1]):
        via = []
        if uid in creators:
            via.append("creator")
        if uid in assignees:
            via.append("assignee")
        print(f"{uid:<50} {name:<12} {', '.join(via)}")

    # Auto-match team members if provided
    if team_members:
        unmatched = [m for m in team_members if not m["yunxiao_id"].strip()]
        if not unmatched:
            print("\nAll team members already have yunxiao_id. No matching needed.")
            return 0

        print(f"\n=== Auto-matching {len(unmatched)} team member(s) without yunxiao_id ===")
        config_path = config.get("_config_file_path")
        matched_count = 0
        still_unmatched = []

        for m in team_members:
            if m["yunxiao_id"].strip():
                continue
            name = m["name"]
            if name in all_people.values():
                # Find by exact name match
                for uid, cname in all_people.items():
                    if cname == name:
                        m["yunxiao_id"] = uid
                        matched_count += 1
                        print(f"  matched: {name} -> {uid}")
                        break
            else:
                still_unmatched.append(name)

        if matched_count > 0 and config_path:
            # Write back to config file
            with open(config_path, "r", encoding="utf-8") as f:
                fc = _json.load(f)
            for m_in_config in fc.get("team", {}).get("members", []):
                for m_in_team in team_members:
                    if m_in_config.get("name") == m_in_team["name"]:
                        m_in_config["yunxiao_id"] = m_in_team["yunxiao_id"]
            with open(config_path, "w", encoding="utf-8") as f:
                _json.dump(fc, f, ensure_ascii=False, indent=2)
                f.write("\n")
            print(f"\nWrote {matched_count} yunxiao_id(s) back to config.")

        if still_unmatched:
            print(f"\nStill unmatched: {', '.join(still_unmatched)}")
            print("Check if their names in config match Yunxiao display names exactly.")

    return 0


# ---------------------------------------------------------------------------
# Show team roster
# ---------------------------------------------------------------------------

def show_team(team_members: list[dict[str, Any]]) -> int:
    if not team_members:
        print("No team members configured. Add members to config/yunxiao_config.json under team.members.")
        return 0

    print(f"Team roster ({len(team_members)} members):\n")
    print(f"{'Name':<12} {'Role':<12} {'TL':<4} {'Exempt':<7} {'Hours':<6} {'Yunxiao ID'}")
    print("-" * 75)
    for m in team_members:
        name = m["name"] or "(empty)"
        role = m["role"] or "(empty)"
        tl = "Yes" if m["is_tl"] else ""
        exempt = "Yes" if m["exempt"] else ""
        hours = str(m["required_hours"])
        yid = m["yunxiao_id"] or "(not set)"
        print(f"{name:<12} {role:<12} {tl:<4} {exempt:<7} {hours:<6} {yid}")

    missing_ids = [m["name"] for m in team_members if not m["yunxiao_id"].strip()]
    if missing_ids:
        print(f"\nWarning: {len(missing_ids)} member(s) missing yunxiao_id: {', '.join(missing_ids)}")
        print("Run --dump-creators to discover IDs, then fill in config.")

    configured = [m for m in team_members if m["yunxiao_id"].strip()]
    if configured:
        print(f"\nConfigured creator IDs: {', '.join(m['yunxiao_id'] for m in configured)}")
    return 0


# ---------------------------------------------------------------------------
# Table rendering
# ---------------------------------------------------------------------------

def render_table(items: list[dict[str, Any]]) -> None:
    headers = ["编号", "标题", "状态", "负责人", "创建人", "迭代", "创建时间"]
    rows: list[list[str]] = []
    for item in items:
        serial = str(item.get("serialNumber") or "")
        subject = str(item.get("subject") or "")
        status_raw = item.get("status")
        status = status_raw.get("displayName") if isinstance(status_raw, dict) else str(status_raw or "")
        assigned_raw = item.get("assignedTo")
        assigned = assigned_raw.get("displayName") if isinstance(assigned_raw, dict) else str(assigned_raw or "")
        creator_raw = item.get("creator")
        creator = creator_raw.get("displayName") if isinstance(creator_raw, dict) else str(creator_raw or "")
        sprint_raw = item.get("sprint")
        sprint = sprint_raw.get("displayName") if isinstance(sprint_raw, dict) else str(sprint_raw or "")
        gmt = item.get("gmtCreate")
        if isinstance(gmt, (int, float)) and gmt > 0:
            gmt_str = dt.datetime.fromtimestamp(gmt / 1000).strftime("%Y-%m-%d %H:%M")
        else:
            gmt_str = ""
        rows.append([serial, subject, status, assigned, creator, sprint, gmt_str])

    widths = []
    for col in range(len(headers)):
        w = len(headers[col])
        for row in rows:
            w = max(w, len(row[col]))
        widths.append(min(w + 2, 60))

    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(line)
    print("-" * len(line))

    for row in rows:
        print("  ".join(row[i].ljust(widths[i]) for i in range(len(headers))))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()

    if args.show_config_path:
        config_file = Path(args.config) if args.config else DEFAULT_CONFIG_PATH
        print(f"Config file: {config_file}")
        print(f"Exists:      {'yes' if config_file.exists() else 'no (copy from example below)'}")
        if EXAMPLE_CONFIG_PATH.exists():
            print(f"Template:    {EXAMPLE_CONFIG_PATH}")
            print(f"Bootstrap:   cp {EXAMPLE_CONFIG_PATH} {config_file}")
        return 0

    config, team_members, _ = build_config(args)
    client = YunxiaoClient(config)

    if args.show_team:
        return show_team(team_members)

    if args.dump_creators:
        return dump_creators(client, config, team_members)

    # Resolve sprint if needed
    sprint_id = config.get("sprint_id") or ""
    sprint_name = ""

    if args.sprint_id:
        sprint_id = args.sprint_id
        sprint_name = args.sprint_id
    elif args.sprint_keyword:
        today = dt.date.today()
        if args.today:
            today = dt.datetime.strptime(args.today, "%Y-%m-%d").date()
        sprints = client.list_sprints(config["project_id"], args.sprint_statuses)
        sprint = resolve_sprint(sprints, args.sprint_keyword, today)
        sprint_id = str(sprint.get("id") or sprint.get("identifier") or "")
        sprint_name = str(sprint.get("name") or sprint.get("displayName") or sprint_id)
        if not sprint_id:
            raise RuntimeError(f"Cannot determine sprint id from sprint: {sprint}")
        print(f"Sprint: {sprint_name} ({sprint_id})", file=sys.stderr)

    # Resolve creator IDs from --creator-id, --role, --team
    creator_ids: list[str] = list(args.creator_id)

    if args.role:
        role_ids = team_creator_ids_by_role(team_members, args.role)
        if not role_ids:
            print(f"Warning: no team members with yunxiao_id found for role '{args.role}'", file=sys.stderr)
        creator_ids.extend(role_ids)
        print(f"Filtering by role '{args.role}': {len(role_ids)} creator(s)", file=sys.stderr)

    if args.team:
        all_team_ids = team_creator_ids(team_members)
        creator_ids.extend(all_team_ids)
        print(f"Filtering by all team members: {len(all_team_ids)} creator(s)", file=sys.stderr)

    # Deduplicate
    seen = set()
    creator_ids = [x for x in creator_ids if not (x in seen or seen.add(x))]

    # Build creator filter conditions
    creator_conditions = build_creator_conditions(creator_ids)
    if creator_ids:
        print(f"Total creator IDs to filter: {len(creator_ids)}", file=sys.stderr)

    # Fetch workitems
    items = client.fetch_all_workitems(
        project_id=config["project_id"],
        sprint_id=sprint_id,
        category=args.category,
        extra_conditions=creator_conditions,
    )

    # Cap results
    items = items[: args.max_results]
    print(f"Found {len(items)} {args.category} items.", file=sys.stderr)

    if not items:
        if args.format == "json":
            json.dump({"requirements": [], "count": 0}, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            print("No items found.")
        return 0

    if args.format == "json":
        client.enrich_with_descriptions(items)
        shaped = [client.shape_requirement(it, sprint_name or None) for it in items]
        json.dump({"requirements": shaped, "count": len(shaped)}, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        render_table(items)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
