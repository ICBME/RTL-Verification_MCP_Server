"""
config.py – Parse tools.toml and produce structured simulator definitions.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class CommandDef:
    name: str
    description: str
    template: str          # Shell template with {param} placeholders


@dataclass
class SimulatorDef:
    name: str
    description: str
    use_ssh: bool
    work_dir: str
    commands: list[CommandDef] = field(default_factory=list)

    def get_command(self, name: str) -> Optional[CommandDef]:
        return next((c for c in self.commands if c.name == name), None)


@dataclass
class SSHConfig:
    enabled: bool = False
    host: str = "localhost"
    port: int = 22
    user: str = ""
    key_file: str = "~/.ssh/id_rsa"
    password: Optional[str] = None


@dataclass
class ServerConfig:
    ssh: SSHConfig
    simulators: dict[str, SimulatorDef]   # keyed by simulator name


def load_config(path: str | Path) -> ServerConfig:
    path = Path(path)
    with path.open("rb") as f:
        raw = tomllib.load(f)

    # SSH block
    ssh_raw = raw.get("ssh", {})
    ssh = SSHConfig(
        enabled=ssh_raw.get("enabled", False),
        host=ssh_raw.get("host", "localhost"),
        port=int(ssh_raw.get("port", 22)),
        user=ssh_raw.get("user", ""),
        key_file=ssh_raw.get("key_file", "~/.ssh/id_rsa"),
        password=ssh_raw.get("password"),
    )

    # Simulators
    simulators: dict[str, SimulatorDef] = {}
    for sim_raw in raw.get("simulators", []):
        commands = [
            CommandDef(
                name=cmd["name"],
                description=cmd["description"],
                template=cmd["template"],
            )
            for cmd in sim_raw.get("commands", [])
        ]
        sim = SimulatorDef(
            name=sim_raw["name"],
            description=sim_raw.get("description", ""),
            use_ssh=sim_raw.get("use_ssh", False),
            work_dir=sim_raw.get("work_dir", "."),
            commands=commands,
        )
        simulators[sim.name] = sim

    return ServerConfig(ssh=ssh, simulators=simulators)
