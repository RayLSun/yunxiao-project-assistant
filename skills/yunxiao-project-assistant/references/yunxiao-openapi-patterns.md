# Yunxiao OpenAPI Patterns

Tested against center-edition `openapi-rdc.aliyuncs.com` (2026-07-11).

## Authentication

All requests use `x-yunxiao-token` header (scripts) or `Authorization: Bearer <token>` (MCP). The token is a personal access token from 云效 -> 个人设置 -> 个人访问令牌.

```python
headers = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "x-yunxiao-token": token,
}
```

Never echo tokens in chat, logs, or files. Keep `config/yunxiao_config.json` in `.gitignore`.

## SearchWorkitems (Primary Query API)

`POST /oapi/v1/projex/organizations/{organizationId}/workitems:search`

This is the most versatile endpoint. Supports filtering by sprint, creator, status, date range, tags, and more via `conditions`.

### Request Body

```json
{
  "category": "Req",
  "conditions": "{\"conditionGroups\":[[...]]}",
  "orderBy": "gmtCreate",
  "page": 1,
  "perPage": 100,
  "sort": "desc",
  "spaceId": "<project_id>",
  "spaceType": "Project"
}
```

**Key gotcha**: `conditions` is a JSON **string** (double-serialized), not a nested object. You must `json.dumps()` the conditions dict before putting it in the body.

### Category Values

| Value | Meaning |
|---|---|
| `Req` | Requirements (需求) |
| `Task` | Tasks (任务) |
| `Bug` | Defects (缺陷) |
| `Risk` | Risks (风险) |

### Condition Structure

Each condition is a dict with these keys:

```json
{
  "fieldIdentifier": "creator",
  "operator": "CONTAINS",
  "value": ["user_id_1", "user_id_2"],
  "toValue": null,
  "className": "user",
  "format": "list"
}
```

Multiple conditions in one group are AND-ed. Multiple groups are OR-ed.

### Common fieldIdentifier Values

| fieldIdentifier | className | format | Notes |
|---|---|---|---|
| `subject` | `string` | `input` | Title contains match |
| `status` | `status` | `list` | Status ID list (e.g. `["28","30","32","100010"]`) |
| `assignedTo` | `user` | `list` | Assignee user IDs |
| `creator` | `user` | `list` | Creator user IDs |
| `sprint` | `sprint` | `list` | Sprint ID |
| `tag` | `tag` | `multiList` | Tag IDs |
| `gmtCreate` | `dateTime` | `input` | Date range, uses `BETWEEN` operator with `value` + `toValue` |

### Creator Filter Example

To filter by specific creators:

```python
extra_conditions = [{
    "fieldIdentifier": "creator",
    "operator": "CONTAINS",
    "value": ["5f2bfdacb69dd0f7311e2932", "another_user_id"],
    "toValue": None,
    "className": "user",
    "format": "list",
}]
items = client.fetch_all_workitems(
    project_id=project_id,
    sprint_id=sprint_id,  # can be "" for no sprint filter
    category="Req",
    extra_conditions=extra_conditions,
)
```

### Discovering User IDs

Yunxiao uses internal user identifiers (not usernames) in the creator/assignee filters. To discover them:

```bash
python scripts/list_requirements.py --dump-creators
```

This fetches ALL requirements (extracting `creator` field) and ALL tasks (extracting `assignedTo` field), then merges both into a single people list. Each person shows their ID, name, and source (creator / assignee / both).

PITFALL: Developers often never create requirements -- they only get assigned tasks. A creator-only scan will miss them. The dual-scan (creators + assignees) is essential.

When team config has members with empty `yunxiao_id`, `--dump-creators` auto-matches by name and writes IDs back to config automatically.

### Team-Based Creator Filtering

When the config file has a `team.members` section with `yunxiao_id` filled in, you can filter by role or entire team:

```bash
# By role
python scripts/list_requirements.py --role 后端研发

# By entire team
python scripts/list_requirements.py --team
```

The `--role` and `--team` flags collect yunxiao_ids from config and pass them as a `creator` condition (same API structure as `--creator-id`). Multiple IDs are OR-ed within one condition group.

## assignedTo Field Structure

