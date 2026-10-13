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
| Export sprint tasks filtered by role | Script: `export_sprint_tasks_by_role.py` | Per-role task list with 链接 column + 工时汇总. Supports `--role`, `--all`, `--no-descriptions` |
| Auto-create tasks to fill workload gaps | Script: `create_tasks.py` | Creates tasks under requirements for under-hour members |
| Create a requirement with title & description | Script: `create_requirement.py` | Creates a requirement (技术类/业务类), returns serialNumber + web URL |
| Fetch ONE requirement by serialNumber (e.g. `LMAV-9`) | Script: `get_requirement.py` | Direct serialNumber lookup, resolves the project from the serial prefix |
| List projects / find which project a code belongs to | Script: `get_requirement.py --list-projects` | Enumerates org projects with their codes |
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

Exports all requirements created by team members in a specific sprint to a single Excel file with three sheets:

1. **需求列表** - all team-created requirements with auto-discovered custom fields
2. **任务明细** - child tasks under those requirements (任务编号, 任务标题, 负责人, 角色, 预计工时, 任务迭代, 状态, 父需求编号, 父需求标题, 链接). The 任务迭代 column shows the task's own sprint (may differ from the parent requirement's sprint). The 链接 column is a clickable hyperlink to the Yunxiao web UI.
3. **工时汇总** - per-person summary (姓名, 角色, 任务数, 预计工时) with totals row

```bash
python scripts/export_team_requirements.py --sprint-name "Sprint260728"

# JSON format instead of Excel (no task/workload sheets in JSON mode)
python scripts/export_team_requirements.py --sprint-name "Sprint260728" --format json
```

Output: `doc/requirements/{sprint}/{sprint}_requirements.xlsx`. Uses `team.members[].yunxiao_id` as creator filter — this is ALL members with a non-empty `yunxiao_id`, INCLUDING exempt 产品 members. The `exempt` flag only zeroes `required_hours` for workload stats; it does NOT exclude a member from the creator filter. The 任务明细 sheet only includes tasks whose `parentId` points to one of the exported requirements. The 工时汇总 sheet aggregates 预计工时 by assignee name across all child tasks. Child tasks are fetched project-wide (not sprint-filtered) to include tasks whose sprint differs from their parent requirement's sprint.

### export_sprint_workload.py - Export Sprint Workload Statistics

Aggregates 预计工时 from tasks (whose parent is a requirement in the sprint) per team R&D member, comparing against required hours.

```bash
python scripts/export_sprint_workload.py --sprint-name "Sprint260728"
```

Output: `doc/workload/{sprint}/{sprint}_workload.xlsx` with two sheets:
- **工时汇总**: per-person summary (name, role, TL, task count, hours, required, diff, status). Red=under, green=met/over.
- **任务明细**: every task with serial, subject, assignee, role, hours, status, parent req serial+subject.

Only counts tasks whose `parentId` points to a requirement in this sprint. Tasks are fetched project-wide (not sprint-filtered) to include tasks whose sprint differs from their parent requirement's sprint. Excludes exempt members (产品). Members with 0 tasks appear with full gap shown.

### Re-running exports (common workflow)

Sprint workload and team-requirement exports are designed to be re-run repeatedly as the sprint progresses. Each run overwrites the previous Excel file. When the user says "再帮我统计一次" / "re-run" / "数据已经更新", simply re-run the same command — do NOT ask for confirmation or re-read the skill.

After each workload re-run, render the summary table in chat AND highlight what changed since the last run (task count delta, per-person hour deltas, status transitions e.g. 不足→达标→超出). This delta view is the primary value to the user on re-runs.

### Workload vs team-requirement scope difference (explain to user)

`export_sprint_workload.py` and `export_team_requirements.py` produce DIFFERENT per-person hour totals for the same sprint. This is by design, not a bug:

- `export_sprint_workload.py` — counts ALL tasks whose parent requirement is in the sprint (full sprint scope, includes tasks from non-team-created requirements). This is the **management view** of total R&D workload.
- `export_team_requirements.py` — counts only child tasks whose parent requirement was CREATED by a team member (team scope). This is the **team delivery view**.

