#!/usr/bin/env python3
"""
Shared client library for Alibaba Yunxiao (center-edition) OpenAPI.

Extracted from export_current_sprint.py and fetch_for_prd.py to eliminate
code duplication. Provides:

- Config loading (file / env / CLI flag priority)
- HTTP request helper (x-yunxiao-token auth)
- Sprint listing and resolution (keyword / date matching)
- Workitem search with pagination
- Workitem detail fetching (for description enrichment)
- Rich-text / description envelope parsing
- Custom field extraction helpers

Usage:
    from yunxiao_client import YunxiaoClient

    client = YunxiaoClient.from_config_file("config/yunxiao_config.json")
    sprints = client.list_sprints(project_id, statuses="TODO,DOING,ARCHIVED")
    items   = client.fetch_all_workitems(project_id, sprint_id, category="Req")

This module depends only on the Python standard library.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_DOMAIN = "openapi-rdc.aliyuncs.com"
DEFAULT_SPRINT_STATUSES = "TODO,DOING,ARCHIVED"
DEFAULT_SEARCH_STATUSES = "TODO,DOING,ARCHIVED,DONE"

PLACEHOLDER_VALUES = {
    "",
    "pt-your-token",
    "your-yunxiao-token",
    "your-organization-id",
    "your-project-id",
    "你的个人访问令牌",
    "你的organizationId",
}


# ---------------------------------------------------------------------------
# Config helpers (module-level for use before client instantiation)
# ---------------------------------------------------------------------------

def load_config_file(path: str | Path | None) -> dict[str, Any]:
    """Load a JSON config file, returning {} if missing or invalid."""
    if not path:
        return {}
    p = Path(path) if not isinstance(path, Path) else path
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise RuntimeError(f"Config file must be a JSON object: {p}")
    return data


def is_real_value(value: Any) -> bool:
    return value is not None and str(value).strip() not in PLACEHOLDER_VALUES


def first_real(*values: Any) -> str | None:
    for value in values:
        if is_real_value(value):
            return str(value).strip()
    return None


def build_config_from_sources(
    file_config: dict[str, Any],
    cli_args: dict[str, str | None],
    env_prefix: str = "YUNXIAO",
) -> dict[str, Any]:
    """Merge config sources: CLI flag > env var > config file.

    ``cli_args`` is a dict mapping known keys (domain, token,
    organization_id, project_id, sprint_id, sprint_keyword,
    sprint_statuses, corp_id) to values or None.
    """
    def env(name: str) -> str | None:
        return os.environ.get(f"{env_prefix}_{name.upper()}")

    domain = first_real(cli_args.get("domain"), env("domain"), file_config.get("domain")) or DEFAULT_DOMAIN
    token = first_real(cli_args.get("token"), env("token"), file_config.get("yunxiao_token"))
    organization_id = first_real(cli_args.get("organization_id"), env("organization_id"), file_config.get("organization_id"))
    project_id = first_real(cli_args.get("project_id"), env("project_id"), file_config.get("project_id"))
    sprint_id = first_real(cli_args.get("sprint_id"), env("sprint_id"), file_config.get("sprint_id"))
    sprint_keyword = first_real(cli_args.get("sprint_keyword"), env("sprint_keyword"), file_config.get("sprint_keyword"))
    sprint_statuses = first_real(cli_args.get("sprint_statuses"), env("sprint_statuses"), file_config.get("sprint_statuses")) or DEFAULT_SPRINT_STATUSES
    corp_id = first_real(cli_args.get("corp_id"), env("corp_id"), file_config.get("corpid"), file_config.get("corp_id")) or ""

    missing = []
    if not token:
        missing.append("yunxiao_token (YUNXIAO_TOKEN)")
    if not organization_id:
        missing.append("organization_id (YUNXIAO_ORGANIZATION_ID)")
    if not project_id:
        missing.append("project_id (YUNXIAO_PROJECT_ID)")
    if missing:
        raise RuntimeError(f"Missing required config: {', '.join(missing)}")

    return {
        "domain": domain,
        "token": token,
        "organization_id": organization_id,
        "project_id": project_id,
        "sprint_id": sprint_id or "",
        "sprint_keyword": sprint_keyword or "",
        "sprint_statuses": sprint_statuses,
        "corp_id": corp_id,
    }


# ---------------------------------------------------------------------------
# Low-level HTTP
# ---------------------------------------------------------------------------

def _normalize_domain(domain: str) -> str:
    value = domain.strip()
    if value.startswith(("http://", "https://")):
        parsed = urllib.parse.urlparse(value)
        value = parsed.netloc or parsed.path
    return value.rstrip("/")


def _urlencode(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _ssl_context() -> ssl.SSLContext:
    cafile = os.environ.get("SSL_CERT_FILE")
    candidates = [Path(cafile)] if cafile else []
    candidates.append(Path("/etc/ssl/cert.pem"))
    try:
        import certifi
        candidates.append(Path(certifi.where()))
    except Exception:
        pass
    for candidate in candidates:
        if candidate and candidate.exists():
            return ssl.create_default_context(cafile=str(candidate))
    return ssl.create_default_context()


def _request_json(
    config: dict[str, Any],
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> Any:
    domain = _normalize_domain(config["domain"])
    url = f"https://{domain}{path}"
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "x-yunxiao-token": config["token"],
    }
    req = urllib.request.Request(url, data=payload, method=method.upper(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60, context=_ssl_context()) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail[:1200]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request failed for {url}: {exc}") from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Non-JSON response from {url}: {raw[:500]}") from exc


def _extract_items(response: Any) -> list[dict[str, Any]]:
    if isinstance(response, list):
        return [item for item in response if isinstance(item, dict)]
    if not isinstance(response, dict):
        return []
    for key in ("result", "data", "items", "list", "records", "workitems", "workItems", "sprints"):
        value = response.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _extract_items(value)
            if nested:
                return nested
    return []


# ---------------------------------------------------------------------------
# Sprint resolution
# ---------------------------------------------------------------------------

def parse_trailing_date(name: str) -> dt.date | None:
    """Parse a trailing YYMMDD suffix from a sprint name."""
    if not name:
        return None
    suffix = name.strip()[-6:]
    if not re.fullmatch(r"\d{6}", suffix):
        return None
    try:
        return dt.datetime.strptime(suffix, "%y%m%d").date()
    except ValueError:
        return None


def resolve_sprint(
    sprints: list[dict[str, Any]],
    keyword: str,
    today: dt.date,
) -> dict[str, Any]:
    """Pick a sprint by keyword.

    1. Filter sprints whose name contains keyword (case-insensitive).
    2. If any filtered sprint has a trailing YYMMDD date, pick the one
       closest to today (ties favor the future date, then the later date).
    3. Otherwise prefer a DOING sprint, then the most recently modified.
    """
    needle = keyword.lower()
    filtered = [
        s for s in sprints
        if needle in str(s.get("name") or s.get("displayName") or "").lower()
    ]
    if not filtered:
        raise RuntimeError(f"No sprint name contains keyword: {keyword!r}")

    dated: list[tuple[int, dt.date, dict[str, Any]]] = []
    for sprint in filtered:
        name = str(sprint.get("name") or sprint.get("displayName") or "")
        sprint_date = parse_trailing_date(name)
        if sprint_date:
            dated.append((abs((sprint_date - today).days), sprint_date, sprint))
    if dated:
        dated.sort(key=lambda item: (item[0], item[1] < today, item[1]))
        return dated[0][2]

    doing = [s for s in filtered if str(s.get("status") or "").upper() == "DOING"]
    if doing:
        doing.sort(key=lambda s: int(s.get("gmtModified") or 0), reverse=True)
        return doing[0]

    filtered.sort(key=lambda s: int(s.get("gmtModified") or 0), reverse=True)
    return filtered[0]


def filter_sprints_by_keyword(
    sprints: list[dict[str, Any]],
    keyword: str,
) -> list[dict[str, Any]]:
    """Return sprints whose name contains keyword (case-insensitive)."""
    needle = keyword.lower()
    return [
        s for s in sprints
        if needle in str(s.get("name") or s.get("displayName") or "").lower()
    ]


# ---------------------------------------------------------------------------
# Rich-text / description parsing
# ---------------------------------------------------------------------------

def richtext_to_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    replacements = [
        (r"(?i)<br\s*/?>", "\n"),
        (r"(?i)</p\s*>", "\n"),
        (r"(?i)</div\s*>", "\n"),
        (r"(?i)</li\s*>", "\n"),
        (r"(?i)<li[^>]*>", "- "),
        (r"<[^>]+>", ""),
    ]
    for pattern, repl in replacements:
        text = re.sub(pattern, repl, text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def parse_description_envelope(value: Any) -> str:
    """Extract HTML from a Yunxiao description field.

    The list endpoint returns description as "". The detail endpoint
    returns a JSON envelope like {"htmlValue": "...", "jsonMLValue": [...]}.
    Prefer htmlValue (richtext_to_text already strips tags).
    Falls through to raw value when the envelope isn't JSON.
    """
    if not value:
        return ""
    raw = str(value)
    stripped = raw.strip()
    if stripped.startswith("{"):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return raw
        if isinstance(parsed, dict):
            for key in ("htmlValue", "html", "content"):
                inner = parsed.get(key)
                if isinstance(inner, str) and inner.strip():
                    return inner
            return ""
    return raw


# ---------------------------------------------------------------------------
# Object helpers
# ---------------------------------------------------------------------------

def object_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("displayName") or value.get("name") or value.get("id") or "")
    if value is None:
        return ""
    return str(value)


def people_names(value: Any) -> str:
    if isinstance(value, list):
        names = [object_name(item) for item in value]
        return ", ".join(name for name in names if name)
    return object_name(value)


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def custom_fields_to_dict(item: dict[str, Any]) -> dict[str, str]:
    """Flatten customFieldValues into {fieldName: displayValue}.

    Multi-value fields are joined with ", ". Empty values are skipped.
    """
    out: dict[str, str] = {}
    for field in item.get("customFieldValues") or item.get("customFields") or []:
        if not isinstance(field, dict):
            continue
        name = field.get("fieldName")
        if not name:
            continue
        values = field.get("values")
        display_values: list[str] = []
        if isinstance(values, list):
            for entry in values:
                if isinstance(entry, dict):
                    dv = str(entry.get("displayValue") or entry.get("identifier") or "")
                    if dv:
                        display_values.append(dv)
                elif entry is not None:
                    display_values.append(str(entry))
        if not display_values:
            for key in ("displayValue", "value", "text"):
                if field.get(key) is not None and str(field.get(key)).strip():
                    display_values.append(str(field.get(key)))
                    break
        joined = ", ".join(display_values)
        if joined:
            out[str(name)] = joined
    return out


def safe_filename_subject(subject: str, max_len: int = 40) -> str:
    """Make a subject safe for filenames. CJK kept; symbols -> '_'."""
    trimmed = subject.strip()[:max_len].strip()
    return re.sub(r"[\\/:*?\"<>|\s]+", "_", trimmed) or "untitled"


# ---------------------------------------------------------------------------
# Standard field mapping (for Excel / display)
# ---------------------------------------------------------------------------

STANDARD_FIELD_BY_HEADER = {
    "编号": "serialNumber",
    "标题": "subject",
    "描述": "description",
    "负责人": "assignedTo",
    "状态": "status",
    "参与者": "participants",
    "工作项类型": "workitemType",
}


def standard_value(item: dict[str, Any], field_key: str) -> str:
    if field_key == "serialNumber":
        return str(item.get("serialNumber") or "")
    if field_key == "subject":
        return str(item.get("subject") or item.get("name") or "")
    if field_key == "description":
        return richtext_to_text(parse_description_envelope(item.get("description")))
    if field_key == "assignedTo":
        return object_name(item.get("assignedTo"))
    if field_key == "status":
        return object_name(item.get("status"))
    if field_key == "participants":
        return people_names(item.get("participants"))
    if field_key == "workitemType":
        return object_name(item.get("workitemType")) or str(item.get("categoryId") or "")
    return ""


def custom_value(item: dict[str, Any], header: str) -> str:
    """Read a value from customFieldValues by matching fieldName to header text."""
    needle = _norm(header)
    if not needle:
        return ""
    for field in item.get("customFieldValues") or item.get("customFields") or []:
        if not isinstance(field, dict):
            continue
        if _norm(field.get("fieldName")) != needle:
            continue
        values = field.get("values")
        if isinstance(values, list):
            display_values = []
            for entry in values:
                if isinstance(entry, dict):
                    display_values.append(str(entry.get("displayValue") or entry.get("identifier") or ""))
                elif entry is not None:
                    display_values.append(str(entry))
            joined = ", ".join(v for v in display_values if v)
            if joined:
                return joined
        for key in ("displayValue", "value", "text"):
            if field.get(key) is not None:
                return str(field[key])
    return ""


def column_value(item: dict[str, Any], header: str) -> str:
    """Resolve a single cell: standard field if header is known, else custom field."""
    field_key = STANDARD_FIELD_BY_HEADER.get(header)
    if field_key:
        return standard_value(item, field_key)
    return custom_value(item, header)


def discover_headers(
    items: list[dict[str, Any]],
    standard_headers: list[str],
) -> list[str]:
    """Build header list = standard headers + every custom fieldName found.

    Discovery is stable: fieldNames are appended in the order first seen.
    """
    custom_names: list[str] = []
    seen: set[str] = set()
    for item in items:
        for field in item.get("customFieldValues") or item.get("customFields") or []:
            if not isinstance(field, dict):
                continue
            name = field.get("fieldName")
            if not name:
                continue
            name_str = str(name)
            key = name_str.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            custom_names.append(name_str)
    return list(standard_headers) + custom_names


# ---------------------------------------------------------------------------
# YunxiaoClient
# ---------------------------------------------------------------------------

class YunxiaoClient:
    """High-level client for Yunxiao center-edition Projex OpenAPI.

    All methods return plain Python data structures (dicts / lists).
    Authentication is handled via x-yunxiao-token header.
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config

    # -- Factory methods --

    @classmethod
    def from_config_file(cls, path: str | Path) -> "YunxiaoClient":
        file_config = load_config_file(Path(path))
        config = build_config_from_sources(file_config, {})
        return cls(config)

    @classmethod
    def from_args(
        cls,
        file_config: dict[str, Any],
        cli_args: dict[str, str | None],
    ) -> "YunxiaoClient":
        config = build_config_from_sources(file_config, cli_args)
        return cls(config)

    # -- Sprints --

    def list_sprints(
        self,
        project_id: str | None = None,
        statuses: str = DEFAULT_SPRINT_STATUSES,
    ) -> list[dict[str, Any]]:
        project_id = project_id or self.config["project_id"]
        org_id = _urlencode(self.config["organization_id"])
        project = _urlencode(project_id)
        query = urllib.parse.urlencode({"status": statuses, "page": 1, "perPage": 200})
        response = _request_json(
            self.config,
            "GET",
            f"/oapi/v1/projex/organizations/{org_id}/projects/{project}/sprints?{query}",
        )
        return _extract_items(response)

    def resolve_sprint_by_keyword(
        self,
        keyword: str,
        today: dt.date | None = None,
        statuses: str = DEFAULT_SPRINT_STATUSES,
    ) -> dict[str, Any]:
        sprints = self.list_sprints(statuses=statuses)
        return resolve_sprint(sprints, keyword, today or dt.date.today())

    def filter_sprints(
        self,
        keyword: str,
        statuses: str = DEFAULT_SPRINT_STATUSES,
    ) -> list[dict[str, Any]]:
        sprints = self.list_sprints(statuses=statuses)
        return filter_sprints_by_keyword(sprints, keyword)

    # -- Workitem search --

    def search_workitems(
        self,
        project_id: str | None = None,
        sprint_id: str = "",
        category: str = "Req",
        page: int = 1,
        per_page: int = 100,
        extra_conditions: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Search workitems via POST /workitems:search.

        Args:
            project_id: Yunxiao projectId (defaults to config).
            sprint_id: Sprint identifier to filter by.
            category: "Req" (requirements), "Task", "Bug", "Risk".
            page: Page number (1-based).
            per_page: Items per page (max 100).
            extra_conditions: Additional condition dicts to AND with
                the sprint filter. Each dict should have keys:
                fieldIdentifier, operator, value, toValue, className, format.

        Returns:
            List of workitem dicts.
        """
        project_id = project_id or self.config["project_id"]
        org_id = _urlencode(self.config["organization_id"])

        conditions: list[dict[str, Any]] = []
        if sprint_id:
            conditions.append({
                "fieldIdentifier": "sprint",
                "operator": "CONTAINS",
                "value": [sprint_id],
                "toValue": None,
                "className": "sprint",
                "format": "list",
            })
        if extra_conditions:
            conditions.extend(extra_conditions)

        body = {
            "category": category,
            "conditions": json.dumps(
                {"conditionGroups": [conditions]} if conditions else {"conditionGroups": [[]]},
                ensure_ascii=False,
            ),
            "orderBy": "gmtCreate",
            "page": page,
            "perPage": per_page,
            "sort": "desc",
            "spaceId": project_id,
            "spaceType": "Project",
        }
        response = _request_json(
            self.config,
            "POST",
            f"/oapi/v1/projex/organizations/{org_id}/workitems:search",
            body,
        )
        return _extract_items(response)

    def fetch_all_workitems(
        self,
        project_id: str | None = None,
        sprint_id: str = "",
        category: str = "Req",
        extra_conditions: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch all workitems across pages."""
        project_id = project_id or self.config["project_id"]
        all_items: list[dict[str, Any]] = []
        page = 1
        per_page = 100
        while True:
            items = self.search_workitems(
                project_id=project_id,
                sprint_id=sprint_id,
                category=category,
                page=page,
                per_page=per_page,
                extra_conditions=extra_conditions,
            )
            all_items.extend(items)
            if len(items) < per_page:
                return all_items
            page += 1

    # -- Workitem detail --

    def fetch_workitem_detail(self, workitem_id: str) -> dict[str, Any]:
        """GET single workitem (list endpoint leaves description empty)."""
        org_id = _urlencode(self.config["organization_id"])
        response = _request_json(
            self.config,
            "GET",
            f"/oapi/v1/projex/organizations/{org_id}/workitems/{workitem_id}",
        )
        if isinstance(response, dict):
            for key in ("result", "data", "workitem"):
                nested = response.get(key)
                if isinstance(nested, dict):
                    return nested
            if "id" in response or "description" in response:
                return response
        return {}

    def enrich_with_descriptions(self, items: list[dict[str, Any]]) -> None:
        """Populate each item's description from the detail endpoint, in place."""
        if not items:
            return
        for item in items:
            workitem_id = str(item.get("id") or "")
            if not workitem_id:
                continue
            try:
                detail = self.fetch_workitem_detail(workitem_id)
            except Exception as exc:  # noqa: BLE001
                import sys
                print(f"  warn: failed to fetch detail for {item.get('serialNumber')}: {exc}", file=sys.stderr)
                continue
            desc = detail.get("description")
            if desc:
                item["description"] = desc

    # -- Convenience: shaped output --

    def shape_requirement(self, item: dict[str, Any], sprint_name: str | None = None) -> dict[str, Any]:
        """Pick the fields needed for PRD generation / display."""
        desc_text = richtext_to_text(parse_description_envelope(item.get("description")))
        return {
            "id": str(item.get("id") or ""),
            "serialNumber": str(item.get("serialNumber") or ""),
            "subject": str(item.get("subject") or ""),
            "assignedTo": object_name(item.get("assignedTo")),
            "status": object_name(item.get("status")),
            "creator": object_name(item.get("creator")),
            "workitemType": object_name(item.get("workitemType")) or str(item.get("categoryId") or ""),
            "sprintName": sprint_name or "",
            "descriptionText": desc_text,
            "customFields": custom_fields_to_dict(item),
        }

    # -- Workitem creation --

    def create_workitem(
        self,
        subject: str,
        assigned_to: str,
        parent_id: str,
        space_id: str,
        workitem_type_id: str,
        sprint_id: str = "",
        description: str = "",
        custom_field_values: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Create a workitem (task) via POST /oapi/v1/projex/organizations/{org}/workitems.

        Args:
            subject: Task title.
            assigned_to: Assignee user ID.
            parent_id: Parent workitem ID (the requirement).
            space_id: Project ID.
            workitem_type_id: Workitem type ID (e.g. task type).
            sprint_id: Sprint ID to assign.
            description: Task description.
            custom_field_values: Custom field values to set at creation time,
                keyed by field identifier (e.g. {"101586": "8"}).

        Returns:
            Created workitem dict from API response.
        """
        org_id = _urlencode(self.config["organization_id"])
        body: dict[str, Any] = {
            "subject": subject,
            "assignedTo": assigned_to,
            "parentId": parent_id,
            "spaceId": space_id,
            "workitemTypeId": workitem_type_id,
            "formatType": "RICHTEXT",
        }
        if sprint_id:
            body["sprint"] = sprint_id
        if description:
            body["description"] = description
        if custom_field_values:
            body["customFieldValues"] = custom_field_values

        response = _request_json(
            self.config,
            "POST",
            f"/oapi/v1/projex/organizations/{org_id}/workitems",
            body,
        )
        if isinstance(response, dict):
            for key in ("result", "data", "workitem"):
                nested = response.get(key)
                if isinstance(nested, dict):
                    return nested
            if "id" in response or "identifier" in response:
                return response
        return response or {}

    def update_workitem_custom_field(
        self,
        workitem_id: str,
        field_identifier: str,
        field_value: str,
    ) -> dict[str, Any]:
        """Update a custom field on a workitem.

        Uses the legacy API: POST /organization/{org}/workitems/updateWorkitemField
        (works with x-yunxiao-token auth on center edition too).

        Args:
            workitem_id: Workitem ID.
            field_identifier: Custom field identifier (e.g. "101586" for 预计工时).
            field_value: Value to set (string).
        """
        org_id = _urlencode(self.config["organization_id"])
        body = {
            "identifier": workitem_id,
            "fieldType": "customField",
            "propertyKey": field_identifier,
            "propertyValue": field_value,
        }
        response = _request_json(
            self.config,
            "POST",
            f"/organization/{org_id}/workitems/updateWorkitemField",
            body,
        )
        return response if isinstance(response, dict) else {}


# ---------------------------------------------------------------------------
# Team config helpers
# ---------------------------------------------------------------------------

def load_team(file_config: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract team members from config, returning a list of member dicts.

    Each member dict has: name, role, is_tl, exempt, yunxiao_id, required_hours.
    exempt members (e.g. 产品) have required_hours = 0.
    """
    team_config = file_config.get("team") or {}
    raw_members = team_config.get("members") or []
    workload = team_config.get("workload") or {}
    default_hours = workload.get("default_hours_per_sprint", 76)
    tl_hours = workload.get("tl_hours_per_sprint", 72)

    members: list[dict[str, Any]] = []
    for m in raw_members:
        if not isinstance(m, dict):
            continue
        is_tl = bool(m.get("is_tl", False))
        exempt = bool(m.get("exempt", False))
        if exempt:
            hours = 0
        elif is_tl:
            hours = tl_hours
        else:
            hours = default_hours
        members.append({
            "name": str(m.get("name") or ""),
            "role": str(m.get("role") or ""),
            "is_tl": is_tl,
            "exempt": exempt,
            "yunxiao_id": str(m.get("yunxiao_id") or ""),
            "required_hours": hours,
        })
    return members


def team_members_by_role(members: list[dict[str, Any]], role: str) -> list[dict[str, Any]]:
    """Filter team members by role (case-insensitive)."""
    needle = role.strip().lower()
    return [m for m in members if m["role"].strip().lower() == needle]


def team_member_by_name(members: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    """Find a team member by name (case-insensitive)."""
    needle = name.strip().lower()
    for m in members:
        if m["name"].strip().lower() == needle:
            return m
    return None


def team_member_by_yunxiao_id(members: list[dict[str, Any]], yunxiao_id: str) -> dict[str, Any] | None:
    """Find a team member by yunxiao_id (case-insensitive)."""
    needle = yunxiao_id.strip().lower()
    if not needle:
        return None
    for m in members:
        if m["yunxiao_id"].strip().lower() == needle:
            return m
    return None


def team_creator_ids(members: list[dict[str, Any]]) -> list[str]:
    """Return all non-empty yunxiao_ids from team members."""
    return [m["yunxiao_id"] for m in members if m["yunxiao_id"].strip()]


def team_creator_ids_by_role(members: list[dict[str, Any]], role: str) -> list[str]:
    """Return yunxiao_ids for team members matching the given role."""
    return [m["yunxiao_id"] for m in team_members_by_role(members, role) if m["yunxiao_id"].strip()]