The search API returns `assignedTo` as a **dict** (not a string), containing the assignee's name and internal user ID:

```json
{
  "assignedTo": {
    "name": "孙磊",
    "id": "6486837b68eb1baac20041e3"
  }
}
```

When filtering tasks by team member, extract the `id` from this dict and compare against `team.members[].yunxiao_id` in config:

```python
for t in task_items:
    assigned_to = t.get("assignedTo")
    if isinstance(assigned_to, dict):
        uid = assigned_to.get("id", "")
        if uid in target_ids:
            # matched
```

PITFALL: Treating `assignedTo` as a string (e.g. `if assigned_to in id_set`) raises `TypeError: unhashable type: 'dict'`. Always check `isinstance(assigned_to, dict)` first.

The `creator` field has the same dict structure: `{"name": "...", "id": "..."}`.

## Workitem Detail (Description Enrichment)

`GET /oapi/v1/projex/organizations/{organizationId}/workitems/{workitemId}`

The search endpoint returns `description: ""` for every item. To get the actual description body, call this detail endpoint per workitem. The description comes as a JSON envelope:

```json
{
  "htmlValue": "<article>...</article>",
  "jsonMLValue": [...]
}
```

Prefer `htmlValue` -- it can be passed through `richtext_to_text()` which strips HTML tags.

**Performance**: For N requirements, this means N extra requests (~0.5-1s per item). `enrich_with_descriptions()` does this in a loop and continues on failures.

**PITFALL**: When fetching tasks via `fetch_workitem_detail()` for a specific task ID, the response includes a `sprint` field showing which sprint the task belongs to. This may differ from the sprint of its parent requirement. Do not assume a task's sprint matches its parent's sprint.

## List Sprints

`GET /oapi/v1/projex/organizations/{organizationId}/projects/{projectId}/sprints?status=TODO,DOING,ARCHIVED&page=1&perPage=200`

Returns sprint list. Fields: `id`, `name`/`displayName`, `status`, `gmtModified`.

## Response Extraction

Yunxiao API responses are inconsistent in nesting. The `_extract_items()` helper checks these keys in order: `result`, `data`, `items`, `list`, `records`, `workitems`, `workItems`, `sprints`. Handles both list-at-root and nested-dict shapes.

## Pagination

Search API uses `page` (1-based) and `perPage` (max 100). When `len(items) < per_page`, pagination is complete. The `fetch_all_workitems()` helper loops automatically.

## Center vs Region Edition

Scripts use center-edition paths only: `/oapi/v1/projex/organizations/{organizationId}/...`. Region edition requires a custom `YUNXIAO_API_BASE_URL` env var and is not supported by the scripts.

## Parent-Child Relationship (Tasks Under Requirements)

The search API returns `parentId` (NOT `parentIdentifier`) on Task workitems, pointing to the parent Requirement's `id`. This is the key field for linking tasks to their parent requirement.

PITFALL: The `parentIdentifier` field may appear in some API docs but the search endpoint returns it as `None` or `EMPTY_VALUE`. Always use `parentId` for parent-child linking.

To find tasks belonging to requirements in a specific sprint:
1. Fetch all Req workitems in the sprint -> collect their `id` values into a set
2. Fetch all Task workitems in the project (pass `sprint_id=""` to `fetch_all_workitems`)
3. Filter tasks where `task.parentId` is in the requirement ID set

CRITICAL PITFALL: Do NOT sprint-filter tasks when looking for child tasks of sprint requirements. A task's `sprint` field may differ from its parent requirement's sprint (e.g. task in Sprint260714 but parent requirement in Sprint260728). Sprint-filtering the task search will silently miss these tasks, causing incorrect workload statistics (missing people, under-counted hours).

**Wrong** (misses cross-sprint tasks):
```python
tasks = client.fetch_all_workitems(project_id, sprint_id, "Task")  # WRONG
```

**Correct** (fetch all tasks, filter by parentId):
```python
tasks = client.fetch_all_workitems(project_id, "", "Task")  # no sprint filter
child_tasks = [t for t in tasks if str(t.get("parentId") or "") in req_ids]
```

This applies to `export_team_requirements.py` and `export_sprint_workload.py` -- both now use `sprint_id=""` when fetching tasks for child-task matching.

