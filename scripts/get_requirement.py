#!/usr/bin/env python3
"""Fetch ONE Yunxiao workitem by serialNumber, across projects.

Complements fetch_for_prd.py (which targets whole sprints or subject keywords)
by resolving a single requirement such as ``LMAV-9`` directly.

The serial prefix is itself a project short code, so the project is resolved
from the prefix automatically — no need to know which project the item lives in
even when it differs from the configured default.

Output: JSON on stdout (default) or a human-readable block with --format text.
This script never writes files unless --save-text is given.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Add lib/ to path
SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SKILL_DIR / "lib"))

from yunxiao_client import (  # noqa: E402
    DEFAULT_DOMAIN,
    YunxiaoClient,
    build_config_from_sources,
    load_config_file,
    parse_description_envelope,
    richtext_to_text,
)

CONFIG_DIR = SKILL_DIR / "config"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "yunxiao_config.json"
EXAMPLE_CONFIG_PATH = CONFIG_DIR / "yunxiao_config.example.json"

CATEGORY_BY_TYPE_HINT = {
    "任务": "Task",
    "缺陷": "Bug",
}


# ---------------------------------------------------------------------------
# Config and CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch a single Yunxiao workitem by serialNumber (cross-project).",
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH),
                        help=f"Config JSON path. Default: {DEFAULT_CONFIG_PATH}")
    parser.add_argument("--domain", help=f"Yunxiao domain. Default: {DEFAULT_DOMAIN}")
    parser.add_argument("--token", help="Yunxiao personal access token. Prefer YUNXIAO_TOKEN.")
    parser.add_argument("--organization-id", help="Yunxiao organizationId.")
    parser.add_argument("--project-id", help="Yunxiao projectId (default project).")

    parser.add_argument("--serial", help="Workitem serialNumber, e.g. LMAV-9.")
    parser.add_argument("--project", help="Project id, short code (LMAV) or name. "
                                          "Default: derived from the serial prefix.")
    parser.add_argument("--category", choices=["Req", "Task", "Bug"], default=None,
                        help="Restrict the search to one workitem category. Default: try Req, Task, Bug.")
    parser.add_argument("--all-projects", action="store_true",
                        help="Also scan every project in the organization when the serial prefix "
                             "does not resolve to a project code (slower).")
    parser.add_argument("--list-projects", action="store_true",
                        help="List every project in the organization and exit.")
    parser.add_argument("--no-description", action="store_true",
                        help="Skip the detail request (description left empty).")
    parser.add_argument("--format", choices=["json", "text"], default="json",
                        help="Output format. Default: json")
    parser.add_argument("--raw", action="store_true",
                        help="Include the raw detail payload (large).")
    parser.add_argument("--save-text", help="Also write the description text to this path.")
    parser.add_argument("--show-config-path", action="store_true",
                        help="Print the absolute config file path and exit.")
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> dict[str, Any]:
    config_file = Path(args.config) if args.config else DEFAULT_CONFIG_PATH
    file_config = load_config_file(config_file)
    cli_args = {
        "domain": args.domain,
        "token": args.token,
        "organization_id": args.organization_id,
        "project_id": args.project_id,
    }
    return build_config_from_sources(file_config, cli_args)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_text(shaped: dict[str, Any], project: dict[str, str], url: str) -> str:
    cf = shaped.get("customFields") or {}
    lines = [
        f"编号    : {shaped.get('serialNumber')}",
        f"标题    : {shaped.get('subject')}",
        f"项目    : {project.get('code')} ({project.get('name')})  id={project.get('id')}",
        f"类型    : {shaped.get('workitemType')}",
        f"状态    : {shaped.get('status')}",
        f"负责人  : {shaped.get('assignedTo')}",
        f"创建人  : {shaped.get('creator')}",
        f"迭代    : {shaped.get('sprintName')}",
        f"链接    : {url}",
    ]
    filled = {k: v for k, v in cf.items() if v}
    if filled:
        lines.append("自定义字段:")
        lines += [f"  {k} = {v}" for k, v in filled.items()]
    desc = shaped.get("descriptionText") or ""
    lines += ["", f"--- 描述正文（{len(desc)} 字符）---", "", desc]
    return "\n".join(lines)


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

    config = build_config(args)
    client = YunxiaoClient(config)

    if args.list_projects:
        projects = client.list_project_summary()
        if args.format == "text":
            for p in projects:
                print(f"{p['code']:<10} {p['name']:<28} {p['id']}")
        else:
            print(json.dumps({"projects": projects}, ensure_ascii=False, indent=2))
        return 0

    if not args.serial:
        raise RuntimeError("Specify --serial LMAV-9 (or use --list-projects)")

    categories = (args.category,) if args.category else ("Req", "Task", "Bug")
    item = client.find_workitem_by_serial(
        args.serial,
        project_id=args.project,
        categories=categories,
        all_projects=args.all_projects,
    )

    project_id = str(item.get("_projectId") or config["project_id"])
    category = str(item.get("_category") or "Req")

    # project metadata for the report (single extra request, best effort)
    project = {"code": str(item.get("_projectCode") or ""), "name": "", "id": project_id}
    try:
        full = client.resolve_project(project_id)
        project["code"] = client.project_code(full) or project["code"]
        project["name"] = str(full.get("name") or "")
    except Exception:  # noqa: BLE001
        pass

    detail: dict[str, Any] = {}
    if not args.no_description:
        print(f"Fetching detail for {item.get('serialNumber')}...", file=sys.stderr)
        try:
            detail = client.fetch_workitem_detail(str(item.get("id") or ""))
        except Exception as exc:  # noqa: BLE001
            print(f"  warn: detail fetch failed: {exc}", file=sys.stderr)

    merged = dict(item)
    if detail.get("description"):
        merged["description"] = detail["description"]

    sprint_name = ""
    sprint = merged.get("sprint") or detail.get("sprint")
    if isinstance(sprint, dict):
        sprint_name = str(sprint.get("name") or sprint.get("displayName") or sprint.get("id") or "")

    shaped = client.shape_requirement(merged, sprint_name)
    # shape_requirement falls back to categoryId when workitemType is absent;
    # prefer the real type name from the detail payload.
    if isinstance(detail.get("workitemType"), dict):
        shaped["workitemType"] = str(
            detail["workitemType"].get("name") or shaped.get("workitemType") or ""
        )

    url = f"https://devops.aliyun.com/projex/project/{project_id}/task/{item.get('id')}"

    if args.save_text:
        out = Path(args.save_text)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(shaped.get("descriptionText") or "", encoding="utf-8")
        print(f"Description text saved: {out}", file=sys.stderr)

    if args.format == "text":
        print(render_text(shaped, project, url))
    else:
        payload: dict[str, Any] = {
            "project": project,
            "category": category,
            "url": url,
            "workitem": shaped,
        }
        if args.raw:
            payload["rawDetail"] = detail or merged
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
