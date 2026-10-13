# Yunxiao Requirement Types & Creation Patterns

Discovered via API probing (2026-07-30) on center-edition.

## Requirement Type IDs

Two requirement workitem types in this project:

| Keyword | Type ID | Name | Required Custom Fields |
|---|---|---|---|
| 技术 | `bca48ee2a0976d38f4802fae` | 技术类需求 | 里程碑, 业务系统, 成本分摊部门 |
| 业务 | `b759681e33af77f5c8620cbc` | 业务类需求 | 里程碑, 业务系统, 成本分摊部门, 2026预算编号 |

PITFALL: `workitemTypeId` is org/project-specific. If this project changes, re-discover by fetching existing requirements and reading `workitemType.id`.

PITFALL: `业务类需求` has an extra required field (2026预算编号) that `技术类需求` does not. Creating a 业务 requirement without it returns HTTP 400.

## Required Fields at Creation Time

All requirement types require:
1. `subject` (title)
2. `assignedTo` (user ID — empty returns "指派人不能为空")
3. Custom fields (see table above — returns "字段【X】不能为空" for each missing one)

Default values used when user doesn't specify:
- 里程碑: `"/"`
- 业务系统: `"AI-招聘助理"`
- 成本分摊部门: `"DEC"` (技术) / `"WFS"` (业务)
- 2026预算编号: `"/"` (业务 only)

## How to Discover Types

Fetch a sample requirement and read `workitemType`:

```python
items = client.search_workitems(project_id, category="Req", page=1, per_page=5)
for item in items:
    wi = item.get("workitemType") or {}
    print(f"type_id={wi.get('id')} | name={wi.get('name')}")
```

Or probe each type by attempting creation with minimal fields and reading the error messages — the API tells you which fields are missing one by one.

PITFALL: The `/workitemtypes` endpoint (`GET /oapi/v1/projex/organizations/{org}/projects/{proj}/workitemtypes`) returns 404 on center-edition. Discover types from existing workitems instead.

## API Response on Create

The creation API (`POST /oapi/v1/projex/organizations/{org}/workitems`) returns only `{"id": "..."}` — no `serialNumber`, no `subject`. To get the serial number, immediately fetch the detail endpoint:

```python
detail = client.fetch_workitem_detail(new_id)
serial = detail.get("serialNumber")  # e.g. "HWTQ-2875"
```

The `create_workitem()` method in `yunxiao_client.py` tries to extract from nested keys but the create response only has a flat `{"id": "..."}` structure — the extraction logic returns the full dict (which is correct), but the agent must still call `fetch_workitem_detail()` to get the serial number.

## No Parent for Top-Level Requirements

When creating a requirement (not a task), `parentId` is not needed. Pass `parent_id=""` to `create_workitem()`. The method requires it as a positional arg, but empty string works.

## Sprint Assignment

Per user preference: do NOT assign sprint at creation time. The user handles sprint assignment manually in the Yunxiao web UI after receiving the link. Pass no `sprint_id` (omit from body, or pass empty).

## Workitem URL Format

Same as tasks: `https://devops.aliyun.com/projex/project/{project_id}/task/{workitem_id}`

## Multi-Agent Support Files

The source repo includes `CLAUDE.md` (for Claude Code) and `AGENTS.md` (for OpenAI Codex) at project root. These contain script usage instructions so any agent that recognizes those files can use the scripts. Keep them in sync with SKILL.md when scripts change. See `CLAUDE.md` and `AGENTS.md` in the source project.