The `idPath` field on tasks contains a comma-separated path (e.g. `parent_id,child_id`), useful for debugging but `parentId` is the primary field.

## Workitem Creation

`POST /oapi/v1/projex/organizations/{organizationId}/workitems`

Creates a workitem (task, requirement, bug, etc.). Key body fields:

```json
{
  "subject": "任务标题",
  "assignedTo": "<user_id>",
  "parentId": "<parent_requirement_id>",
  "spaceId": "<project_id>",
  "workitemTypeId": "<task_type_id>",
  "sprint": "<sprint_id>",
  "formatType": "RICHTEXT",
  "description": "可选描述",
  "customFieldValues": {
    "101586": "8",
    "54f7fa7e4ba9e64a2e405a86ca": "AI-招聘助理",
    "priority": "76d9fbd7f74acc49b4b6940453"
  }
}
```

The `workitemTypeId` for tasks can be discovered from an existing task's `workitemType.id` field. In this project it's `ba102e46bc6a8483d9b7f25c` (may differ per org).

Response returns the created workitem with `id` (and sometimes `serialNumber`).

### CRITICAL: Required custom fields must be set at creation time

Yunxiao enforces required custom fields at creation time. The API validates them one-by-one and returns HTTP 400 with the FIRST missing field. Example sequence for requirements:
1. Missing `assignedTo` → "指派人不能为空"
2. Missing 里程碑 (`6084d274899a5942faac8ee8dc`) → "字段【里程碑】不能为空"
3. Missing 业务系统 (`54f7fa7e4ba9e64a2e405a86ca`) → "字段【业务系统】不能为空"
4. Missing 成本分摊部门 (`574fb04effab3ce1f171524ab3`) → "字段【成本分摊部门】不能为空"
5. (业务类需求 only) Missing 2026预算编号 (`38f661ed375902ce6dc0312124`) → "字段【2026预算编号】不能为空"

You must fix each error in order — the API stops at the first missing field.

**FIX**: Pass `customFieldValues` in the creation body. The value is a flat dict mapping `fieldId` to string value.

### CRITICAL: Required fields differ by workitem type

Different requirement types have different required custom fields. 技术类需求 requires 3 fields (里程碑, 业务系统, 成本分摊部门), while 业务类需求 requires a 4th (2026预算编号). Task types have their own set (预计工时, etc.). Always probe with a test creation to discover the full set of required fields for each type.

**Probe technique**: Call the creation API with progressively more fields until it succeeds, noting each error message. Or fetch a sample workitem of the same type and inspect its `customFieldValues[].fieldId` set.

### CRITICAL: Creation API response is minimal

The CreateWorkitem API response for requirements only returns `{"id": "..."}` — no `serialNumber`, `subject`, or other fields. To get the serialNumber, call `fetch_workitem_detail(workitem_id)` as a second request. The web URL can be constructed directly from `id` without needing the detail call.

### CRITICAL: Only inherit fields enabled on the target type

Not all requirement custom fields exist on the task type. If you pass a field not enabled on the task type, the API returns: `"当前工作项类型下，未启用此字段【成本分摊部门】，不支持修改"`.

**FIX**: Before creating tasks, fetch an existing task of the same type and extract its `customFieldValues[].fieldId` set. Only pass fields from the parent requirement whose `fieldId` is in that set. Exclude `sumPlanedLaborHour` (computed read-only field).

```python
# Get valid task field IDs from existing task
task_field_ids = set()
for cf in existing_task.get("customFieldValues", []):
    fid = cf.get("fieldId", "")
    if fid and fid != "sumPlanedLaborHour":
        task_field_ids.add(fid)

# Inherit from parent requirement
custom_values = {}
for cf in parent_req.get("customFieldValues", []):
    fid = cf.get("fieldId", "")
    if fid in task_field_ids:
        vals = cf.get("values", [])
        if vals:
            v = vals[0]
            ident = str(v.get("identifier") or v.get("displayValue") or "") if isinstance(v, dict) else str(v)
            if ident:
                custom_values[fid] = ident
# Add hours
custom_values[hours_field_id] = str(hours)
```

## Update Custom Field (预计工时 etc.)

