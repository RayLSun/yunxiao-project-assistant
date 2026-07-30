#!/usr/bin/env python3
"""
Create a requirement in Yunxiao Projex.

Takes title, description, and optionally assignee/type from CLI args,
calls the Yunxiao API to create the requirement, then fetches the
serialNumber and prints the web URL.

Usage:
    # Minimal (defaults: 技术类需求, 孙磊 as assignee)
    python scripts/create_requirement.py --title "需求标题" --description "需求描述"

    # Specify type and assignee
    python scripts/create_requirement.py --title "标题" --description "描述" --type 技术 --assignee "孙磊"

    # List available types and assignees
    python scripts/create_requirement.py --show-types
    python scripts/create_requirement.py --show-assignees

Output (JSON on stdout):
    {"id": "...", "serialNumber": "HWTQ-2873", "url": "https://devops.aliyun.com/...", ...}
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SKILL_DIR / "lib"))

from yunxiao_client import (
    DEFAULT_DOMAIN,
    YunxiaoClient,
    build_config_from_sources,
    first_real,
    load_config_file,
    load_team,
)

CONFIG_DIR = SKILL_DIR / "config"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "yunxiao_config.json"

# ---------------------------------------------------------------------------
# Requirement type definitions (discovered from existing requirements)
# ---------------------------------------------------------------------------

# Maps short keyword -> full type info
REQ_TYPE_MAP: dict[str, dict[str, str]] = {
    "技术": {
        "workitem_type_id": "bca48ee2a0976d38f4802fae",
        "type_name": "技术类需求",
        "required_fields": {
            "6084d274899a5942faac8ee8dc": "/",        # 里程碑
            "54f7fa7e4ba9e64a2e405a86ca": "AI-招聘助理", # 业务系统
            "574fb04effab3ce1f171524ab3": "DEC",        # 成本分摊部门
        },
    },
    "业务": {
        "workitem_type_id": "b759681e33af77f5c8620cbc",
        "type_name": "业务类需求",
        "required_fields": {
            "6084d274899a5942faac8ee8dc": "/",          # 里程碑
            "54f7fa7e4ba9e64a2e405a86ca": "AI-招聘助理", # 业务系统
            "574fb04effab3ce1f171524ab3": "WFS",        # 成本分摊部门
            "38f661ed375902ce6dc0312124": "/",          # 2026预算编号
        },
    },
}

DEFAULT_TYPE_KEYWORD = "业务"

# Field IDs for common fields
FIELD_MILESTONE = "6084d274899a5942faac8ee8dc"
FIELD_BIZ_SYSTEM = "54f7fa7e4ba9e64a2e405a86ca"
FIELD_COST_DEPT = "574fb04effab3ce1f171524ab3"
FIELD_BUDGET_2026 = "38f661ed375902ce6dc0312124"

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a requirement in Yunxiao Projex.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--domain", help=f"Yunxiao domain. Default: {DEFAULT_DOMAIN}")
    parser.add_argument("--token", help="Yunxiao personal access token.")
    parser.add_argument("--organization-id", help="Yunxiao organizationId.")
    parser.add_argument("--project-id", help="Yunxiao projectId.")
    parser.add_argument("--title", required=False, help="Requirement title (subject).")
    parser.add_argument("--description", default="", help="Requirement description (rich text).")
    parser.add_argument("--type", default=DEFAULT_TYPE_KEYWORD,
                        help=f"Requirement type keyword. Choices: {', '.join(REQ_TYPE_MAP.keys())}. Default: {DEFAULT_TYPE_KEYWORD}")
    parser.add_argument("--assignee", default="", help="Assignee name (must match team.members[].name).")
    parser.add_argument("--milestone", default="/", help="里程碑 value. Default: /")
    parser.add_argument("--biz-system", default="AI-招聘助理", help="业务系统 value. Default: AI-招聘助理")
    parser.add_argument("--cost-dept", default="", help="成本分摊部门. Default: DEC(技术) or WFS(业务)")
    parser.add_argument("--budget-2026", default="/", help="2026预算编号. Default: /")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be created without creating.")
    parser.add_argument("--show-types", action="store_true", help="Show available requirement types and exit.")
    parser.add_argument("--show-assignees", action="store_true", help="Show team members and exit.")
    parser.add_argument("--show-config-path", action="store_true")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def resolve_type(keyword: str) -> dict[str, str]:
    """Resolve a type keyword to type info dict."""
    for k, v in REQ_TYPE_MAP.items():
        if keyword in k or k in keyword:
            return v
    raise RuntimeError(
        f"Unknown requirement type: '{keyword}'. "
        f"Available: {', '.join(REQ_TYPE_MAP.keys())}"
    )


def resolve_assignee(team_members: list[dict[str, Any]], name: str) -> str:
    """Resolve assignee name to yunxiao_id. Returns empty string if not found."""
    needle = name.strip().lower()
    for m in team_members:
        if m["name"].strip().lower() == needle:
            return m["yunxiao_id"]
    return ""


def build_url(project_id: str, workitem_id: str) -> str:
    return f"https://devops.aliyun.com/projex/project/{project_id}/task/{workitem_id}"


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

    if args.show_types:
        print("Available requirement types:")
        for k, v in REQ_TYPE_MAP.items():
            fields = ", ".join(v["required_fields"].keys())
            print(f"  {k}: type_id={v['workitem_type_id']}, name={v['type_name']}, required_fields=[{fields}]")
        return 0

    # Load config early for --show-assignees
    config_file = Path(args.config) if args.config else DEFAULT_CONFIG_PATH
    file_config = load_config_file(config_file)
    cli_args = {
        "domain": args.domain,
        "token": args.token,
        "organization_id": args.organization_id,
        "project_id": args.project_id,
    }
    config = build_config_from_sources(file_config, cli_args)

    if args.show_assignees:
        team_members = load_team(file_config)
        print("Team members (name -> yunxiao_id):")
        for m in team_members:
            print(f"  {m['name']} ({m['role']}) -> {m['yunxiao_id'] or '(empty)'}")
        return 0

    # Validate required args
    if not args.title:
        print("ERROR: --title is required.", file=sys.stderr)
        return 1

    client = YunxiaoClient(config)
    team_members = load_team(file_config)

    # Resolve type
    type_info = resolve_type(args.type)
    workitem_type_id = type_info["workitem_type_id"]
    type_name = type_info["type_name"]

    # Resolve assignee
    assignee_name = args.assignee.strip()
    if assignee_name:
        assignee_id = resolve_assignee(team_members, assignee_name)
        if not assignee_id:
            print(f"ERROR: Assignee '{assignee_name}' not found in team config.", file=sys.stderr)
            print("Use --show-assignees to see available names.", file=sys.stderr)
            return 1
    else:
        # Default to first non-exempt member with yunxiao_id
        assignee_id = ""
        for m in team_members:
            if not m["exempt"] and m["yunxiao_id"]:
                assignee_id = m["yunxiao_id"]
                assignee_name = m["name"]
                break
        if not assignee_id:
            print("ERROR: No assignee specified and no default found in team config.", file=sys.stderr)
            return 1

    # Build custom field values
    custom_fields: dict[str, str] = {
        FIELD_MILESTONE: args.milestone,
        FIELD_BIZ_SYSTEM: args.biz_system,
    }

    # 成本分摊部门: use --cost-dept if given, else use type default
    if args.cost_dept:
        custom_fields[FIELD_COST_DEPT] = args.cost_dept
    else:
        # Use default from type info
        default_dept = type_info["required_fields"].get(FIELD_COST_DEPT, "/")
        custom_fields[FIELD_COST_DEPT] = default_dept

    # 2026预算编号: only required for 业务类需求
    if FIELD_BUDGET_2026 in type_info.get("required_fields", {}):
        custom_fields[FIELD_BUDGET_2026] = args.budget_2026

    # Show plan
    print(f"=== Create Requirement ({'DRY RUN' if args.dry_run else 'LIVE'}) ===", file=sys.stderr)
    print(f"  Title:       {args.title}", file=sys.stderr)
    print(f"  Type:        {type_name}", file=sys.stderr)
    print(f"  Assignee:    {assignee_name} ({assignee_id})", file=sys.stderr)
    print(f"  Description: {args.description[:100]}{'...' if len(args.description) > 100 else ''}", file=sys.stderr)
    print(f"  Fields:      {json.dumps(custom_fields, ensure_ascii=False)}", file=sys.stderr)

    if args.dry_run:
        print("\n--dry-run: no requirement created. Remove --dry-run to execute.", file=sys.stderr)
        print(json.dumps({"dry_run": True, "title": args.title, "type": type_name, "assignee": assignee_name}, ensure_ascii=False))
        return 0

    # Execute: create requirement
    print("\nCreating requirement...", file=sys.stderr)
    try:
        result = client.create_workitem(
            subject=args.title,
            assigned_to=assignee_id,
            parent_id="",  # no parent for requirements
            space_id=config["project_id"],
            workitem_type_id=workitem_type_id,
            description=args.description,
            custom_field_values=custom_fields,
        )
    except Exception as e:
        print(f"ERROR: Failed to create requirement: {e}", file=sys.stderr)
        return 1

    # Extract workitem ID from response
    new_id = str(result.get("id") or result.get("identifier") or "")
    if not new_id:
        print(f"ERROR: No ID returned from API. Response: {json.dumps(result, ensure_ascii=False)[:500]}", file=sys.stderr)
        return 1

    # Fetch detail to get serialNumber
    print(f"  Created with ID: {new_id}", file=sys.stderr)
    try:
        detail = client.fetch_workitem_detail(new_id)
        serial = str(detail.get("serialNumber") or "")
        subject = str(detail.get("subject") or args.title)
    except Exception as e:
        print(f"  WARNING: Failed to fetch detail for serialNumber: {e}", file=sys.stderr)
        serial = ""
        subject = args.title

    url = build_url(config["project_id"], new_id)

    output = {
        "id": new_id,
        "serialNumber": serial,
        "subject": subject,
        "type": type_name,
        "assignee": assignee_name,
        "url": url,
    }

    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
