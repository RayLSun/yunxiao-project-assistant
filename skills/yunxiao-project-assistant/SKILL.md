---
name: yunxiao-project-assistant
description: Assist with Alibaba Cloud DevOps Yunxiao project collaboration. Use when the user asks to query, analyze, create, update, comment on, triage, summarize, or synchronize Projex requirements, work items, tasks, blockers, owners, statuses, iterations, fields, or related project-management data in Yunxiao. Supports two modes - Python scripts for stable recurring workflows, and Yunxiao MCP Server for flexible exploratory queries.
---

# Yunxiao Project Assistant

## Overview

Two complementary modes for Yunxiao Projex project collaboration:

1. **Python scripts** (`scripts/`) - Stable, repeatable workflows. Use for fixed processes like sprint export, PRD data fetching, and filtering requirements by creator. Each script reads the same `config/yunxiao_config.json`.

2. **Yunxiao MCP Server** - Flexible, AI-driven queries. Use for one-off exploration: "analyze this requirement", "check the comments on AIGC-1234", "what's the status of all items assigned to Zhang San?".

Both modes share the same authentication (personal access token) and organization/project context.

## When To Use Which

| Scenario | Mode | Why |
|---|---|---|
| Export sprint work items to Excel | Script: `export_sprint.py` | Fixed format, repeatable, stable output |
| Fetch requirement data for PRD generation | Script: `fetch_for_prd.py` | Structured data fetch, AI generates PRD from JSON |
| List requirements filtered by creator | Script: `list_requirements.py` | Exact filter, deterministic results |
| Discover creator IDs in a project | Script: `list_requirements.py --dump-creators` | Discovery helper |
| Export team-created requirements to Excel | Script: `export_team_requirements.py` | Sprint + team creator filter, single Excel |
| Export sprint workload statistics | Script: `export_sprint_workload.py` | Per-person 预计工时 vs required hours |
| Auto-create tasks to fill workload gaps | Script: `create_tasks.py` | Creates tasks under requirements for under-hour members |
| Analyze a requirement (description, comments, fields) | MCP | Flexible, multi-tool, AI synthesizes |
| Check status / assignee across items | MCP | Ad-hoc, user-driven scope |
| Update status, add comment, create child task | MCP | Write operations, AI confirms before writing |
| "What did Zhang San work on last sprint?" | Either | Script for raw data, MCP for analysis |

## Script Reference

All scripts live in `scripts/` and share `config/yunxiao_config.json` (copy from `config/yunxiao_config.example.json`). The shared library is `lib/yunxiao_client.py`.

### Setup

```bash
cp config/yunxiao_config.example.json config/yunxiao_config.json
# Edit config/yunxiao_config.json: set yunxiao_token, organization_id, project_id
```

### export_sprint.py - Export Sprint To Excel

```bash
# Export requirements + tasks from current sprint (auto-resolve by keyword)
python scripts/export_sprint.py --sprint-keyword "招聘" --kind all

# Direct sprint id
python scripts/export_sprint.py --sprint-id 06175757e51bfd3f2aa47f3e60 --kind all

# Requirements only / tasks only
python scripts/export_sprint.py --kind req
python scripts/export_sprint.py --kind task

# Discover custom field names for Excel headers
python scripts/export_sprint.py --kind task --dump-sample 3
```

Output: `exports/{sprint}_requirements.xlsx`, `exports/{sprint}_tasks.xlsx`. Task export includes an auto-generated 工时汇总 summary sheet.

### fetch_for_prd.py - Fetch Requirement Data For PRD

```bash
# By sprint keyword
python scripts/fetch_for_prd.py --sprint-keyword "招聘"

# By requirement name (searches across all sprints)
python scripts/fetch_for_prd.py --requirement-keyword "性别兜底"

# Direct sprint id
python scripts/fetch_for_prd.py --sprint-id 06175757e51bfd3f2aa47f3e60
```

Output: JSON on stdout. The agent uses this JSON to generate PRD markdown files (following `config/template.md` structure) into `prds/`.

### list_requirements.py - List Requirements By Creator / Role

