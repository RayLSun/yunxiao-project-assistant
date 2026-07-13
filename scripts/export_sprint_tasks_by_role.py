#!/usr/bin/env python3
"""
Export sprint tasks filtered by team role to Excel.

Fetches all tasks in a sprint, filters by assignee role (using team config),
and outputs an Excel file with:
  - Task detail sheet (with clickable 链接 column before 标题)
  - 工时汇总 sheet (per-person task count + 预计工时 summary)

Usage:
    # Export all 后端研发 tasks
    python scripts/export_sprint_tasks_by_role.py --sprint-name "Sprint260728" --role 后端研发

    # Export 前端研发 tasks
    python scripts/export_sprint_tasks_by_role.py --sprint-name "Sprint260728" --role 前端研发

    # Export all non-exempt team members' tasks
    python scripts/export_sprint_tasks_by_role.py --sprint-name "Sprint260728" --all

    # Custom output filename
    python scripts/export_sprint_tasks_by_role.py --sprint-name "Sprint260728" --role 后端研发 --output-name "后端任务总表"
"""
from __future__ import annotations

import argparse
import datetime as dt
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
    column_value,
    discover_headers,
    load_config_file,
    load_team,
    team_members_by_role,
    object_name,
    filter_excluded_statuses,
)

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

CONFIG_DIR = SKILL_DIR / "config"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "yunxiao_config.json"
EXAMPLE_CONFIG_PATH = CONFIG_DIR / "yunxiao_config.example.json"
OUTPUT_DIR = SKILL_DIR / "exports"

