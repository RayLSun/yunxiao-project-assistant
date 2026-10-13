# Yunxiao Project Assistant

Codex / Hermes skill for handling Alibaba Cloud DevOps (Yunxiao) project collaboration. Two complementary modes: **Python scripts** for stable recurring workflows, and **Yunxiao MCP Server** for flexible exploratory queries.

## Layout

```text
.
├── README.md
├── LICENSE
├── .gitignore
├── examples/                        # MCP server config examples
│   ├── mcp.remote.json
│   └── mcp.stdio.json
├── lib/
│   └── yunxiao_client.py            # Shared API client (config, HTTP, sprint, workitem)
├── scripts/
│   ├── create_requirement.py       # Create a requirement (returns serialNumber + URL)
│   ├── create_tasks.py             # Auto-create tasks to fill workload gaps
│   ├── export_sprint.py            # Export sprint work items to Excel
│   ├── export_sprint_tasks_by_role.py  # Export sprint tasks filtered by role
│   ├── export_sprint_workload.py    # Export sprint workload statistics
│   ├── export_team_requirements.py  # Export team-created requirements to Excel
│   ├── fetch_for_prd.py             # Fetch requirement data for PRD generation
│   ├── get_requirement.py           # Fetch ONE workitem by serialNumber (cross-project)
│   └── list_requirements.py        # List requirements filtered by creator / sprint
├── config/
│   ├── yunxiao_config.example.json  # Config template (copy to yunxiao_config.json)
│   └── template.md                  # PRD template (6-section default)
├── exports/                         # Excel output directory (auto-created)
├── prds/                            # PRD output directory (auto-created)
└── skills/
    └── yunxiao-project-assistant/
        ├── SKILL.md
        ├── agents/openai.yaml
        └── references/
            ├── yunxiao-mcp.md       # MCP server setup reference
            ├── project-workflows.md # Playbooks for scripts + MCP
            ├── yunxiao-openapi-patterns.md   # API patterns: search, projects, serialNumber, tokens
            └── yunxiao-requirement-types.md  # Requirement type ids + required custom fields
```

## Quick Start

### 1. Configure

```bash
cp config/yunxiao_config.example.json config/yunxiao_config.json
```

Edit `config/yunxiao_config.json` and set at least:

- `yunxiao_token` - personal access token (云效 -> 个人设置 -> 个人访问令牌)
- `organization_id` - 组织管理后台 -> 基本信息 -> 组织 ID
- `project_id` - the segment after `/projex/project/` in the web URL

### 2. Install Python dependency (for Excel export only)

```bash
pip install openpyxl
```

`list_requirements.py` and `fetch_for_prd.py` use only the standard library - no install needed.

### 3. Run

```bash
# Export current sprint to Excel
python scripts/export_sprint.py --sprint-keyword "招聘" --kind all

# Fetch requirement data for PRD generation
python scripts/fetch_for_prd.py --sprint-keyword "招聘"

# List requirements by creator
python scripts/list_requirements.py --dump-creators
python scripts/list_requirements.py --creator-id abc123 --format table

# Fetch ONE workitem by serialNumber (project resolved from the prefix)
python scripts/get_requirement.py --serial LMAV-9 --format text
python scripts/get_requirement.py --list-projects --format text

# Find the config file
python scripts/export_sprint.py --show-config-path
```

## Scripts

### export_sprint.py

Exports work items (requirements / tasks) from a Yunxiao sprint to Excel. Supports auto-discovery of custom field headers, 工时汇总 summary sheet, and three sprint targeting modes (direct id, keyword, keyword+date).

### fetch_for_prd.py

Fetches requirement data (with descriptions) as JSON on stdout. The agent consumes this JSON, lists requirements for user confirmation, then generates PRD markdown files. Two modes: by sprint (keyword or id) or by requirement name keyword.

### get_requirement.py

Fetches a single workitem by its **serialNumber** (e.g. `LMAV-9`), with the project resolved automatically from the serial prefix — the prefix is the project's short code (`LMAV` for `LMAV-9`), so an item is findable without knowing or configuring which project it lives in. Searches requirements, tasks and bugs unless `--category` narrows it.

```bash
python scripts/get_requirement.py --serial LMAV-9                     # JSON (default)
python scripts/get_requirement.py --serial LMAV-9 --format text       # readable block
python scripts/get_requirement.py --serial LMAV-9 --save-text out.md  # dump description
python scripts/get_requirement.py --serial LMAV-9 --project LMAV      # explicit project
python scripts/get_requirement.py --list-projects --format text       # codes + ids
```

Output (JSON): `{project, category, url, workitem:{serialNumber,subject,status,assignedTo,creator,workitemType,sprintName,descriptionText,customFields}}`. Add `--raw` to include the raw detail payload.

Resolution order: explicit `--project` → the project matching the serial prefix → the configured default; the full organization scan is opt-in via `--all-projects`, so a mistyped serial fails in ~3s instead of scanning every project. Uses only the standard library.

### list_requirements.py

Lists requirements filtered by creator ID(s) and/or sprint. Includes `--dump-creators` discovery mode to find user IDs, and `--format table` for human-readable output.

## Yunxiao MCP Server (Flexible Queries)

For exploratory queries (analyze a requirement, check comments, update status), use the Yunxiao MCP Server. See `examples/mcp.remote.json` for configuration:

```json
{
  "mcpServers": {
    "yunxiao": {
      "url": "https://openapi-rdc.aliyuncs.com/ai/mcp?toolsets=project-management",
      "headers": {
        "Authorization": "Bearer <YOUR_TOKEN>"
      }
    }
  }
}
```

Use `project-management` for normal requirement and task work. Add `organization-management` for member or organization lookup, and add `code-management` only when repository or merge-request context is required.

Official Yunxiao MCP documentation: https://help.aliyun.com/zh/yunxiao/developer-reference/cloud-effect-mcp-tool-instructions

## Install As Agent Skill

Copy `skills/yunxiao-project-assistant` into your agent's skills directory:

**Hermes:**
```powershell
Copy-Item -Recurse -Force .\skills\yunxiao-project-assistant "$env:USERPROFILE\AppData\Local\hermes\skills\yunxiao-project-assistant"
```

**Claude Code:** recognizes `CLAUDE.md` at project root automatically.

**OpenAI Codex:** recognizes `AGENTS.md` at project root automatically.

Restart your agent after copying if the skill list does not refresh automatically.

## Notes

- Uses center-edition paths: `/oapi/v1/projex/organizations/{organizationId}/...`. Region edition is not supported by the scripts.
- The scripts never print `x-yunxiao-token`. Keep `config/yunxiao_config.json` out of version control - `.gitignore` already excludes it.
- Shared library `lib/yunxiao_client.py` depends only on the Python standard library. Only `export_sprint.py` requires `openpyxl`.

## License

MIT
