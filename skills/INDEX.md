# Skill Index: RTL Simulation MCP Server

## Overview
This MCP server provides tools for RTL simulation. Load only the skills you need.

## Available Skills

| Skill File      | Simulator        | License    | SSH Required |
|-----------------|-----------------|------------|--------------|
| `vcs.md`        | Synopsys VCS    | Commercial |      No     |


## Universal Tools (always available, no skill needed)

### `list_skills`
Returns this index. Call first to discover available skills.

### `load_skill`
Load a skill file by name to get detailed tool usage instructions.
```json
{ "skill": "vcs" }
```

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

### `list_simulators`
Returns all configured simulators and their available commands.

## Recommended Agent Workflow
1. Call `list_skills` to see what's available
2. Call `load_skill` with the simulator the user wants
3. Use tools described in that skill
4. Use `execute_command` for anything not covered by predefined commands
