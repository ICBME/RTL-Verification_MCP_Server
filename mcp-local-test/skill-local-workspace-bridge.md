# Skill: Local Workspace Bridge

## Overview
Use this skill to bind a local project directory to a Gitea-backed workspace repository and keep it synchronized.
Common values are persisted in `server_root/.mcp/common_config.json`.
If an argument is omitted, the tool will try to use the saved common value.

## Tools

### `bind_workspace`
Create or update local workspace metadata, ensure the remote workspace repo exists, register it with the remote server, and push the initial snapshot.

Required args:
- None (but `root_path`, `remote_server`, `gitea_base_url`, and `gitea_token` must be provided either by args or common config).

Optional args:
- `repo_owner`: Target Gitea user or organization.
- `repo_default_branch`: Default branch for the workspace repo. Default is `main`.
- `on_existing`: `ask` | `reuse` | `overwrite` | `fail` (default: `ask`).
- `auth_token`: Optional token for remote MCP authentication, stored in local metadata.
- `gitea_token`: Token used for Gitea API calls and git push.

Example:
```json
{
  "root_path": "/home/usr/ICtools",
  "remote_server": "http://127.0.0.1:8999/mcp",
  "gitea_base_url": "http://127.0.0.1:3000",
  "repo_owner": "workspace-bot",
  "repo_default_branch": "main",
  "gitea_token": "your-gitea-token",
  "on_existing": "reuse"
}
```

### `sync_workspace`
Compare the local source revision with the remote workspace state and push when they differ.

Required args:
- None (but `root_path` and `gitea_token` must be provided either by args or common config).

Optional args:
- `auth_token`: Optional token for remote MCP authentication (overrides metadata/common config).
- `gitea_token`: Token used for git push.

Example:
```json
{
  "root_path": "/home/usr/ICtools",
  "auth_token": "your-remote-token",
  "gitea_token": "your-gitea-token"
}
```

## Suggested Flow
1. Call `bind_workspace` once for a new local directory.
2. Before a remote simulation run, call `sync_workspace`.
3. If the local source revision differs from the registered remote revision, the tool performs a git push automatically.
