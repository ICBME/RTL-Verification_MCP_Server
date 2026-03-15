# Skill Index: RTL Simulation MCP Server

## Overview
This MCP server provides RTL simulation tools and exposes skills through resources.
Read only the skill resources you need.

## Available Skills

| Skill File      | Simulator        | License    | SSH Required |
|-----------------|-----------------|------------|--------------|
| `vcs.md`        | Synopsys VCS    | Commercial |      No     |


## Resource Discovery Flow

### `skills://index`
Read this resource first to discover available skills and the loading pattern.

### `skills://<name>`
Load one skill resource by name, for example `skills://vcs`.

### `skills://simulators`
Read this resource when you need the configured simulator catalog and command templates.

## Universal Tools (always available, no skill needed)

### `execute_command`
Execute an arbitrary shell command.
```json
{
  "command": "ls -la /path/to/design",
  "work_dir": "/home/rtluser/proj",
  "use_ssh": true,
  "timeout": 60
}
```

## Recommended Agent Workflow
1. Read `skills://index`
2. Load `skills://<name>` for the relevant simulator or workflow
3. Read `skills://simulators` only when simulator command details are needed
4. Use `execute_command` for anything not covered by predefined commands
