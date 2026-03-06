"""
skills.py – Manage markdown skill files that agents load on demand.
"""
from __future__ import annotations

from pathlib import Path


class SkillsManager:
    def __init__(self, skills_dir: str | Path):
        self._dir = Path(skills_dir)

    def list_skills(self) -> str:
        """Return the INDEX.md content (master skill list)."""
        index = self._dir / "INDEX.md"
        if index.exists():
            return index.read_text()
        # Fallback: enumerate files
        files = [f.stem for f in self._dir.glob("*.md") if f.stem != "INDEX"]
        return "Available skills:\n" + "\n".join(f"- {s}" for s in files)

    def load_skill(self, name: str) -> str:
        """Load a specific skill by name (without .md extension)."""
        name = name.strip().lower().replace(".md", "")
        skill_file = self._dir / f"{name}.md"
        if not skill_file.exists():
            available = [f.stem for f in self._dir.glob("*.md") if f.stem != "INDEX"]
            return (
                f"Skill '{name}' not found.\n"
                f"Available skills: {', '.join(available)}\n"
                "Use list_skills to see the index."
            )
        return skill_file.read_text()
