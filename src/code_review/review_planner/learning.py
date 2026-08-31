from __future__ import annotations

import json
from pathlib import Path

LEARNED_PRACTICES_FILE = "learned-practices.json"


def default_learned_practices_path(*, target: Path) -> Path:
    return target / ".code-review" / LEARNED_PRACTICES_FILE


def load_learned_practices(*, target: Path) -> dict:
    learned_path = default_learned_practices_path(target=target)
    if not learned_path.exists():
        return {}
    payload = json.loads(learned_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("Learned practices file must contain a JSON object.")
    return payload


def save_learned_practices(*, target: Path, payload: dict) -> None:
    learned_path = default_learned_practices_path(target=target)
    learned_path.parent.mkdir(parents=True, exist_ok=True)
    learned_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def record_learned_practices(
    *,
    target: Path,
    practices: list[str],
    specialty_id: str = "repo-learnings",
    specialty_title: str = "Repository Learnings Pack",
) -> dict:
    learned = load_learned_practices(target=target)
    extensions = learned.setdefault("extensions", {})
    if not isinstance(extensions, dict):
        raise TypeError("'extensions' in learned practices must be an object.")
    specialties = extensions.setdefault("specialties", {})
    if not isinstance(specialties, dict):
        raise TypeError("'extensions.specialties' in learned practices must be an object.")
    pack = specialties.setdefault(
        specialty_id,
        {"title": specialty_title, "practices": [], "file_hints": ["**/*"]},
    )
    if not isinstance(pack, dict):
        raise TypeError("Learned specialty pack must be an object.")
    existing = pack.setdefault("practices", [])
    if not isinstance(existing, list):
        raise TypeError("Learned specialty practices must be an array.")

    for practice in practices:
        if isinstance(practice, str) and practice.strip() and practice not in existing:
            existing.append(practice.strip())

    if "title" not in pack:
        pack["title"] = specialty_title
    if "file_hints" not in pack:
        pack["file_hints"] = ["**/*"]
    save_learned_practices(target=target, payload=learned)
    return learned


def merge_learned_extensions(*, config: dict, target: Path) -> dict:
    learned = load_learned_practices(target=target)
    if not learned:
        return config
    learned_extensions = learned.get("extensions", {})
    if not isinstance(learned_extensions, dict):
        raise TypeError("Learned practices extensions must be an object.")
    merged = dict(config)
    base_extensions = merged.get("extensions", {})
    if base_extensions and not isinstance(base_extensions, dict):
        raise ValueError("'extensions' must be an object when provided.")
    merged_extensions = dict(base_extensions) if isinstance(base_extensions, dict) else {}
    for section in ("personas", "baselines", "tools", "languages", "specialties", "strategies"):
        existing_section = merged_extensions.get(section, {})
        incoming_section = learned_extensions.get(section, {})
        if incoming_section and not isinstance(incoming_section, dict):
            raise ValueError(f"Learned extensions section '{section}' must be an object.")
        if existing_section and not isinstance(existing_section, dict):
            raise ValueError(f"Config extensions section '{section}' must be an object.")
        section_data = dict(existing_section) if isinstance(existing_section, dict) else {}
        if isinstance(incoming_section, dict):
            for key, value in incoming_section.items():
                section_data[key] = value
        if section_data:
            merged_extensions[section] = section_data
    if merged_extensions:
        merged["extensions"] = merged_extensions
    return merged
