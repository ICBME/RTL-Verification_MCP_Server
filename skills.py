"""
skills.py – Manage markdown skill files that agents load on demand.
"""
from __future__ import annotations

from pathlib import Path


class SkillsManager:
    def __init__(self, skills_dir: str | Path):
        self._dir = Path(skills_dir)

    def list_skill_names(self) -> list[str]:
        return sorted(f.stem for f in self._dir.glob("*.md") if f.stem != "INDEX")

    def skills_index(self) -> str:
        """Return the INDEX.md content with tool loading hints."""
        index = self._dir / "INDEX.md"
        extra = [
            "",
            "## Skill Tool",
            "",
            "- Call `get_skill()` first to discover available skills.",
        ]
        for name in self.list_skill_names():
            extra.append(f"- Call `get_skill(name=\"{name}\")` when that skill is relevant.")
        extra.append("- Call `get_skill(name=\"simulators\")` for simulator catalog and command templates.")

        if index.exists():
            return index.read_text() + "\n" + "\n".join(extra)

        return "Available skills:\n" + "\n".join(f"- {s}" for s in self.list_skill_names())

    def load_skill(self, name: str) -> str:
        """Load a specific skill by name (without .md extension)."""
        name = name.strip().lower().replace(".md", "")
        skill_file = self._dir / f"{name}.md"
        if not skill_file.exists():
            available = self.list_skill_names()
            return (
                f"Skill '{name}' not found.\n"
                f"Available skills: {', '.join(available)}\n"
                "Call get_skill() to see the index."
            )
        return skill_file.read_text()
