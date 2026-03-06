# Skill: VCS RTL Simulation

## Overview
Use this skill when the user wants to compile or simulate RTL designs using **Synopsys VCS**.
VCS is a commercial simulator that runs on the host machine via SSH.

## When to Use
- Compiling SystemVerilog / Verilog / VHDL with VCS
- Running UVM testbenches
- Viewing waveforms with DVE
- Checking simulation logs for errors

## Available Tools (call via MCP)

### `vcs_compile`
Compile RTL sources.

**Required args:** `files` (space-separated RTL file paths), `top` (top module name)
**Optional args:** `opts` (extra VCS flags, e.g. `+define+DEBUG`)

**Example call:**
```json
{ "tool": "vcs_compile", "files": "rtl/top.sv rtl/sub.sv tb/tb_top.sv", "top": "tb_top" }
```

### `vcs_simulate`
Run the compiled `simv` binary.

**Required args:** none (simv must already be compiled)
**Optional args:** `plusargs` (e.g. `+UVM_TESTNAME=my_test +UVM_VERBOSITY=UVM_HIGH`)

### `vcs_compile_and_run`
Compile and simulate in a single step.

**Required args:** `files`, `top`, `testname`
**Optional args:** `plusargs`

### `vcs_check_log`
Grep for errors/warnings in a log file.

**Optional args:** `log_file` (default: `sim.log`)

### `vcs_view_waves`
Open DVE waveform viewer.

**Required args:** `wave_file` (path to `.vpd` or `.vcd`)

## Workflow Example
```
1. vcs_compile   → compile RTL
2. vcs_simulate  → run test with plusargs
3. vcs_check_log → inspect results
4. vcs_view_waves (optional) → debug waves
```

## Notes
- VCS commands are executed **via SSH on the host machine** (configured in `tools.toml`)
- Working directory on host: see `tools.toml` → `vcs.work_dir`
- For custom commands not listed above, use the `execute_command` tool with `use_ssh=true`
