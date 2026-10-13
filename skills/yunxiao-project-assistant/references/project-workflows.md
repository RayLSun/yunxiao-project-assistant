# Project Workflows

Use these playbooks when a Yunxiao project-management request has more than one step.

## Script-Based Workflows (Stable, Repeatable)

### Export Sprint To Excel

Trigger: "导出当前迭代" / "export current sprint" / "导出需求和任务"

```bash
python scripts/export_sprint.py --kind all
```

If the sprint keyword is needed:
```bash
python scripts/export_sprint.py --sprint-keyword "招聘" --kind all
```

After running, summarize: file paths (absolute), row counts, and if a 工时汇总 table was printed, render it in chat.

### Fetch Requirements For PRD

Trigger: "为迭代 X 的需求生成 PRD" / "把 AIGC-xxxx 转成 PRD"

```bash
# By sprint
python scripts/fetch_for_prd.py --sprint-keyword "X"

# By requirement name
python scripts/fetch_for_prd.py --requirement-keyword "性别兜底"
```

The script emits JSON on stdout. List the requirements found, ask the user which to generate PRDs for, then author each PRD following `config/template.md`. Write files to `prds/{serialNumber}_{safe_subject}.md`.

### List Requirements By Creator / Role

Trigger: "看看张三创建的需求" / "list requirements by creator" / "后端研发的需求" / "团队所有人的需求"

#### Direct creator ID filter

```bash
# First discover creator IDs
python scripts/list_requirements.py --dump-creators

# Then filter
python scripts/list_requirements.py --creator-id abc123 --creator-id def456 --format table
```

#### Team role filter (uses config team.members)

```bash
# Filter by role
python scripts/list_requirements.py --role 后端研发 --format table

# Filter by all team members
python scripts/list_requirements.py --team --format table

# Show team roster to verify config
python scripts/list_requirements.py --show-team
```

#### Auto-fill yunxiao_id workflow

When team members have `name` filled but `yunxiao_id` empty:

1. Run `python scripts/list_requirements.py --dump-creators` to fetch creator IDs + display names.
2. Match display names against `team.members[].name` (case-insensitive, whitespace-trimmed).
3. Write matched IDs back to `config/yunxiao_config.json`.
4. If a name doesn't auto-match, show the user the dump list and ask them to identify the correct ID.

After auto-fill, `--role` and `--team` filters work immediately.

### Export Team Requirements To Excel

Trigger: "导出团队需求" / "导出迭代内团队创建的需求"

```bash
python scripts/export_team_requirements.py --sprint-name "Sprint260728"
```

Output: single Excel file at `doc/requirements/{sprint}/{sprint}_requirements.xlsx`. Uses all non-exempt team members' yunxiao_id as creator filter. Excel includes standard fields + auto-discovered custom fields, freeze panes, and auto-filter.

PREFERENCE: User prefers a single Excel file over multiple scattered markdown files. Default format is `xlsx`; `--format json` is available as fallback.

### Export Sprint Workload Statistics

Trigger: "统计工时" / "看看研发工时" / "workload statistics"

```bash
python scripts/export_sprint_workload.py --sprint-name "Sprint260728"
```

Output: `doc/workload/{sprint}/{sprint}_workload.xlsx` with two sheets:
- **工时汇总**: per-person summary (name, role, TL, task count, hours, required, diff, status). Red=under, green=met/over.
- **任务明细**: every task with serial, subject, assignee, role, hours, status, parent req serial+subject.

Only counts tasks whose `parentId` points to a requirement in this sprint. Excludes exempt members (产品). Members with 0 tasks appear with full gap shown.

Render the summary table in chat after running so the user sees the workload status immediately.

### Auto-Create Tasks To Fill Workload Gaps

Trigger: "补齐工时" / "创建任务" / "fill workload gaps"

```bash
# ALWAYS dry-run first
python scripts/create_tasks.py --sprint-name "Sprint260728" --dry-run

# After user confirms the plan
python scripts/create_tasks.py --sprint-name "Sprint260728"
```

**Safe execution workflow** (user preference):
1. ALWAYS run `--dry-run` first and show the plan to the user
2. Create ONE task as a test (via execute_code, not the script)
3. Ask the user to verify it in Yunxiao web UI (task name, assignee, hours, parent, inherited fields)
4. Only after user confirms, run the full batch without `--dry-run`

After batch creation, run `export_sprint_workload.py` again to verify updated hours.

## MCP-Based Workflows (Flexible, Exploratory)

### Triage Assigned Work

1. Resolve organization and project.
2. List work items assigned to the target user with active statuses.
3. Group by status, priority, due date, iteration, and blockers.
4. Highlight overdue or blocked items first.
5. Suggest next actions without changing live data unless requested.

Return format:

```text
Summary: <one sentence>
High attention: <IDs and reasons>
Next actions: <ordered list>
```

### Analyze A Requirement

Fetch the requirement/work item, description, fields, comments, children, linked tasks, and recent activity via MCP tools. Then report:

- Goal and user value
- Current state
- Ambiguities or missing decisions
- Acceptance criteria
- Implementation or coordination impact
- Recommended Yunxiao updates

If acceptance criteria are missing, draft them as a proposal rather than writing them directly.

### Create Child Tasks

Before creating tasks, confirm:

- Parent requirement ID
- Target project
- Task type
- Required fields
- Owner or assignee policy
- Iteration or milestone

Create small, outcome-oriented task titles. Keep descriptions practical: context, scope, done condition, dependencies.

### Update Status

Before changing status:

1. Read the work item.
2. Check valid statuses or transitions if the tools expose them.
3. Verify that required fields for the target status are satisfied.
4. Add a short comment when the status change benefits from context.

Do not infer completion only from a user's vague sentence like "looks fine"; ask whether to close or move the item.

### Add Comment

When the user supplies exact text, use it. When drafting, use this shape:

```text
Progress: ...
Risk/blocker: ...
Next: ...
```

Avoid speculative commitments. Mention dates only when they came from Yunxiao data or the user.

### Synchronize From Conversation

When the user asks to sync decisions from chat into Yunxiao:

1. Extract decisions, action items, owners, due dates, and affected requirements/tasks.
2. Ask for missing target IDs if they cannot be resolved.
3. Present a compact write plan.
4. Apply comments or field updates after the user confirms the plan.
