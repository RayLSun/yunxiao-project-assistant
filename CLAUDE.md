# Yunxiao Project Assistant

This project provides Python scripts for Alibaba Cloud DevOps (Yunxiao) project collaboration. When the user asks about Yunxiao requirements, tasks, sprints, or workload, use these scripts.

## Prerequisites

- Python 3.8+ (standard library only, except `openpyxl` for Excel exports)
- Config file at `config/yunxiao_config.json` with `yunxiao_token`, `organization_id`, `project_id`

If the config file doesn't exist, copy the template:
```bash
cp config/yunxiao_config.example.json config/yunxiao_config.json
```

## Available Scripts

All scripts live in `scripts/` and share `config/yunxiao_config.json`.

### Create a Requirement

When the user says "create requirement" / "创建需求", parse the title and description from their message, then run:

```bash
python scripts/create_requirement.py --title "需求标题" --description "需求描述内容"
```

Options:
- `--type 业务|技术` — requirement type (default: 业务)
- `--assignee "姓名"` — assignee from team config (default: first non-exempt member)
- `--dry-run` — preview without creating

Output is JSON with `serialNumber` and `url`. Return the URL to the user.

### Create Tasks (Fill Workload Gaps)

```bash
python scripts/create_tasks.py --sprint-name "Sprint260730" --dry-run   # preview
python scripts/create_tasks.py --sprint-name "Sprint260730"            # execute
```

**Always run `--dry-run` first**, show the plan, and ask for confirmation before running without `--dry-run`.

### Export Sprint to Excel

```bash
python scripts/export_sprint.py --sprint-keyword "招聘" --kind all
python scripts/export_sprint.py --kind req          # requirements only
python scripts/export_sprint.py --kind task         # tasks only
```

### Export Team Requirements

```bash
python scripts/export_team_requirements.py --sprint-name "Sprint260730"
```

### Export Sprint Workload

```bash
python scripts/export_sprint_workload.py --sprint-name "Sprint260730"
```

### Export Tasks by Role

```bash
python scripts/export_sprint_tasks_by_role.py --sprint-name "Sprint260730" --role 后端研发
python scripts/export_sprint_tasks_by_role.py --sprint-name "Sprint260730" --all
```

### Fetch Requirement Data (for PRD)

```bash
python scripts/fetch_for_prd.py --sprint-keyword "招聘"
python scripts/fetch_for_prd.py --requirement-keyword "性别兜底"
```

### List Requirements

```bash
python scripts/list_requirements.py --dump-creators    # discover user IDs
python scripts/list_requirements.py --format table      # table output
python scripts/list_requirements.py --role 后端研发     # by role
```

## MCP Server (Flexible Queries)

For ad-hoc exploration (analyze requirement, check comments, update status), use the Yunxiao MCP Server. See `examples/mcp.remote.json` for configuration.

## Safety Rules

- Never print or expose `yunxiao_token` in output or commits.
- Keep `config/yunxiao_config.json` out of version control.
- Treat Yunxiao as a live production system. Do not bulk-edit or delete items unless the user explicitly asks.
- Always run `--dry-run` before write operations.