```bash
# List all requirements by specific creators
python scripts/list_requirements.py --creator-id abc123 --creator-id def456

# Filter by sprint + creator
python scripts/list_requirements.py --sprint-keyword "招聘" --creator-id abc123

# Filter by team role (uses config team.members)
python scripts/list_requirements.py --role 后端研发

# Filter by all team members
python scripts/list_requirements.py --team

# Show team roster with workload expectations
python scripts/list_requirements.py --show-team

# Discover user IDs (scans both requirement creators AND task assignees)
python scripts/list_requirements.py --dump-creators

# Table format instead of JSON
python scripts/list_requirements.py --format table
```

Output: JSON on stdout (default) or formatted table. JSON includes id, serialNumber, subject, status, assignedTo, creator, descriptionText, customFields.

### export_team_requirements.py - Export Team Requirements To Excel

Exports all requirements created by team members in a specific sprint to a single Excel file.

```bash
python scripts/export_team_requirements.py --sprint-name "Sprint260728"

# JSON format instead of Excel
python scripts/export_team_requirements.py --sprint-name "Sprint260728" --format json
```

Output: `doc/requirements/{sprint}/{sprint}_requirements.xlsx`. Uses `team.members[].yunxiao_id` as creator filter (all non-exempt members). Excel includes standard fields + auto-discovered custom fields.

### export_sprint_workload.py - Export Sprint Workload Statistics

Aggregates 预计工时 from tasks (whose parent is a requirement in the sprint) per team R&D member, comparing against required hours.

```bash
python scripts/export_sprint_workload.py --sprint-name "Sprint260728"
```

Output: `doc/workload/{sprint}/{sprint}_workload.xlsx` with two sheets:
- **工时汇总**: per-person summary (name, role, TL, task count, hours, required, diff, status). Red=under, green=met/over.
- **任务明细**: every task with serial, subject, assignee, role, hours, status, parent req serial+subject.

Only counts tasks whose `parentId` points to a requirement in this sprint. Excludes exempt members (产品). Members with 0 tasks appear with full gap shown.

### create_tasks.py - Auto-Create Tasks To Fill Workload Gaps

For members in `task_creation.members_to_fill` whose current workload < required hours, finds requirements where they are a participant, creates child tasks with role-prefixed names until the gap is filled.

```bash
# Preview what would be created (no writes)
python scripts/create_tasks.py --sprint-name "Sprint260728" --dry-run

# Actually create tasks
python scripts/create_tasks.py --sprint-name "Sprint260728"
```

