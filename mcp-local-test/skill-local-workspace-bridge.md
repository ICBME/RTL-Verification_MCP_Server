# Skill: Local Workspace Bridge

## Overview
Use this skill to bind a local project directory and sync it to a remote workspace.

## Tools

### `bind_workspace`
Create or update local workspace metadata.

Required args:
- `root_path`: Absolute path of local project directory.
- `remote_server`: Remote MCP server URL.
- `remote_host`: SSH/rsync host.
- `remote_base_dir`: Remote workspace root directory.

Optional args:
- `on_existing`: `ask` | `reuse` | `overwrite` | `fail` (default: `ask`).

Example:
```json
{
  "root_path": "/home/usr/ICtools",
  "remote_server": "http://127.0.0.1:18080/mcp",
  "remote_host": "127.0.0.1",
  "remote_base_dir": "/tmp/remote-workspaces",
  "on_existing": "reuse"
}
```

### `sync_workspace`
Run remote workspace ensure + rsync + optional finalize.

Required args:
- `root_path`: Absolute path of bound local project directory.
- `ssh_user`: SSH user for rsync.

Optional args:
- `ssh_port`: SSH port (default: `22`).
- `delete`: Enable rsync `--delete` (default: `false`).
- `dry_run`: Preview only (default: `true`).
- `remote_base_dir_override`: Override remote base directory from metadata.

Example:
```json
{
  "root_path": "/home/usr/ICtools",
  "ssh_user": "usr",
  "ssh_port": 22,
  "delete": false,
  "dry_run": true
}
```

## Suggested Flow
1. Call `bind_workspace` once for a new local directory.
2. Call `sync_workspace` with `dry_run=true` to verify command behavior.
3. Call `sync_workspace` with `dry_run=false` to apply changes.