`POST /organization/{organizationId}/workitems/updateWorkitemField`

This uses the legacy API path (no `/projex/` segment) but works with `x-yunxiao-token` on center edition. Body:

```json
{
  "identifier": "<workitem_id>",
  "fieldType": "customField",
  "propertyKey": "<field_identifier>",
  "propertyValue": "<value_as_string>"
}
```

The `propertyKey` is the custom field's `fieldId` (e.g. `101586` for 预计工时). Discover field IDs from an existing workitem's `customFieldValues[].fieldId`.

PITFALL: The field value must be a string, even for numeric fields like 预计工时. Pass `"4"` not `4`.

## Custom Field Discovery

To find the `fieldId` of a custom field (e.g. 预计工时, 优先级), fetch a sample workitem and inspect `customFieldValues`:

```python
items = client.search_workitems(project_id=pid, sprint_id=sid, category="Task", page=1, per_page=1)
for cf in items[0].get("customFieldValues", []):
    print(f"{cf['fieldName']} -> fieldId={cf['fieldId']}")
```

Common field IDs in this project (may differ per org):
- 预计工时: `101586`
- 优先级: `priority`
- 预计工时汇总: `sumPlanedLaborHour`
- 里程碑: `6084d274899a5942faac8ee8dc`
- 业务系统: `54f7fa7e4ba9e64a2e405a86ca`
- 成本分摊部门: `574fb04effab3ce1f171524ab3`
- 2025预算编号: `72cb814522a4698967f656ab2e`
- 2026预算编号: `38f661ed375902ce6dc0312124`

Requirement workitemType IDs in this project:
- 技术类需求: `bca48ee2a0976d38f4802fae`
- 业务类需求: `b759681e33af77f5c8620cbc`
- 任务: `ba102e46bc6a8483d9b7f25c`

## Workitem Web URL

The Yunxiao web UI URL for a workitem (task, requirement, bug, etc.) follows this format:

```
https://devops.aliyun.com/projex/project/{project_id}/task/{workitem_id}
```

- **No `organization` segment** in the path (unlike the API endpoints which all start with `/oapi/v1/projex/organizations/{organizationId}/...`).
- Path segment is `task` (not `workitem`), regardless of workitem category.
- `{workitem_id}` is the `id` field from the search API response (e.g. `2f652483dc9e05dc3a2da8607c`), NOT the `serialNumber` (e.g. `HWTQ-1509`).

PITFALL: A common wrong guess is `https://devops.aliyun.com/organization/{org_id}/projex/project/{project_id}/workitem/{workitem_id}` — this redirects to the login page. The correct format omits the organization segment and uses `task` as the path segment.

Helper function in `export_sprint_tasks_by_role.py`:

```python
def build_workitem_url(project_id: str, workitem_id: str) -> str:
    return f"https://devops.aliyun.com/projex/project/{project_id}/task/{workitem_id}"
```

## Status Filtering (Excluded Statuses)

All export scripts filter out items with certain statuses before writing to Excel. The shared library defines:

```python
EXCLUDED_STATUSES = {"已取消"}

def filter_excluded_statuses(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter out items whose status is in EXCLUDED_STATUSES (e.g. 已取消)."""
    return [
        item for item in items
        if object_name(item.get("status")) not in EXCLUDED_STATUSES
    ]
```

**Usage pattern** - call immediately after `fetch_all_workitems()`:

```python
items = client.fetch_all_workitems(project_id, sprint_id, "Task")
items = filter_excluded_statuses(items)
```

This applies to ALL four export scripts: `export_sprint.py`, `export_sprint_tasks_by_role.py`, `export_sprint_workload.py`, `export_team_requirements.py` (both requirements and child tasks).

Known sprint statuses (center edition):
- Requirements: 待处理, 已梳理, 已评审, 待测试, 待发布
- Tasks: 待处理, 处理中, 已完成, 已取消 (only 已取消 is excluded)

To add more excluded statuses, edit `EXCLUDED_STATUSES` in `lib/yunxiao_client.py`.

## Project List (cross-project queries)

`POST /oapi/v1/projex/organizations/{organizationId}/projects:search`

Body: `{"page": 1, "perPage": 100}`

