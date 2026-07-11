#!/usr/bin/env python3
"""
Export requirements created by team members in a specific sprint.

Reads team config for creator IDs, fetches requirements with descriptions,
and exports to doc/requirements/ as individual markdown files.

Usage:
    python scripts/export_team_requirements.py --sprint-name "Sprint260728"
    python scripts/export_team_requirements.py --sprint-name "Sprint260728" --format json
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
    team_creator_ids,
    richtext_to_text,
    parse_description_envelope,
    object_name,
    custom_fields_to_dict,
    safe_filename_subject,
)

CONFIG_DIR = SKILL_DIR / "config"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "yunxiao_config.json"
EXAMPLE_CONFIG_PATH = CONFIG_DIR / "yunxiao_config.example.json"
OUTPUT_DIR = SKILL_DIR / "doc" / "requirements"


# ---------------------------------------------------------------------------
# Config and CLI
# ---------------------------------------------------------------------------

def build_config(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    config_file = Path(args.config) if args.config else DEFAULT_CONFIG_PATH
    file_config = load_config_file(config_file)
    cli_args = {
        "domain": args.domain,
        "token": args.token,
        "organization_id": args.organization_id,
        "project_id": args.project_id,
    }
    config = build_config_from_sources(file_config, cli_args)
    team_members = load_team(file_config)
    return config, team_members


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export team-created requirements from a sprint to doc/requirements/.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--domain", help=f"Yunxiao domain. Default: {DEFAULT_DOMAIN}")
    parser.add_argument("--token", help="Yunxiao personal access token.")
    parser.add_argument("--organization-id", help="Yunxiao organizationId.")
    parser.add_argument("--project-id", help="Yunxiao projectId.")
    parser.add_argument("--sprint-name", required=True, help="Sprint name (exact or keyword match).")
    parser.add_argument("--sprint-statuses", default=DEFAULT_SPRINT_STATUSES, help=f"Default: {DEFAULT_SPRINT_STATUSES}")
    parser.add_argument("--today", help="Override today, format YYYY-MM-DD.")
    parser.add_argument("--format", choices=["xlsx", "json"], default="xlsx", help="Output format. Default: xlsx (single Excel file)")
    parser.add_argument("--show-config-path", action="store_true")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def requirement_to_markdown(item: dict[str, Any], sprint_name: str) -> str:
    """Render a single requirement as a markdown document."""
    serial = item.get("serialNumber") or ""
    subject = item.get("subject") or ""
    status = object_name(item.get("status"))
    assigned = object_name(item.get("assignedTo"))
    creator = object_name(item.get("creator"))
    wtype = object_name(item.get("workitemType")) or item.get("categoryId") or ""
    desc_text = richtext_to_text(parse_description_envelope(item.get("description")))
    custom = custom_fields_to_dict(item)

    lines = [
        f"# {subject}",
        "",
        f"> **编号**: {serial} | **创建人**: {creator} | **负责人**: {assigned} | **状态**: {status} | **迭代**: {sprint_name}",
        "",
    ]

    if wtype:
        lines.append(f"**工作项类型**: {wtype}")
        lines.append("")

    if custom:
        lines.append("## 自定义字段")
        lines.append("")
        for k, v in custom.items():
            lines.append(f"- **{k}**: {v}")
        lines.append("")

    lines.append("## 描述")
    lines.append("")
    if desc_text.strip():
        lines.append(desc_text)
    else:
        lines.append("> (无描述)")
    lines.append("")

    return "\n".join(lines)


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

    config, team_members = build_config(args)
    client = YunxiaoClient(config)

    # Get all team creator IDs (non-exempt members)
    creator_ids = team_creator_ids(team_members)
    if not creator_ids:
        print("ERROR: No team members with yunxiao_id found. Run --dump-creators first.", file=sys.stderr)
        return 1

    print(f"Team creators: {len(creator_ids)} IDs", file=sys.stderr)

    # Resolve sprint by name
    today = dt.date.today()
    if args.today:
        today = dt.datetime.strptime(args.today, "%Y-%m-%d").date()

    sprints = client.list_sprints(config["project_id"], args.sprint_statuses)
    # Try exact match first, then keyword
    needle = args.sprint_name.lower()
    exact = [s for s in sprints if str(s.get("name") or s.get("displayName") or "").lower() == needle]
    if exact:
        sprint = exact[0]
        print(f"Sprint (exact match): {sprint.get('name')} -> {sprint.get('id')}", file=sys.stderr)
    else:
        keyword_matches = [s for s in sprints if needle in str(s.get("name") or s.get("displayName") or "").lower()]
        if len(keyword_matches) == 1:
            sprint = keyword_matches[0]
            print(f"Sprint (keyword match): {sprint.get('name')} -> {sprint.get('id')}", file=sys.stderr)
        elif len(keyword_matches) > 1:
            print(f"Multiple sprints match '{args.sprint_name}':", file=sys.stderr)
            for s in keyword_matches:
                print(f"  {s.get('name')} (id={s.get('id')}, status={s.get('status')})", file=sys.stderr)
            print("Use a more specific name.", file=sys.stderr)
            return 1
        else:
            print(f"ERROR: No sprint found matching '{args.sprint_name}'", file=sys.stderr)
            return 1

    sprint_id = str(sprint.get("id") or sprint.get("identifier") or "")
    sprint_name = str(sprint.get("name") or sprint.get("displayName") or sprint_id)
    if not sprint_id:
        print(f"ERROR: Cannot determine sprint id from sprint: {sprint}", file=sys.stderr)
        return 1

    # Build creator filter
    creator_conditions = [{
        "fieldIdentifier": "creator",
        "operator": "CONTAINS",
        "value": creator_ids,
        "toValue": None,
        "className": "user",
        "format": "list",
    }] if creator_ids else []

    # Fetch requirements
    print(f"Fetching requirements for sprint '{sprint_name}' filtered by {len(creator_ids)} team creators...", file=sys.stderr)
    items = client.fetch_all_workitems(
        project_id=config["project_id"],
        sprint_id=sprint_id,
        category="Req",
        extra_conditions=creator_conditions,
    )
    print(f"Found {len(items)} requirements.", file=sys.stderr)

    if not items:
        print("No requirements found. Nothing to export.", file=sys.stderr)
        return 0

    # Enrich with descriptions
    print("Fetching descriptions...", file=sys.stderr)
    client.enrich_with_descriptions(items)

    # Create output directory
    safe_sprint_name = re.sub(r"[^\w.-]+", "_", sprint_name)
    sprint_output_dir = OUTPUT_DIR / safe_sprint_name
    sprint_output_dir.mkdir(parents=True, exist_ok=True)

    # Export
    if args.format == "json":
        # Single JSON file with all requirements
        output_path = sprint_output_dir / f"{safe_sprint_name}_requirements.json"
        shaped = [client.shape_requirement(it, sprint_name) for it in items]
        with output_path.open("w", encoding="utf-8") as f:
            json.dump({"sprint": sprint_name, "sprintId": sprint_id, "count": len(shaped), "requirements": shaped}, f, ensure_ascii=False, indent=2)
        print(f"\nExported {len(shaped)} requirements to {output_path.resolve()}")
    else:
        # Single Excel file with all requirements
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
        from yunxiao_client import discover_headers, column_value, STANDARD_FIELD_BY_HEADER

        STANDARD_HEADERS = ["编号", "标题", "描述", "创建人", "负责人", "状态", "工作项类型"]

        # Build headers: standard + auto-discovered custom fields
        headers = discover_headers(items, STANDARD_HEADERS)

        # Build rows
        rows: list[list[str]] = []
        for item in items:
            row: list[str] = []
            for header in headers:
                if header == "创建人":
                    row.append(object_name(item.get("creator")))
                elif header in STANDARD_FIELD_BY_HEADER:
                    row.append(column_value(item, header))
                else:
                    row.append(column_value(item, header))
            rows.append(row)

        # Metadata
        metadata = {
            "导出时间": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "组织ID": config["organization_id"],
            "项目ID": config["project_id"],
            "迭代": sprint_name,
            "迭代ID": sprint_id,
            "需求数": str(len(items)),
            "筛选条件": f"创建人 = 团队成员 ({len(creator_ids)} 人)",
        }

        # Write Excel
        output_path = sprint_output_dir / f"{safe_sprint_name}_requirements.xlsx"
        workbook = Workbook()

        # Data sheet
        sheet = workbook.active
        sheet.title = "需求列表"

        # Title row
        sheet.append([f"{sprint_name} - 团队需求列表"])
        sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
        sheet["A1"].font = Font(bold=True, size=14)
        sheet["A1"].alignment = Alignment(horizontal="center")

        # Metadata rows
        for key, value in metadata.items():
            sheet.append([key, value])

        # Header row
        header_row = sheet.max_row + 2
        for col, header in enumerate(headers, start=1):
            cell = sheet.cell(row=header_row, column=col, value=header)
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="D9EAF7")
            cell.alignment = Alignment(horizontal="center", vertical="center")

        # Data rows
        for row in rows:
            sheet.append(row)

        # Column widths
        col_widths = {
            "编号": 16, "标题": 40, "描述": 70, "创建人": 12,
            "负责人": 12, "状态": 12, "工作项类型": 18,
        }
        for col_idx, header in enumerate(headers, start=1):
            width = col_widths.get(header, 20)
            sheet.column_dimensions[get_column_letter(col_idx)].width = width

        # Wrap text on data rows
        for row in sheet.iter_rows(min_row=header_row + 1):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)

        # Freeze + filter
        sheet.freeze_panes = sheet.cell(row=header_row + 1, column=1)
        sheet.auto_filter.ref = f"A{header_row}:{get_column_letter(len(headers))}{max(header_row, sheet.max_row)}"

        workbook.save(output_path)
        print(f"\nExported {len(items)} requirements to {output_path.resolve()}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
