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
from yunxiao_client import discover_headers, column_value, STANDARD_FIELD_BY_HEADER, filter_excluded_statuses

CONFIG_DIR = SKILL_DIR / "config"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "yunxiao_config.json"
EXAMPLE_CONFIG_PATH = CONFIG_DIR / "yunxiao_config.example.json"
OUTPUT_DIR = SKILL_DIR / "doc" / "requirements"


# ---------------------------------------------------------------------------
# Config and CLI
# ---------------------------------------------------------------------------

def build_config(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
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
    return config, team_members, file_config


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

    config, team_members, file_config = build_config(args)
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
    items = filter_excluded_statuses(items)
    print(f"After filtering excluded statuses: {len(items)} requirements.", file=sys.stderr)

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
        # Single Excel file with all requirements + child tasks + workload summary
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter

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

        # --- Fetch child tasks for exported requirements ---
        req_ids: set[str] = {str(it.get("id") or "") for it in items}
        req_by_id: dict[str, dict[str, Any]] = {str(it.get("id") or ""): it for it in items}

        print("Fetching child tasks for exported requirements...", file=sys.stderr)
        # Fetch ALL tasks in project (not sprint-filtered) so we don't miss
        # tasks whose sprint differs from their parent requirement's sprint
        all_tasks = client.fetch_all_workitems(config["project_id"], "", "Task")
        all_tasks = filter_excluded_statuses(all_tasks)
        child_tasks = [t for t in all_tasks if str(t.get("parentId") or "") in req_ids]
        print(f"  {len(child_tasks)} child tasks found.", file=sys.stderr)

        # Extract hours helper
        def _extract_hours(task_item: dict[str, Any]) -> float:
            for cf in task_item.get("customFieldValues") or []:
                if cf.get("fieldName") == "预计工时":
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

        # Build name -> role lookup from team config + external members
        role_by_name: dict[str, str] = {
            m["name"]: m["role"] for m in team_members if m["name"]
        }
        # Merge external members (跨组帮忙的研发)
        team_config = file_config.get("team") or {}
        for em in team_config.get("external_members") or []:
            if isinstance(em, dict) and em.get("name"):
                role_by_name[em["name"]] = em.get("role", "")

        # Build task rows and per-person aggregation
        task_rows: list[list[str]] = []
        hours_by_person: dict[str, dict[str, Any]] = {}

        for t in child_tasks:
            pid = str(t.get("parentId") or "")
            parent = req_by_id.get(pid, {})
            assignee = object_name(t.get("assignedTo")) or "(未分配)"
            role = role_by_name.get(assignee, "")
            hours = _extract_hours(t)
            task_sprint = object_name(t.get("sprint"))

            task_rows.append([
                str(t.get("serialNumber") or ""),
                str(t.get("subject") or ""),
                assignee,
                role,
                f"{hours:.0f}" if hours == int(hours) else f"{hours:.1f}",
                task_sprint,
                object_name(t.get("status")),
                str(parent.get("serialNumber") or ""),
                str(parent.get("subject") or "")[:60],
                f"https://devops.aliyun.com/projex/project/{config['project_id']}/task/{t.get('id') or ''}",
            ])

            if assignee not in hours_by_person:
                hours_by_person[assignee] = {"count": 0, "hours": 0.0, "role": role}
            hours_by_person[assignee]["count"] += 1
            hours_by_person[assignee]["hours"] += hours

        # Sort task rows by assignee name then serial
        task_rows.sort(key=lambda r: (r[2], r[0]))

        # Write Excel
        output_path = sprint_output_dir / f"{safe_sprint_name}_requirements.xlsx"
        workbook = Workbook()

        # --- Sheet 1: 需求列表 ---
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

        # --- Sheet 2: 任务明细 ---
        task_sheet = workbook.create_sheet("任务明细")
        task_headers = ["任务编号", "任务标题", "负责人", "角色", "预计工时", "任务迭代", "状态", "父需求编号", "父需求标题", "链接"]

        task_sheet.append([f"{sprint_name} - 需求关联任务明细"])
        task_sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(task_headers))
        task_sheet["A1"].font = Font(bold=True, size=14)
        task_sheet["A1"].alignment = Alignment(horizontal="center")

        task_sheet.append(["任务总数", str(len(task_rows))])
        task_sheet.append([])

        task_header_row = task_sheet.max_row + 1
        for col, header in enumerate(task_headers, start=1):
            cell = task_sheet.cell(row=task_header_row, column=col, value=header)
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="D9EAF7")
            cell.alignment = Alignment(horizontal="center", vertical="center")

        for row in task_rows:
            task_sheet.append(row)

        task_widths = [16, 50, 12, 12, 10, 14, 12, 16, 50, 50]
        for i, w in enumerate(task_widths, start=1):
            task_sheet.column_dimensions[get_column_letter(i)].width = w

        # Make link cells clickable
        link_col_idx = len(task_headers)
        for row_idx in range(task_header_row + 1, task_sheet.max_row + 1):
            cell = task_sheet.cell(row=row_idx, column=link_col_idx)
            if cell.value:
                cell.hyperlink = cell.value
                cell.font = Font(color="0563C1", underline="single")

        for row in task_sheet.iter_rows(min_row=task_header_row + 1):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)

        task_sheet.freeze_panes = task_sheet.cell(row=task_header_row + 1, column=1)
        task_sheet.auto_filter.ref = f"A{task_header_row}:{get_column_letter(len(task_headers))}{max(task_header_row, task_sheet.max_row)}"

        # --- Sheet 3: 工时汇总 ---
        summary_sheet = workbook.create_sheet("工时汇总")
        summary_headers = ["姓名", "角色", "任务数", "预计工时"]

        summary_sheet.append([f"{sprint_name} - 按负责人工时汇总"])
        summary_sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(summary_headers))
        summary_sheet["A1"].font = Font(bold=True, size=14)
        summary_sheet["A1"].alignment = Alignment(horizontal="center")

        summary_sheet.append([])
        sheader_row = summary_sheet.max_row + 1
        for col, header in enumerate(summary_headers, start=1):
            cell = summary_sheet.cell(row=sheader_row, column=col, value=header)
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="D9EAF7")
            cell.alignment = Alignment(horizontal="center")

        total_count = 0
        total_hours = 0.0
        for name in sorted(hours_by_person.keys()):
            d = hours_by_person[name]
            h_str = f"{d['hours']:.0f}" if d["hours"] == int(d["hours"]) else f"{d['hours']:.1f}"
            summary_sheet.append([name, d["role"], str(d["count"]), h_str])
            total_count += d["count"]
            total_hours += d["hours"]

        h_str = f"{total_hours:.0f}" if total_hours == int(total_hours) else f"{total_hours:.1f}"
        summary_sheet.append(["总计", "", str(total_count), h_str])

        for i, w in enumerate([16, 12, 12, 12], start=1):
            summary_sheet.column_dimensions[get_column_letter(i)].width = w

        for row in summary_sheet.iter_rows(min_row=sheader_row + 1):
            for cell in row:
                cell.alignment = Alignment(horizontal="center", vertical="center")

        workbook.save(output_path)
        print(f"\nExported {len(items)} requirements + {len(child_tasks)} child tasks to {output_path.resolve()}", file=sys.stderr)

        # Print summary to stdout
        print(f"\n=== {sprint_name} 工时汇总（需求关联任务） ===\n")
        print(f"{'姓名':<10} {'角色':<12} {'任务数':<8} {'预计工时':<10}")
        print("-" * 44)
        for name in sorted(hours_by_person.keys()):
            d = hours_by_person[name]
            h_str = f"{d['hours']:.0f}" if d["hours"] == int(d["hours"]) else f"{d['hours']:.1f}"
            print(f"{name:<10} {d['role']:<12} {d['count']:<8} {h_str:<10}")
        print("-" * 44)
        print(f"{'总计':<10} {'':<12} {total_count:<8} {h_str:<10}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