When the user runs both, proactively note which scope each number represents so they don't confuse the two totals.

### export_sprint_tasks_by_role.py - Export Sprint Tasks By Role

Exports all tasks in a sprint, filtered by assignee role (using `team.members` config), to a single Excel file with clickable 链接 column and 工时汇总 sheet.

```bash
# Export all 后端研发 tasks
python scripts/export_sprint_tasks_by_role.py --sprint-name "Sprint260728" --role 后端研发

# Export 前端研发 tasks
python scripts/export_sprint_tasks_by_role.py --sprint-name "Sprint260728" --role 前端研发

# Export all non-exempt team members' tasks (all R&D roles)
python scripts/export_sprint_tasks_by_role.py --sprint-name "Sprint260728" --all

# Custom output filename
python scripts/export_sprint_tasks_by_role.py --sprint-name "Sprint260728" --role 后端研发 --output-name "后端任务总表"

# Skip description fetching (faster, description column will be empty)
python scripts/export_sprint_tasks_by_role.py --sprint-name "Sprint260728" --role 后端研发 --no-descriptions
```

Output: `exports/{sprint}_{role}_tasks.xlsx` (or `{sprint}_all_tasks.xlsx` with `--all`, or custom name with `--output-name`). Excel includes:
- **任务明细** sheet: all filtered tasks with auto-discovered columns. A clickable 链接 column (Yunxiao web URL) is inserted before 标题.
- **工时汇总** sheet: per-person summary (name, task count, 预计工时 sum) with totals row.

Filters by `assignedTo` field matching `team.members[].yunxiao_id` for the given role. Requires `yunxiao_id` to be filled in config.

Workitem URL format: `https://devops.aliyun.com/projex/project/{project_id}/task/{workitem_id}`

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

**Auto-fill yunxiao_id workflow**: When the user has filled `name` but left `yunxiao_id` empty (including after adding a new 产品 member, who may be a primary requirement creator):
1. **Run `--dump-creators` proactively** — don't wait for an export to silently drop data. Any newly added member (especially 产品) should have their `yunxiao_id` filled before the next requirement/team export, even if they are `exempt: true`.
2. `--dump-creators` scans both requirements (creators) and tasks (assignees) to find all people.
3. Auto-matches by exact name against `team.members[].name` (case-insensitive, whitespace-trimmed).
4. Writes matched `yunxiao_id` values back to `config/yunxiao_config.json` automatically.
5. If a name doesn't auto-match, show the user the dump list and ask them to identify the correct ID.
6. Verify with `python scripts/list_requirements.py --show-team`.

PITFALL: Developers who never created a requirement will NOT appear in the creator-only scan. The dual-scan (creators + assignees) is essential to find all team members.

### External Members (跨组帮忙研发)

When developers from other teams help out on your product's requirements, their tasks will appear in the 任务明细 and 工时汇总 sheets. To show their role correctly, add them to `team.external_members` in config:

```json
{
  "team": {
    "members": [ ... ],
    "workload": { ... },
    "external_members": [
      {"name": "涂冉", "role": "后端研发", "yunxiao_id": "xxxxxxxx"}
    ]
  }
}
```

- `name` -- must match the assignee name returned by the Yunxiao API (used for role lookup in task/workload sheets).
- `role` -- free text, typically 后端研发 / 前端研发 / RPA研发.
- `yunxiao_id` -- Yunxiao internal user ID (optional for role lookup, but recommended for completeness).

