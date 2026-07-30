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
            └── project-workflows.md # Playbooks for scripts + MCP
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

# Find the config file
python scripts/export_sprint.py --show-config-path
```

## Scripts

### export_sprint.py

Exports work items (requirements / tasks) from a Yunxiao sprint to Excel. Supports auto-discovery of custom field headers, 工时汇总 summary sheet, and three sprint targeting modes (direct id, keyword, keyword+date).

### fetch_for_prd.py

Fetches requirement data (with descriptions) as JSON on stdout. The agent consumes this JSON, lists requirements for user confirmation, then generates PRD markdown files. Two modes: by sprint (keyword or id) or by requirement name keyword.

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