PITFALL: the REST-looking `GET /oapi/v1/projex/organizations/{organizationId}/projects` returns **HTTP 404 NotFound**. Only the `:search` verb variant works. Do not "fix" a 404 by adding a trailing slash or a query string — switch the method to POST and the path to `projects:search`.

Each project record carries the short code that prefixes every serialNumber in it:

| Field | Example | Notes |
|---|---|---|
| `id` | `dff633319b24b7787f3fada38f` | Use with `spaceId` in search bodies |
| `customCode` | `LMAV` | **This is the serialNumber prefix** |
| `name` | `SME` | Display name |

Real codes in this organization (2026-10): `HWTQ` 数字创新部, `AIGC` DEC-智能招聘, `SFFC` Staffing Cloud, `LMAV` SME, `OMJS` DEC-统一工作台, `MBXM` 深圳外包系统, `FPHX` 产品营销, `RZDY` 风险金项目, `ULWZ` 销售部, `BMQJ` 采购部, `XOGV` 需求管理-DEMO, `DEVEX` DevOps 研发示例项目.

### Why this matters: serialNumber → project

`HWTQ-1509` lives in HWTQ, `LMAV-9` lives in LMAV. The **serial prefix is the project `customCode`**, so a serialNumber alone is enough to locate its project — no need to ask the user which project an item is in, and no need for the configured default `project_id` to match.

Resolution order used by `YunxiaoClient.find_workitem_by_serial()`:

1. explicit `project_id` (id / code / name) — when the caller passes one
2. the project whose `customCode` equals the serial prefix (case-insensitive)
3. the configured default `project_id`
4. every project in the organization, only when `all_projects=True`

Steps 1–3 cost 1–3 requests; step 4 costs one request per project per category, so it stays opt-in. When the prefix already resolves, a typo fails in ~3s with the searched project ids in the message.

## serialNumber Search Condition

Serial numbers are searchable directly — no need to page through a sprint:

```python
extra_conditions = [{
    "fieldIdentifier": "serialNumber",
    "operator": "CONTAINS",
    "value": ["LMAV-9"],
    "toValue": None,
    "className": "string",
    "format": "input",
}]
items = client.fetch_all_workitems(project_id=pid, sprint_id="", category="Req",
                                   extra_conditions=extra_conditions)
```

PITFALL: `CONTAINS` also matches longer serials, so `LMAV-9` returns `LMAV-90`, `LMAV-91`… always re-filter with an exact compare:

```python
want = "LMAV-9"
exact = [i for i in items if str(i.get("serialNumber") or "").upper() == want]
```

`category` still has to be passed (`Req` / `Task` / `Bug` are separate search spaces), which is why `find_workitem_by_serial()` tries all three by default — a serial gives no hint about whether it is a requirement or a task (`HWTQ-1509` is a Task, `LMAV-9` is a requirement).

## Token Expiry (`ExpiredToken`)

A stale token returns HTTP 401 with `{"errorCode":"ExpiredToken","errorMessage":"Expired token."}`. Personal access tokens from 云效 → 个人设置 → 个人访问令牌 expire, and a config file can sit unused for months while still *looking* populated — `build_config_from_sources()` renames the field, so `config["yunxiao_token"]` is empty even when the file has one:

| In the JSON file | In the dict returned by `build_config_from_sources()` |
|---|---|
| `yunxiao_token` | `token` ← **always read this key** |

When the skill's own config 401s, check other Yunxiao configs on the machine before asking the user for a new token — a sibling skill's config often holds a longer-lived generic token (e.g. `~/.claude/skills/yunxiao-sprint-export/config/yunxiao_config.json`). Verify it with a cheap search before trusting it, and never print the token.

## Config Priority

All scripts follow: CLI flag > environment variable > config file.

```
YUNXIAO_DOMAIN, YUNXIAO_TOKEN, YUNXIAO_ORGANIZATION_ID,
YUNXIAO_PROJECT_ID, YUNXIAO_SPRINT_ID, YUNXIAO_SPRINT_KEYWORD,
YUNXIAO_SPRINT_STATUSES, YUNXIAO_CORP_ID
```

Placeholder values (`pt-your-token`, `your-organization-id`, etc.) are detected and rejected by `is_real_value()`.
