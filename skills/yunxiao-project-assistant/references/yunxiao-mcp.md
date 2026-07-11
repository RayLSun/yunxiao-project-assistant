# Yunxiao MCP Reference

Source: https://help.aliyun.com/zh/yunxiao/developer-reference/cloud-effect-mcp-tool-instructions
Checked: 2026-07-10

## Server Purpose

Yunxiao MCP Server, `alibabacloud-devops-mcp-server`, lets an AI assistant interact with Alibaba Cloud DevOps/Yunxiao. The official documentation describes coverage for organization management, Codeup code management, Projex project collaboration, Flow pipelines, packages, application delivery, and Testhub.

For this skill, prefer Projex project collaboration through the `project-management` toolset.

## Remote Hosted Setup

Use Streamable HTTP when the client can configure a remote MCP server:

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

If the user needs organization/member lookup too, use:

```text
toolsets=organization-management,project-management
```

If code context or merge requests are part of the task, use:

```text
toolsets=code-management,project-management
```

## Local stdio Setup

Local stdio requires Node.js 20 or newer:

```json
{
  "mcpServers": {
    "yunxiao": {
      "command": "npx",
      "args": ["-y", "alibabacloud-devops-mcp-server", "--toolsets=project-management"],
      "env": {
        "YUNXIAO_ACCESS_TOKEN": "<YOUR_TOKEN>"
      }
    }
  }
}
```

## Authentication

Preferred token delivery order:

1. `Authorization: Bearer <TOKEN>`
2. `X-Yunxiao-Token: <TOKEN>`
3. `?yunxiao_access_token=<TOKEN>` only when headers are impossible, because query strings can appear in logs

Never echo real tokens in chat, logs, files, or screenshots.

## Private Region

The hosted endpoint defaults to the Yunxiao central site at `openapi-rdc.aliyuncs.com`. For a private or dedicated region, local stdio can set:

```json
{
  "YUNXIAO_API_BASE_URL": "https://your-org.devops.aliyuncs.com"
}
```

## Troubleshooting

- If no Yunxiao tools are visible, ask the user to confirm that the MCP server is configured and authenticated.
- If many unrelated tools appear, suggest limiting the server to `project-management`, plus `organization-management` or `code-management` only when needed.
- If a read works but writes fail, check token permissions for project collaboration APIs and whether the work item status transition is valid.
