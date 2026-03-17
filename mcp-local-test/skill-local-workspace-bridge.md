# Skill: Local Workspace Bridge

## Overview
Use this skill to bind a local project directory to a Gitea-backed workspace repository and keep it synchronized.
Common values are persisted in `server_root/.mcp/common_config.json`.
If an argument is omitted, the tool will try to use the saved common value.

## Tools

### `bind_workspace`
Create or update local workspace metadata, let the remote server provision a unique Gitea repository and access token, write local git config, and push the initial snapshot.

Required args:
- None (but `root_path` and `remote_server` must be provided either by args or common config).

Optional args:
- `on_existing`: `ask` | `reuse` | `overwrite` | `fail` (default: `ask`).
- `auth_token`: Optional token for remote MCP authentication, stored in local metadata.

Example:
```json
{
  "root_path": "/home/usr/ICtools",
  "remote_server": "http://127.0.0.1:8999",
  "on_existing": "reuse"
}
```

### `sync_workspace`
Compare the local source revision with the remote workspace state and push when they differ.

Required args:
- None (but `root_path` must be provided either by args or common config).

Optional args:
- `auth_token`: Optional token for remote MCP authentication (overrides metadata/common config).

Example:
```json
{
  "root_path": "/home/usr/ICtools",
  "auth_token": "your-remote-token"
}
```

## Suggested Flow
1. Call `bind_workspace` once for a new local directory.
2. Before a remote simulation run, call `sync_workspace`, or use the local execution tools which perform the sync check automatically.
3. If the local source revision differs from the registered remote revision, the tool performs a git push automatically using the generated local git config.
