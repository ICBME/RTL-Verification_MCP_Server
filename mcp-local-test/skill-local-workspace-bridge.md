# Skill: Local Workspace Bridge

## Overview
Use this skill to bind a local project directory and sync it to a remote workspace.
Common values are persisted in `server_root/.mcp/common_config.json`.
If an argument is omitted, the tool will try to use the saved common value.

## Tools

### `bind_workspace`
Create or update local workspace metadata.

Required args:
- None (but `root_path`, `remote_server`, `remote_host`, `remote_base_dir` must be provided either by args or common config).

Optional args:
- `on_existing`: `ask` | `reuse` | `overwrite` | `fail` (default: `ask`).
- `auth_token`: Optional token for remote MCP authentication, stored in local metadata.

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
- None (but `root_path`, `ssh_user` must be provided either by args or common config).

Optional args:
- `ssh_port`: SSH port (default: `22`).
- `ssh_key_path`: Optional SSH private key file path.
- `ssh_key_passphrase`: Optional passphrase for SSH private key.
- `auth_token`: Optional token for remote MCP authentication (overrides metadata/common config).
- `transfer_method`: `rsync` | `scp` | `auto` (default `auto`; fallback to scp when rsync is unavailable).
- `delete`: Enable rsync `--delete` (default: `false`).
- `dry_run`: Preview only (default: `true`).
- `remote_base_dir_override`: Override remote base directory from metadata.

Example:
```json
{
  "root_path": "/home/usr/ICtools",
  "ssh_user": "usr",
  "ssh_port": 22,
  "ssh_key_path": "~/.ssh/id_rsa",
  "ssh_key_passphrase": "your-passphrase",
  "auth_token": "your-remote-token",
  "transfer_method": "auto",
  "delete": false,
  "dry_run": true
}
```

## Suggested Flow
1. Call `bind_workspace` once for a new local directory.
2. Call `sync_workspace` with `dry_run=true` to verify command behavior.
3. Call `sync_workspace` with `dry_run=false` to apply changes.