STANDARD_TASK_HEADERS = ["编号", "标题", "描述", "负责人", "状态", "工作项类型"]


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
    parser = argparse.ArgumentParser(description="Export sprint tasks filtered by team role to Excel.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help=f"Config JSON path. Default: {DEFAULT_CONFIG_PATH}")
    parser.add_argument("--domain", help=f"Yunxiao domain. Default: {DEFAULT_DOMAIN}")
    parser.add_argument("--token", help="Yunxiao personal access token. Prefer YUNXIAO_TOKEN.")
    parser.add_argument("--organization-id", help="Yunxiao organizationId.")
    parser.add_argument("--project-id", help="Yunxiao projectId.")
    parser.add_argument("--sprint-name", required=True, help="Sprint name (exact or keyword match).")
    parser.add_argument("--sprint-statuses", default=DEFAULT_SPRINT_STATUSES, help=f"Default: {DEFAULT_SPRINT_STATUSES}")
    parser.add_argument("--role", help="Team role to filter (e.g. 后端研发, 前端研发, RPA研发). Required unless --all.")
    parser.add_argument("--all", action="store_true", help="Export tasks for all non-exempt team members.")
    parser.add_argument("--output-name", help="Custom output filename (without extension). Default: {sprint}_{role}_tasks")
    parser.add_argument("--no-descriptions", action="store_true", help="Skip fetching task descriptions (faster).")
    parser.add_argument("--show-config-path", action="store_true", help="Print the absolute config file path and exit.")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Sprint resolution
# ---------------------------------------------------------------------------

def resolve_sprint_by_name(
    client: YunxiaoClient,
    project_id: str,
    sprint_name: str,
    statuses: str,
) -> dict[str, Any]:
    """Find a sprint by exact name, then keyword match."""
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
# Assignee helpers
# ---------------------------------------------------------------------------

def extract_assignee_id(item: dict[str, Any]) -> str:
    """Return user id from assignedTo dict."""
    assigned = item.get("assignedTo")
    if isinstance(assigned, dict):
        return str(assigned.get("id") or assigned.get("identifier") or "")
    return ""


def extract_assignee_name(item: dict[str, Any]) -> str:
    """Return display name from assignedTo dict."""
    assigned = item.get("assignedTo")
    if isinstance(assigned, dict):
        return str(assigned.get("displayName") or assigned.get("name") or "")
    return ""


def extract_hours(item: dict[str, Any]) -> float:
    """Extract 预计工时 from customFieldValues."""
    for cf in item.get("customFieldValues") or []:
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


def build_workitem_url(project_id: str, workitem_id: str) -> str:
    """Build the Yunxiao web URL for a workitem.

    Format: https://devops.aliyun.com/projex/project/{project_id}/task/{workitem_id}
    """
    return f"https://devops.aliyun.com/projex/project/{project_id}/task/{workitem_id}"


def format_hours(h: float) -> str:
    return str(int(h)) if h == int(h) else f"{h:.2f}"


# ---------------------------------------------------------------------------
# Excel output
# ---------------------------------------------------------------------------

def write_excel(
    output_path: Path,
    sprint_name: str,
    sprint_id: str,
    config: dict[str, Any],
    filter_label: str,
    task_headers: list[str],
    rows: list[list[str]],
    member_names: list[str],
    summary_data: list[dict[str, Any]],
) -> None:
    workbook = Workbook()

    # Build final headers: insert 链接 before 标题
    final_headers: list[str] = []
    for h in task_headers:
        if h == "标题":
            final_headers.append("链接")
        final_headers.append(h)

    # Build final rows: insert URL before 标题 value
    final_rows: list[list[str]] = []
    for row in rows:
        new_row: list[str] = []
        for i, h in enumerate(task_headers):
            if h == "标题":
                # row[i] is the URL we already inserted
                new_row.append(row[i])
            new_row.append(row[i])
        final_rows.append(new_row)

    metadata = {
        "导出时间": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "项目ID": config["project_id"],
        "迭代": sprint_name,
        "迭代ID": sprint_id,
        "筛选": filter_label,
        "任务总数": str(len(final_rows)),
    }

    # --- Task detail sheet ---
    sheet = workbook.active
    sheet.title = "任务明细"

    title_text = f"任务明细 - {sprint_name} - {filter_label}"
    sheet.append([title_text])
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(final_headers))
    sheet["A1"].font = Font(bold=True, size=14)
    sheet["A1"].alignment = Alignment(horizontal="center")

    for key, value in metadata.items():
        sheet.append([key, value])

    header_row = sheet.max_row + 2
    for col, header in enumerate(final_headers, start=1):
        cell = sheet.cell(row=header_row, column=col, value=header)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="D9EAF7")
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for row in final_rows:
        sheet.append(row)

    # Column widths
    widths = [16, 50, 40, 70, 18, 16, 24, 20, 20, 14, 14, 20, 20, 20]
    for i, w in enumerate(widths[:len(final_headers)], start=1):
        sheet.column_dimensions[get_column_letter(i)].width = w

    # Make link cells clickable
    link_col_idx = final_headers.index("链接") + 1
    for row_idx in range(header_row + 1, sheet.max_row + 1):
        cell = sheet.cell(row=row_idx, column=link_col_idx)
        if cell.value:
            cell.hyperlink = cell.value
            cell.font = Font(color="0563C1", underline="single")

    for row in sheet.iter_rows(min_row=header_row + 1):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    sheet.freeze_panes = sheet.cell(row=header_row + 1, column=1)
    sheet.auto_filter.ref = f"A{header_row}:{get_column_letter(len(final_headers))}{max(header_row, sheet.max_row)}"

    # --- 工时汇总 sheet ---
    if "预计工时" in task_headers and summary_data:
        summary_sheet = workbook.create_sheet("工时汇总")
        summary_sheet.append([f"工时汇总 - {filter_label}"])
        summary_sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=3)
        summary_sheet["A1"].font = Font(bold=True, size=14)
        summary_sheet["A1"].alignment = Alignment(horizontal="center")

        summary_sheet.append(["迭代", sprint_name])
        summary_sheet.append([])

        sum_headers = ["姓名", "任务数", "预计工时"]
        sheader_row = summary_sheet.max_row + 1
        for col, header in enumerate(sum_headers, start=1):
            cell = summary_sheet.cell(row=sheader_row, column=col, value=header)
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="D9EAF7")
            cell.alignment = Alignment(horizontal="center")

        total_count = 0
        total_hours = 0.0
        for d in summary_data:
            summary_sheet.append([d["name"], str(d["count"]), format_hours(d["hours"])])
            total_count += d["count"]
            total_hours += d["hours"]

        summary_sheet.append(["总计", str(total_count), format_hours(total_hours)])

        for i, w in enumerate([16, 12, 12], start=1):
            summary_sheet.column_dimensions[get_column_letter(i)].width = w

        for row in summary_sheet.iter_rows(min_row=sheader_row + 1):
            for cell in row:
                cell.alignment = Alignment(horizontal="center", vertical="center")

    workbook.save(output_path)


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

    if not args.role and not args.all:
        print("ERROR: Must specify --role or --all.", file=sys.stderr)
        return 1

    config, team_members = build_config(args)
    client = YunxiaoClient(config)

    # Determine target member IDs
    if args.all:
        target_members = [m for m in team_members if not m["exempt"] and m["yunxiao_id"]]
        filter_label = "全部研发（非产品）"
    else:
        target_members = team_members_by_role(team_members, args.role)
        target_members = [m for m in target_members if m["yunxiao_id"]]
        filter_label = args.role

    if not target_members:
        print(f"ERROR: No team members found for filter: {filter_label}", file=sys.stderr)
        print("Ensure team.members in config has yunxiao_id filled.", file=sys.stderr)
        return 1

    # Build yunxiao_id -> name lookup
    target_ids: dict[str, str] = {m["yunxiao_id"]: m["name"] for m in target_members}
    member_names = [m["name"] for m in target_members]

    print(f"Filter: {filter_label}", file=sys.stderr)
    print(f"Members ({len(target_members)}): {', '.join(member_names)}", file=sys.stderr)

    # Resolve sprint
    sprint = resolve_sprint_by_name(client, config["project_id"], args.sprint_name, args.sprint_statuses)
    sprint_id = str(sprint.get("id") or sprint.get("identifier") or "")
    sprint_name = str(sprint.get("name") or sprint.get("displayName") or sprint_id)
    if not sprint_id:
        print(f"ERROR: Cannot determine sprint id from sprint: {sprint}", file=sys.stderr)
        return 1
    print(f"Sprint: {sprint_name} ({sprint_id})", file=sys.stderr)

    # Fetch all tasks in sprint
    print("Fetching tasks...", file=sys.stderr)
    all_tasks = client.fetch_all_workitems(config["project_id"], sprint_id, "Task")
    all_tasks = filter_excluded_statuses(all_tasks)
    print(f"  Total tasks in sprint: {len(all_tasks)}", file=sys.stderr)

    # Filter by target assignees
    filtered_tasks: list[dict[str, Any]] = []
    for t in all_tasks:
        uid = extract_assignee_id(t)
        if uid in target_ids:
            t["_assignee_name"] = target_ids[uid]
            filtered_tasks.append(t)

    print(f"  Filtered tasks: {len(filtered_tasks)}", file=sys.stderr)

    # Per-person counts
    for name in member_names:
        count = sum(1 for t in filtered_tasks if t["_assignee_name"] == name)
        print(f"    {name}: {count} tasks", file=sys.stderr)

    if not filtered_tasks:
        print("No tasks found for the specified filter.", file=sys.stderr)
        return 0

    # Enrich with descriptions
    if not args.no_descriptions:
        print("Fetching descriptions...", file=sys.stderr)
        client.enrich_with_descriptions(filtered_tasks)

    # Discover headers
    task_headers = discover_headers(filtered_tasks, STANDARD_TASK_HEADERS)
    print(f"Headers ({len(task_headers)}): {task_headers}", file=sys.stderr)

    # Build rows: insert URL before 标题
    project_id = config["project_id"]
    rows: list[list[str]] = []
    for item in filtered_tasks:
        wid = str(item.get("id") or item.get("identifier") or "")
        url = build_workitem_url(project_id, wid) if wid else ""
        row: list[str] = []
        for header in task_headers:
            if header == "标题":
                row.append(url)
            row.append(column_value(item, header))
        rows.append(row)

    # Build summary data
    summary_data: list[dict[str, Any]] = []
    for m in target_members:
        name = m["name"]
        person_tasks = [t for t in filtered_tasks if t["_assignee_name"] == name]
        hours = sum(extract_hours(t) for t in person_tasks)
        summary_data.append({"name": name, "count": len(person_tasks), "hours": hours})

    # Output filename
    safe_sprint = re.sub(r"[^\w.-]+", "_", sprint_name)
    if args.output_name:
        safe_name = re.sub(r"[^\w.-]+", "_", args.output_name)
    elif args.all:
        safe_name = f"{safe_sprint}_all_tasks"
    else:
        safe_role = re.sub(r"[^\w.-]+", "_", args.role)
        safe_name = f"{safe_sprint}_{safe_role}_tasks"

    output_dir = OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{safe_name}.xlsx"

    write_excel(
        output_path,
        sprint_name,
        sprint_id,
        config,
        filter_label,
        task_headers,
        rows,
        member_names,
        summary_data,
    )

    # Print summary
    print(f"\n=== {sprint_name} - {filter_label} ===\n")
    print(f"{'姓名':<8} {'任务数':<8} {'预计工时':<10}")
    print("-" * 30)
    total_count = 0
    total_hours = 0.0
    for d in summary_data:
        print(f"{d['name']:<8} {d['count']:<8} {format_hours(d['hours']):<10}")
        total_count += d["count"]
        total_hours += d["hours"]
    print("-" * 30)
    print(f"{'总计':<8} {total_count:<8} {format_hours(total_hours):<10}")
    print(f"\nExported to {output_path.resolve()}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
