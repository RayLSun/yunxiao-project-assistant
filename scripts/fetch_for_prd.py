#!/usr/bin/env python3
"""
Fetch Yunxiao (center-edition) requirements for PRD generation.

Uses the shared yunxiao_client library for API access. This script only
handles CLI arguments and output shaping (JSON on stdout).

Three targeting modes (mutually exclusive; --requirement-keyword wins):
  1. --sprint-id            direct sprint identifier
  2. --sprint-keyword       substring match against sprint names
  3. --requirement-keyword  substring match against requirement subjects
                            across all sprints in the project

Output: a single JSON object on stdout. Claude / the agent consumes this,
renders the requirement list for user confirmation, then generates PRDs.

This script never writes files or talks to the user. It is a pure data fetcher.
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
    filter_sprints_by_keyword,
    first_real,
    load_config_file,
    resolve_sprint,
    safe_filename_subject,
)

CONFIG_DIR = SKILL_DIR / "config"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "yunxiao_config.json"
EXAMPLE_CONFIG_PATH = CONFIG_DIR / "yunxiao_config.example.json"

DEFAULT_MAX_RESULTS = 50


# ---------------------------------------------------------------------------
# Config and CLI
# ---------------------------------------------------------------------------

def build_config(args: argparse.Namespace) -> dict[str, Any]:
    config_file = Path(args.config) if args.config else DEFAULT_CONFIG_PATH
    file_config = load_config_file(config_file)

    cli_args = {
        "domain": args.domain,
        "token": args.token,
        "organization_id": args.organization_id,
        "project_id": args.project_id,
    }
    config = build_config_from_sources(file_config, cli_args)
    return config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Yunxiao requirements for PRD generation.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help=f"Config JSON path. Default: {DEFAULT_CONFIG_PATH}")
    parser.add_argument("--domain", help=f"Yunxiao domain. Default: {DEFAULT_DOMAIN}")
    parser.add_argument("--token", help="Yunxiao personal access token. Prefer YUNXIAO_TOKEN.")
    parser.add_argument("--organization-id", help="Yunxiao organizationId.")
    parser.add_argument("--project-id", help="Yunxiao projectId.")
    parser.add_argument("--sprint-id", help="Direct sprint id.")
    parser.add_argument("--sprint-keyword", help="Substring filter against sprint names.")
    parser.add_argument("--sprint-statuses", default=DEFAULT_SPRINT_STATUSES, help=f"Comma-separated sprint statuses for --sprint-keyword. Default: {DEFAULT_SPRINT_STATUSES}")
    parser.add_argument("--requirement-keyword", help="Substring filter against requirement subjects across all sprints.")
    parser.add_argument("--search-statuses", default=DEFAULT_SEARCH_STATUSES, help=f"Comma-separated sprint statuses for --requirement-keyword search. Default: {DEFAULT_SEARCH_STATUSES}")
    parser.add_argument("--recent-sprints", type=int, default=0, help="Limit requirement search to the N most recently modified sprints (0 = all).")
    parser.add_argument("--today", help="Override today for date-based sprint selection, format YYYY-MM-DD.")
    parser.add_argument("--max-results", type=int, default=DEFAULT_MAX_RESULTS, help=f"Cap requirement count. Default: {DEFAULT_MAX_RESULTS}")
    parser.add_argument("--show-config-path", action="store_true", help="Print the absolute config file path and exit.")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def run_sprint_mode(config: dict[str, Any], args: argparse.Namespace, today: dt.date) -> dict[str, Any]:
    """Mode A: pick a sprint, fetch its requirements."""
    client = YunxiaoClient(config)

    if args.sprint_id:
        sprint = {"id": args.sprint_id, "name": args.sprint_id}
        matched = [sprint]
    else:
        sprints = client.list_sprints(config["project_id"], args.sprint_statuses)
        matched = filter_sprints_by_keyword(sprints, args.sprint_keyword or "")
        if not matched:
            raise RuntimeError(f"No sprint name contains keyword: {args.sprint_keyword!r}")
        if len(matched) == 1:
            sprint = matched[0]
        else:
            try:
                sprint = resolve_sprint(matched, args.sprint_keyword or "", today)
            except RuntimeError:
                sprint = None

    sprint_id = ""
    sprint_name = ""
    if sprint:
        sprint_id = str(sprint.get("id") or sprint.get("identifier") or "")
        sprint_name = str(sprint.get("name") or sprint.get("displayName") or sprint_id)
        if not sprint_id:
            raise RuntimeError(f"Cannot determine sprint id from sprint: {sprint}")

    output: dict[str, Any] = {}
    if sprint_id:
        items = client.fetch_all_workitems(config["project_id"], sprint_id, "Req")
        print(f"Fetching descriptions for {len(items)} requirements...", file=sys.stderr)
        client.enrich_with_descriptions(items)
        requirements = [client.shape_requirement(it, sprint_name) for it in items[: args.max_results]]
        output["sprint"] = {"id": sprint_id, "name": sprint_name}
        output["requirements"] = requirements
    if len(matched) > 1 and not sprint_id:
        output["matchedSprints"] = [
            {"id": str(s.get("id") or ""), "name": str(s.get("name") or s.get("displayName") or ""), "status": str(s.get("status") or "")}
            for s in matched
        ]
        output["requirements"] = []
    elif len(matched) > 1:
        output["matchedSprints"] = [
            {"id": str(s.get("id") or ""), "name": str(s.get("name") or s.get("displayName") or ""), "status": str(s.get("status") or "")}
            for s in matched
        ]
    return output


def run_requirement_mode(config: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    """Mode B: search requirements by subject substring across sprints."""
    client = YunxiaoClient(config)
    needle = (args.requirement_keyword or "").lower()
    if not needle:
        raise RuntimeError("--requirement-keyword cannot be empty")

    sprints = client.list_sprints(config["project_id"], args.search_statuses)
    if args.recent_sprints and args.recent_sprints > 0:
        sprints.sort(key=lambda s: int(s.get("gmtModified") or 0), reverse=True)
        sprints = sprints[: args.recent_sprints]

    matched: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for sprint in sprints:
        sprint_id = str(sprint.get("id") or "")
        if not sprint_id:
            continue
        try:
            items = client.fetch_all_workitems(config["project_id"], sprint_id, "Req")
        except Exception:
            continue
        for it in items:
            subject = str(it.get("subject") or "")
            if needle in subject.lower():
                matched.append((sprint, it))
        if len(matched) >= args.max_results:
            break

    matched = matched[: args.max_results]
    print(f"Found {len(matched)} matching requirements across {len(sprints)} sprints; fetching descriptions...", file=sys.stderr)
    items_only = [it for _, it in matched]
    client.enrich_with_descriptions(items_only)

    requirements = [
        client.shape_requirement(it, str(sp.get("name") or sp.get("displayName") or ""))
        for sp, it in matched
    ]
    return {"requirements": requirements}


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

    config = build_config(args)

    today = dt.date.today()
    if args.today:
        today = dt.datetime.strptime(args.today, "%Y-%m-%d").date()

    if args.requirement_keyword:
        output = run_requirement_mode(config, args)
    elif args.sprint_id or args.sprint_keyword:
        output = run_sprint_mode(config, args, today)
    else:
        raise RuntimeError(
            "Specify one of: --sprint-id, --sprint-keyword, or --requirement-keyword"
        )

    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
