from __future__ import annotations

from copy import deepcopy


CURRENT_SCHEMA_VERSION = 2
SCHEMA_VERSION_KEY = "schema_version"
DEPRECATION_KEY_RENAMES = {
    "reviewer_personas": "personas",
    "methodology_packs": "baselines",
    "tool_packs": "tools",
    "language_packs": "languages",
    "domain_packs": "specialties",
    "challenge_strategies": "strategies",
}


def _as_dict(payload: object, *, label: str) -> dict:
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object.")
    return payload


def migrate_config_payload(payload: dict) -> tuple[dict, list[str]]:
    config = deepcopy(_as_dict(payload, label="Config"))
    notes: list[str] = []
    for old_key, new_key in DEPRECATION_KEY_RENAMES.items():
        if old_key in config and new_key in config:
            if config[old_key] != config[new_key]:
                raise ValueError(
                    f"Conflicting config keys '{old_key}' and '{new_key}'. "
                    f"Remove deprecated '{old_key}' or align values."
                )
            config.pop(old_key)
            notes.append(f"Removed deprecated config key '{old_key}' in favor of '{new_key}'.")
            continue
        if old_key in config and new_key not in config:
            config[new_key] = config.pop(old_key)
            notes.append(f"Migrated deprecated config key '{old_key}' -> '{new_key}'.")
    previous_version = int(config.get(SCHEMA_VERSION_KEY, 1))
    if previous_version < CURRENT_SCHEMA_VERSION:
        config[SCHEMA_VERSION_KEY] = CURRENT_SCHEMA_VERSION
        notes.append(f"Upgraded config schema_version {previous_version} -> {CURRENT_SCHEMA_VERSION}.")
    elif SCHEMA_VERSION_KEY not in config:
        config[SCHEMA_VERSION_KEY] = CURRENT_SCHEMA_VERSION
    return config, notes


def migrate_state_payload(payload: dict) -> tuple[dict, list[str]]:
    state = deepcopy(_as_dict(payload, label="State"))
    notes: list[str] = []
    selections = state.get("selections")
    if isinstance(selections, dict):
        for old_key, new_key in DEPRECATION_KEY_RENAMES.items():
            if old_key in selections and new_key in selections:
                if selections[old_key] != selections[new_key]:
                    raise ValueError(
                        f"Conflicting state selection keys '{old_key}' and '{new_key}'. "
                        f"Remove deprecated '{old_key}' or align values."
                    )
                selections.pop(old_key)
                notes.append(f"Removed deprecated state selection key '{old_key}' in favor of '{new_key}'.")
                continue
            if old_key in selections and new_key not in selections:
                selections[new_key] = selections.pop(old_key)
                notes.append(f"Migrated deprecated state selection key '{old_key}' -> '{new_key}'.")
    previous_version = int(state.get(SCHEMA_VERSION_KEY, 1))
    if previous_version < CURRENT_SCHEMA_VERSION:
        state[SCHEMA_VERSION_KEY] = CURRENT_SCHEMA_VERSION
        notes.append(f"Upgraded state schema_version {previous_version} -> {CURRENT_SCHEMA_VERSION}.")
    elif SCHEMA_VERSION_KEY not in state:
        state[SCHEMA_VERSION_KEY] = CURRENT_SCHEMA_VERSION
    return state, notes