Logic:
1. Calculate current hours per member from tasks (parent in this sprint's requirements)
2. For each member with a gap, find requirements where they are a `participant`
3. Greedily create tasks with hours from `available_hours` [2, 4, 8] (largest that fits)
4. Task names: `{role_prefix}{requirement_subject[:40]}` for the first task, then `{role_prefix}{requirement_subject[:40]}-2`, `-3`, etc. for subsequent tasks under the same requirement. The script scans existing tasks under each requirement (matching base name and `-N` suffix pattern) and planned tasks to ensure unique names. No two tasks under the same requirement will share a name.
5. Inherit custom fields from parent requirement (only fields enabled on task type) + set 预计工时 at creation time
6. Rotates across candidate requirements; same requirement can have multiple tasks for same person

**Safe execution workflow** (user preference):
1. ALWAYS run `--dry-run` first and show the plan to the user
2. Create ONE task as a test (via execute_code, not the script)
3. Ask the user to verify it in Yunxiao web UI (task name, assignee, hours, parent, inherited fields)
4. Only after user confirms, run the full batch without `--dry-run`

PITFALL: CreateWorkitem API rejects requests if required custom fields are missing. The API returns errors like "字段【预计工时】不能为空" and "字段【业务系统】不能为空". Custom fields must be passed in the `customFieldValues` body parameter at creation time, NOT set after creation via UpdateWorkitemField.

PITFALL: Not all requirement custom fields are enabled on the task type. The API returns "当前工作项类型下，未启用此字段【成本分摊部门】" if you try to inherit a field the task type doesn't support. FIX: Before creating tasks, fetch an existing task and extract its `customFieldValues[].fieldId` set. Only inherit fields from the parent requirement whose `fieldId` appears in that set (excluding `sumPlanedLaborHour` which is a computed field).

PITFALL: The search API returns `parentId` (not `parentIdentifier` as the OpenAPI docs suggest). Always use `parentId` when checking parent-child relationships.

### --dump-creators: Dual-Scan Discovery + Auto-Match

CRITICAL: Developers rarely create requirements in Yunxiao -- they are assigned tasks. The `--dump-creators` command scans BOTH:

1. All requirements in the project -> extracts `creator` field
2. All tasks in the project -> extracts `assignedTo` field

It merges both into a single people list, showing each person's ID, name, and source (creator / assignee / both).

**Auto-match flow** (when team config has members with empty `yunxiao_id`):
1. `--dump-creators` fetches all requirements and tasks
2. Extracts unique people from `creator` (req) and `assignedTo` (task) fields
3. Matches by exact name against `team.members[].name`
4. Writes matched `yunxiao_id` values back to `config/yunxiao_config.json` automatically
5. Reports any still-unmatched members for manual identification

If all team members already have `yunxiao_id` filled, it skips matching and just shows the people list.

### Team Configuration

The config file supports a `team` section with member roster and workload rules:

```json
{
  "team": {
    "members": [
      {"name": "张三", "role": "后端研发", "is_tl": true, "yunxiao_id": ""},
      {"name": "李四", "role": "后端研发", "is_tl": false, "yunxiao_id": ""},
      {"name": "王五", "role": "产品", "is_tl": false, "exempt": true, "yunxiao_id": ""}
    ],
    "workload": {
      "default_hours_per_sprint": 76,
      "tl_hours_per_sprint": 72
    }
  }
}
```

- `exempt: true` -- marks roles that don't fill timesheets (e.g. 产品). Their `required_hours` is 0.
- `is_tl: true` -- TL workload (72h by default). Non-TL R&D get 76h.
- `yunxiao_id` -- Yunxiao internal user ID (NOT the username). Required for `--role` and `--team` filters.

**Auto-fill yunxiao_id workflow**: When the user has filled `name` but left `yunxiao_id` empty:
1. Run `python scripts/list_requirements.py --dump-creators` -- it scans both requirements (creators) and tasks (assignees) to find all people.
2. Auto-matches by exact name against `team.members[].name` (case-insensitive, whitespace-trimmed).
3. Writes matched `yunxiao_id` values back to `config/yunxiao_config.json` automatically.
4. If a name doesn't auto-match, show the user the dump list and ask them to identify the correct ID.
5. Verify with `python scripts/list_requirements.py --show-team`.

PITFALL: Developers who never created a requirement will NOT appear in the creator-only scan. The dual-scan (creators + assignees) is essential to find all team members.

### Task Creation Configuration

The config file supports a `task_creation` section for auto-creating tasks to fill workload gaps:

```json
{
  "task_creation": {
    "workitem_type_id": "ba102e46bc6a8483d9b7f25c",
    "hours_field_id": "101586",
    "available_hours": [2, 4, 8],
    "role_prefixes": {
      "后端研发": "【后端】",
      "前端研发": "【前端】",
      "RPA研发": "【RPA】"
    },
    "members_to_fill": ["孙磊", "夏鹏程"]
  }
}
```

- `workitem_type_id` -- Task workitem type ID (discover from existing task's `workitemType.id`)
- `hours_field_id` -- Custom field ID for 预计工时 (discover from `customFieldValues[].fieldId`)
- `available_hours` -- Allowed hour values per task; the script picks the largest that fits the remaining gap
- `role_prefixes` -- Maps role to task name prefix
- `members_to_fill` -- Names of members who need workload gap filled (must match `team.members[].name`)

PITFALL: `workitem_type_id` and `hours_field_id` are org-specific. Discover them by fetching a sample task and inspecting its `workitemType.id` and `customFieldValues` before configuring.

## MCP Mode

When the request is exploratory or requires multi-tool synthesis, use the configured Yunxiao MCP Server. The MCP tools provide direct read/write access to work items, comments, fields, iterations, and statuses.

### Workflow

1. Identify the scope: organization, project, work item IDs, iteration, assignee, status, date range, and the user's intended outcome.
2. Discover the available Yunxiao MCP tools before calling them. Do not assume exact tool names; search for Yunxiao, Projex, project, work item, task, comment, field, or iteration tools exposed by the host.
3. Read before writing. Fetch the relevant work item, project metadata, allowed fields/statuses, and recent comments before deciding what to create or update.
4. Execute read-only requests directly when enough scope is available.
5. For write actions, proceed only when the target and payload are unambiguous. If an update could affect the wrong item, status, assignee, iteration, or field, ask one concise clarification before calling the write tool.
6. Return the result with stable identifiers: organization/project names, work item IDs, titles, changed fields, comments added, status transitions, and links when tools provide them.

### Tool Selection

- Prefer the Yunxiao `project-management` toolset for requirements, tasks, work items, comments, fields, iterations, and work logs.
- Use `organization-management` only when the organization, members, departments, or roles must be resolved.
- Use `code-management` only when a project task explicitly depends on repositories, branches, commits, files, or merge requests.
- Avoid enabling or relying on all Yunxiao toolsets for normal requirement/task handling; the official server exposes many tools and broad tool lists waste context.

### Common MCP Requests

**Find And Summarize Work**: Resolve the project and filters, list matching work items via MCP tools, then summarize by status, owner, priority, due date, blockers, and next action.

**Analyze A Requirement**: Fetch the work item via MCP, then its comments, custom fields, parent/child items, and linked tasks. Produce a concise analysis covering goal, current state, gaps, acceptance criteria, implementation implications, and recommended next updates.

**Create Or Update Tasks**: Before writing, confirm the target project and required fields are known. Map natural-language instructions into Yunxiao fields conservatively. After the tool call, report exactly what was created or changed.

**Add Comments Or Status Notes**: Use the user's wording when they provide exact comment text. If they ask for a drafted update, keep it factual, short, and tied to current project evidence from the work item.

## Pitfalls

- **Config file vs example**: Users may accidentally edit `config/yunxiao_config.example.json` (the template) instead of `config/yunxiao_config.json` (the real config). The example file is tracked in git and contains placeholders. If real data appears in the example, restore it to placeholders and migrate the data to `yunxiao_config.json`. The real config is gitignored.
- **Windows path quirk with write_file**: The `write_file` and `patch` tools on Windows resolve MSYS-style paths like `/d/SunProjects/...` incorrectly to `C:\d\SunProjects\...`. Always use forward-slash Windows paths (e.g. `D:/SunProjects/...`).
- **load_config_file type check**: The function accepts `str | Path | None`. Early versions only accepted `Path`, which caused `AttributeError: 'str' object has no attribute 'exists'` when called with a string path. Fixed in current version.

## First-run Onboarding

When the script fails with `Missing required config`, or the user asks "where is the config" / "how do I set this up":

1. Run `python scripts/export_sprint.py --show-config-path` (or any script with `--show-config-path`). It prints the absolute path and whether the file exists.
2. If the file does not exist: `cp config/yunxiao_config.example.json config/yunxiao_config.json`
3. Collect missing values from the user:
   - `yunxiao_token` - personal access token (from 云效 -> 个人设置 -> 个人访问令牌)
   - `organization_id` - 组织管理后台 -> 基本信息 -> 组织 ID
   - `project_id` - the segment after `/projex/project/` in the web URL
4. Write the config file. Never echo the token back to the user.
5. Rerun the original task.

## Safety

- Never print or store Yunxiao access tokens.
- Prefer token delivery through `x-yunxiao-token` header (scripts) or `Authorization: Bearer ***` header (MCP). Use query-string tokens only as a last resort.
- Treat Yunxiao as a live production system. Do not bulk-edit, close, delete, reassign, or transition work items unless the user explicitly asks for that operation and the target set is clear.
- When a tool or script returns incomplete data, say what is missing instead of inventing field values or statuses.
- Keep `config/yunxiao_config.json` out of version control - `.gitignore` already excludes it.

## References

- Read `references/yunxiao-mcp.md` when configuring or troubleshooting Yunxiao MCP access.
- Read `references/project-workflows.md` for playbooks for requirement analysis, task triage, comments, and status updates.
- Read `references/yunxiao-openapi-patterns.md` for API patterns: SearchWorkitems conditions, creator filter, description enrichment, pagination, response extraction.