External members are NOT included in the team creator filter (they don't affect which requirements are exported). They are only used to resolve role labels in the 任务明细 and 工时汇总 sheets. If an assignee is not found in either `team.members` or `team.external_members`, their role column will be blank.

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

### create_requirement.py - Create A Requirement

Creates a requirement (需求) in Yunxiao from the agent's natural-language invocation. The user says something like "创建需求：实现XXX功能" and the agent extracts the title + description, calls this script, and returns the web URL.

**Agent workflow** (no --dry-run needed for single requirement creation):
1. Parse user's input: extract title (first line / short description) and description (rest of the content).
2. Determine type (default: 业务). If user says "技术类需求" or mentions 技术, use 技术 type.
3. Determine assignee (default: first non-exempt team member). If user specifies someone, use their name.
4. Run the script. On success, return the serialNumber and clickable URL to the user.
5. Do NOT set sprint — the user will assign it manually after creation.

```bash
# Default: 技术类需求, first non-exempt team member as assignee
python scripts/create_requirement.py --title "需求标题" --description "详细描述"

# Specify type (技术/业务) and assignee
python scripts/create_requirement.py --title "标题" --description "描述" --type 技术 --assignee "孙磊"

# Specify custom field values
python scripts/create_requirement.py --title "标题" --description "描述" --milestone "一键发布岗位" --biz-system "AI-招聘助理" --cost-dept "DEC"

# Business type (requires extra 2026预算编号 field)
python scripts/create_requirement.py --title "标题" --description "描述" --type 业务 --biz-system "iStaffing-SSC" --cost-dept "WFS"

# Preview
python scripts/create_requirement.py --title "标题" --description "描述" --dry-run

# Show available types / assignees
python scripts/create_requirement.py --show-types
python scripts/create_requirement.py --show-assignees
```

Output: JSON on stdout with `id`, `serialNumber`, `subject`, `type`, `assignee`, `url`.

**Requirement types** (discovered from existing workitems in this project):
| Keyword | Type Name | workitemTypeId | Required Custom Fields |
|---|---|---|---|
| 技术 | 技术类需求 | `bca48ee2a0976d38f4802fae` | 里程碑, 业务系统, 成本分摊部门 |
| 业务 | 业务类需求 | `b759681e33af77f5c8620cbc` | 里程碑, 业务系统, 成本分摊部门, 2026预算编号 |

**Default values** (when user doesn't specify): 里程碑="/", 业务系统="AI-招聘助理", 成本分摊部门=type default (DEC for 技术, WFS for 业务), 2026预算编号="/".

PITFALL: Each requirement type has DIFFERENT required custom fields. 业务类需求 requires 2026预算编号 but 技术类需求 does not. The script hardcodes the `REQ_TYPE_MAP` with per-type required fields — update this map if new types are added.

PITFALL: The CreateWorkitem API only returns `{"id": "..."}` — no `serialNumber`, `subject`, or other fields. The script calls `fetch_workitem_detail()` as a second request to get the serialNumber. If the detail call fails, the URL is still constructed correctly from the id.

PITFALL: `assignedTo` is required for requirement creation (API returns "指派人不能为空"). If the user doesn't specify, the script defaults to the first non-exempt team member with a yunxiao_id. User preference: they will manually adjust assignee and other fields in the web UI after creation.

See `references/yunxiao-openapi-patterns.md` for requirement creation API details and field IDs.

### get_requirement.py - Fetch ONE Requirement By serialNumber

Fetches a single workitem by its serialNumber, resolving the project from the serial prefix. Use this when the user names a specific item ("拉取需求 LMAV-9", "看看 HWTQ-1509") instead of going through `fetch_for_prd.py` (which needs a sprint or a subject keyword).

```bash
# By serialNumber — the project is derived from the prefix
python scripts/get_requirement.py --serial LMAV-9

# Human-readable block instead of JSON, and dump the description to a file
python scripts/get_requirement.py --serial LMAV-9 --format text --save-text /tmp/lmav9.md

# Explicit project (id, short code, or name)
python scripts/get_requirement.py --serial LMAV-9 --project LMAV

# Skip the detail request when you only need the fields (faster)
python scripts/get_requirement.py --serial LMAV-9 --no-description

# Enumerate projects (codes + ids) — also the way to discover a prefix
python scripts/get_requirement.py --list-projects --format text
```

Output (JSON, default): `{project: {code,name,id}, category, url, workitem: {...shaped..., descriptionText}}`. Add `--raw` to also include the raw detail payload.

Project resolution order: explicit `--project` → the project whose code equals the serial prefix → the configured default. The full org scan only happens with `--all-projects`, so a typo normally fails in ~3s rather than scanning every project.

PITFALL: the configured `project_id` is NOT a constraint. Projects are independent — `LMAV-9` resolves from a client configured for `HWTQ`, which is the whole point of this script. Do not "fix" a missing item by switching the config's `project_id`; that would break the sprint/team exports built on it.

PITFALL: a serialNumber does not say whether the item is a requirement or a task (`HWTQ-1509` is a Task, `LMAV-9` is a requirement), so all three categories are searched by default. Narrow with `--category` only when you already know.

### Requirement → PRD

`get_requirement.py` is the single-item counterpart to `fetch_for_prd.py`: use it to pull one requirement's `descriptionText`, then write the PRD with `config/template.md` (背景与目标 / 用户场景与故事 / 功能需求 / 非功能需求 / 验收标准 / 风险与依赖). Write generated PRDs into `prds/` (gitignored).

When the source description already reads like a finished document, the PRD's value is what it ADDS: User Stories in "作为…我希望…以便…" form, functional points as 输入→处理→输出, Given-When-Then acceptance criteria including the exception branches, and non-functional baselines. Mark genuinely unspecified items 「待补充」 rather than inventing them, and never silently reconcile contradictory source text — list the contradictions under 待澄清事项 and keep both readings.

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

- **Project list 404 (CRITICAL)**: `GET /oapi/v1/projex/organizations/{orgId}/projects` returns HTTP 404. The working endpoint is `POST …/projects:search` with body `{"page":1,"perPage":100}`. A 404 here is NOT a permissions or token problem — do not go hunting for allowlist issues.
- **Config dict key is `token`, not `yunxiao_token`**: `build_config_from_sources()` renames the field. Always read `config["token"]`; `config["yunxiao_token"]` is always empty, which makes a working config look empty. The JSON file keeps the original `yunxiao_token` spelling.
- **`ExpiredToken` (HTTP 401)**: personal access tokens expire. Before asking the user for a new one, check other Yunxiao configs on the machine — a sibling skill's config (e.g. `~/.claude/skills/yunxiao-sprint-export/config/yunxiao_config.json`) often holds a longer-lived token. Verify it with a cheap search first, write it into the skill config (backup the old file), and never echo the token.
- **A serialNumber already identifies its project**: the prefix (`LMAV` in `LMAV-9`) is the project's `customCode`, so `get_requirement.py --serial LMAV-9` works from a config whose `project_id` points at a completely different project. Never change the configured `project_id` to reach an item in another project — the team config and sprint exports depend on it.
- **Config file vs example**: Users may accidentally edit `config/yunxiao_config.example.json` (the template) instead of `config/yunxiao_config.json` (the real config). The example file is tracked in git and contains placeholders. If real data appears in the example, restore it to placeholders and migrate the data to `yunxiao_config.json`. The real config is gitignored.
- **Config missing from project dir but exists in skill install dir**: When running scripts from the source project (`D:/SunProjects/yunxiao-project-assistant`), `config/yunxiao_config.json` may be absent (gitignored, not present after fresh clone/cleanup). The installed skill copy at `~/AppData/Local/hermes/skills/yunxiao-project-assistant/config/yunxiao_config.json` typically holds the real config (placed there during skill setup). Before asking the user for token/org/project, ALWAYS check the skill install dir first and copy the config over: `cp ~/AppData/Local/hermes/skills/yunxiao-project-assistant/config/yunxiao_config.json <project_dir>/config/yunxiao_config.json`. The org ID and project ID are also in Hermes memory as a fallback.
- **Windows path quirk with write_file**: The `write_file` and `patch` tools on Windows resolve MSYS-style paths like `/d/SunProjects/...` incorrectly to `C:\\d\\SunProjects\\...`. Always use forward-slash Windows paths (e.g. `D:/SunProjects/...`).
- **load_config_file type check**: The function accepts `str | Path | None`. Early versions only accepted `Path`, which caused `AttributeError: 'str' object has no attribute 'exists'` when called with a string path. Fixed in current version.
- **openpyxl dependency**: Excel-exporting scripts (`export_sprint.py`, `export_sprint_tasks_by_role.py`, `export_team_requirements.py`, `export_sprint_workload.py`) require `openpyxl`. The Hermes venv may not have it. Install with: `uv pip install openpyxl --python <hermes_venv_python>`. When running via `execute_code`, use `sys.executable` to find the venv path.
- **Row index alignment when inserting columns**: When a script inserts an extra column (e.g. 链接 before 标题) into row data before passing to `write_excel`, the row list becomes longer than the header list. The `write_excel` function must track a separate row index when reconstructing final rows, NOT use `enumerate(task_headers)` as the row index. See `export_sprint_tasks_by_role.py` `write_excel()` for the correct pattern.
- **Workitem web URL format**: The correct format is `https://devops.aliyun.com/projex/project/{project_id}/task/{workitem_id}` -- no organization segment, path segment is `task` (not `workitem`). The wrong format `https://devops.aliyun.com/organization/{org_id}/projex/project/{project_id}/workitem/{workitem_id}` redirects to login page. See `references/yunxiao-openapi-patterns.md` for details.
- **Excel file locked for writing**: When re-running an export script while the previous Excel file is still open in Excel, `workbook.save()` raises `PermissionError`. Use a different output filename or close the file first.
- **build_config must return file_config**: Scripts that need access to raw config (e.g. for `team.external_members`) must have `build_config()` return `file_config` alongside `config` and `team_members`. Forgetting this causes `NameError: name 'file_config' is not defined` at runtime.
- **Status filtering (已取消)**: All export scripts must call `filter_excluded_statuses()` on items immediately after `fetch_all_workitems()`. This filters out "已取消" status items so they don't pollute Excel exports or workload statistics. The function and `EXCLUDED_STATUSES` set are defined in `lib/yunxiao_client.py`. See `references/yunxiao-openapi-patterns.md` for details.
- **Role resolution from team.members + external_members**: When building name->role lookup for Excel sheets, merge `team.members` and `team.external_members` into a single dict. External members only provide role labels for the 任务明细 and 工时汇总 sheets; they are NOT included in the team creator filter (which determines which requirements are exported). If an assignee name is not found in either config section, their role column is blank.
- **Cross-sprint child tasks (CRITICAL)**: When fetching child tasks for requirements in a sprint, do NOT sprint-filter the task search. A task's `sprint` field may differ from its parent requirement's sprint (e.g. task assigned to Sprint260714 but parent requirement in Sprint260728). Sprint-filtering the task search silently misses these tasks, causing missing people and under-counted hours in workload statistics. Always fetch tasks with `sprint_id=""` and filter by `parentId` matching the requirement ID set. This affects `export_team_requirements.py` and `export_sprint_workload.py`. See `references/yunxiao-openapi-patterns.md` for details.
- **Exempt members (产品) still need yunxiao_id for requirement exports (CRITICAL)**: `team_creator_ids()` returns ALL members with a non-empty `yunxiao_id` — it does NOT filter by exempt status. Exempt 产品 members are often the PRIMARY creators of requirements. If an exempt member's `yunxiao_id` is empty, every requirement they created — plus all child dev tasks under those requirements — is silently excluded from `export_team_requirements.py` (and the team-scope 工时汇总/任务明细 sheets). SYMPTOM: developer task counts or hours look suspiciously low in the team-requirements export vs the sprint-workload export. FIX: run `--dump-creators` after adding ANY new member (including 产品) to auto-fill their `yunxiao_id`, even when they are `exempt: true`. The workload script (`export_sprint_workload.py`) is unaffected — it fetches all sprint requirements project-wide and does not filter by creator.

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
- Read `references/yunxiao-requirement-types.md` for requirement type IDs, required custom fields at creation time, and API response quirks.
