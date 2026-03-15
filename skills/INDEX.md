# Skill Index: RTL Simulation MCP Server

## Overview
This MCP server provides RTL simulation tools and exposes skills through one tool.
Load only the skill content you need.

## Available Skills

| Skill File      | Simulator        | License   | SSH Required |
|-----------------|-----------------|------------|--------------|
| `vcs.md`        | Synopsys VCS    | Commercial |      No      |
| `test.md`       | Test Tool       | For Test   |      No      |

## Skill Tool

### `get_skill()`
Call with no arguments first to get this index and the loading pattern.

### `get_skill(name="<skill>")`
Load one skill by name, for example `get_skill(name="vcs")`.

### `get_skill(name="simulators")`
Return the configured simulator catalog and command templates.

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
1. Call `get_skill()`
2. Load `get_skill(name="<skill>")` for the relevant simulator or workflow
3. Call `get_skill(name="simulators")` only when simulator command details are needed
4. Use `execute_command` for anything not covered by predefined commands
