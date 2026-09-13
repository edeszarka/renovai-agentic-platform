"""
Skill Registry — Type-safe, Progressive Disclosure for Agent Skills.

Agents can introspect available skills, load their metadata without loading
the full instructions, and defer script loading until activation time.

Progressive disclosure layers:
  Layer 0: Metadata (YAML frontmatter) — always loaded, used for routing
  Layer 1: Instructions (body after frontmatter) — loaded after routing
  Layer 2: Scripts (scripts/ directory) — loaded on demand when skill runs
  Layer 3: References (references/ directory) — loaded when estimation needed
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

SKILLS_ROOT = Path(__file__).resolve().parent.parent / ".agent" / "skills"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SkillInfo:
    """Immutable descriptor for a single agent skill."""

    name: str
    description: str
    trigger_conditions: list[str]
    not_trigger_when: list[str]
    depends_on: list[str]
    calls: dict[str, str]
    skill_dir: Path

    # Progressive disclosure paths
    instructions_path: Path | None = None
    scripts_dir: Path | None = None
    references_dir: Path | None = None

    @property
    def has_instructions(self) -> bool:
        return self.instructions_path is not None and self.instructions_path.exists()

    @property
    def has_scripts(self) -> bool:
        return self.scripts_dir is not None and self.scripts_dir.is_dir()

    @property
    def has_references(self) -> bool:
        return self.references_dir is not None and self.references_dir.is_dir()


@dataclass
class SkillRegistry:
    """
    Central registry for all agent skills.

    Usage:
        registry = SkillRegistry()
        registry.load_all()

        # Layer 0 — metadata only
        skills = registry.find_by_intent("wall_construction")

        # Layer 1 — load instructions for a matched skill
        instructions = registry.load_instructions(skills[0].name)

        # Layer 2 — get script path on activation
        script = registry.get_script_path(skills[0].name, "estimate.py")
    """

    _skills: dict[str, SkillInfo] = field(default_factory=dict)

    # -----------------------------------------------------------------------
    # Loading
    # -----------------------------------------------------------------------

    def load_all(self, root: str | Path | None = None) -> None:
        """Scan SKILLS_ROOT for all skill directories and load metadata."""
        root_path = Path(root) if root else SKILLS_ROOT
        if not root_path.is_dir():
            logger.warning("Skills root not found: %s", root_path)
            return

        for entry in sorted(root_path.iterdir()):
            if not entry.is_dir():
                continue
            skill_file = entry / "SKILL.md"
            if not skill_file.exists():
                continue
            try:
                info = self._load_skill(skill_file)
                self._skills[info.name] = info
                logger.debug("Loaded skill: %s", info.name)
            except Exception as exc:
                logger.warning("Failed to load skill from %s: %s", skill_file, exc)

        logger.info("Loaded %d skills from %s", len(self._skills), root_path)

    def _load_skill(self, skill_file: Path) -> SkillInfo:
        """Parse a single SKILL.md file and return its metadata."""
        raw_text = skill_file.read_text(encoding="utf-8")

        # Extract YAML frontmatter between --- markers
        fm_match = re.match(r"^---\s*\n(.*?)\n---", raw_text, re.DOTALL)
        if not fm_match:
            raise ValueError(f"No YAML frontmatter found in {skill_file}")

        metadata = yaml.safe_load(fm_match.group(1))
        skill_dir = skill_file.parent

        return SkillInfo(
            name=metadata.get("name", skill_dir.name),
            description=metadata.get("description", "").strip(),
            trigger_conditions=metadata.get("trigger_conditions", []),
            not_trigger_when=metadata.get("not_trigger_when", []),
            depends_on=metadata.get("depends_on", []),
            calls=metadata.get("calls", {}),
            skill_dir=skill_dir,
            instructions_path=skill_file,
            scripts_dir=skill_dir / "scripts"
            if (skill_dir / "scripts").is_dir()
            else None,
            references_dir=skill_dir / "references"
            if (skill_dir / "references").is_dir()
            else None,
        )

    # -----------------------------------------------------------------------
    # Querying
    # -----------------------------------------------------------------------

    def get(self, name: str) -> SkillInfo | None:
        """Get a skill by name (Layer 0 only — no file I/O)."""
        return self._skills.get(name)

    def all(self) -> list[SkillInfo]:
        """Return all loaded skills."""
        return list(self._skills.values())

    def find_by_intent(self, intent: str) -> list[SkillInfo]:
        """
        Find skills whose description or trigger conditions match an intent.

        This is a simple keyword match useful for routing. Returns skills
        sorted by relevance (more trigger matches first).
        """
        intent_lower = intent.lower()
        scored: list[tuple[int, SkillInfo]] = []

        for skill in self._skills.values():
            score = 0
            all_triggers = [skill.description.lower()] + [
                t.lower() for t in skill.trigger_conditions
            ]
            for t in all_triggers:
                if intent_lower in t:
                    score += 1
            if score > 0:
                scored.append((score, skill))

        scored.sort(key=lambda x: -x[0])
        return [s for _, s in scored]

    # -----------------------------------------------------------------------
    # Progressive Disclosure
    # -----------------------------------------------------------------------

    def load_instructions(self, name: str) -> str:
        """
        Load full instructions body for a skill (Layer 1).

        Loads the entire SKILL.md file and extracts the body after the
        YAML frontmatter. This is called AFTER the gateway has confirmed
        routing, never before.
        """
        skill = self._skills.get(name)
        if not skill or not skill.instructions_path:
            return ""

        raw_text = skill.instructions_path.read_text(encoding="utf-8")
        # Strip YAML frontmatter
        body = re.sub(r"^---\s*\n.*?\n---\s*\n", "", raw_text, count=1, flags=re.DOTALL)
        return body.strip()

    def get_script_path(self, name: str, script_name: str) -> Path | None:
        """
        Get the path to a specific script file (Layer 2).

        Returns None if the script does not exist. The caller is responsible
        for importing or executing the script.
        """
        skill = self._skills.get(name)
        if not skill or not skill.scripts_dir:
            return None
        script_path = skill.scripts_dir / script_name
        return script_path if script_path.exists() else None

    def get_reference_path(self, name: str, ref_name: str) -> Path | None:
        """
        Get the path to a reference file (Layer 3).

        Returns None if the reference does not exist.
        """
        skill = self._skills.get(name)
        if not skill or not skill.references_dir:
            return None
        ref_path = skill.references_dir / ref_name
        return ref_path if ref_path.exists() else None
