from __future__ import annotations

import argparse


MODEL_TIER_BY_PERSONA = {
    "correctness": "high",
    "security": "high",
    "architecture": "high",
    "maintainability": "medium",
    "test-quality": "medium",
    "performance": "medium",
}
DEFAULT_MODEL_TIER = "medium"
TIER_RANK = {"low": 1, "medium": 2, "high": 3}
RANK_TIER = {1: "low", 2: "medium", 3: "high"}
STRATEGY_TIER_BUMP = {
    "adversarial": 1,
    "failure-mode": 1,
    "strategic-critic": 1,
    "devils-advocate": 0,
    "parallelization-critic": 0,
}

TOKEN_PROFILES: dict[str, dict] = {
    "balanced": {
        "toon": False,
        "cache_mode": "prompt",
        "model_routing": "right-size",
        "max_parallel_units": 6,
        "max_files_per_unit": 120,
        "max_file_hints": 12,
    },
    "efficient": {
        "toon": True,
        "cache_mode": "context",
        "model_routing": "right-size",
        "max_parallel_units": 4,
        "max_files_per_unit": 80,
        "max_file_hints": 8,
    },
    "ultra": {
        "toon": True,
        "cache_mode": "full",
        "model_routing": "right-size",
        "max_parallel_units": 3,
        "max_files_per_unit": 50,
        "max_file_hints": 6,
    },
}


def resolve_token_policy(config: dict, args: argparse.Namespace) -> dict:
    profile_name = config.get("token_profile", args.token_profile)
    if profile_name not in TOKEN_PROFILES:
        raise ValueError(f"Unknown token profile: {profile_name}. Available: {', '.join(sorted(TOKEN_PROFILES))}")

    policy = dict(TOKEN_PROFILES[profile_name])
    cfg_token = config.get("token_strategy", {})
    if cfg_token and not isinstance(cfg_token, dict):
        raise ValueError("'token_strategy' must be an object in config.")

    explicit_parallel = False
    for key in ("cache_mode", "model_routing", "max_parallel_units", "max_files_per_unit", "max_file_hints"):
        if key in cfg_token:
            policy[key] = cfg_token[key]
            if key == "max_parallel_units":
                explicit_parallel = True

    policy["toon"] = True
    if args.cache_mode:
        policy["cache_mode"] = args.cache_mode
    if args.model_routing:
        policy["model_routing"] = args.model_routing
    if args.max_parallel_units is not None:
        policy["max_parallel_units"] = args.max_parallel_units
        explicit_parallel = True
    if args.max_files_per_unit is not None:
        policy["max_files_per_unit"] = args.max_files_per_unit
    if args.max_file_hints is not None:
        policy["max_file_hints"] = args.max_file_hints

    if policy["cache_mode"] not in {"none", "prompt", "context", "full"}:
        raise ValueError("cache_mode must be one of: none, prompt, context, full")
    if policy["model_routing"] not in {"right-size", "fixed"}:
        raise ValueError("model_routing must be one of: right-size, fixed")
    for key in ("max_parallel_units", "max_files_per_unit", "max_file_hints"):
        value = policy[key]
        if not isinstance(value, int) or value < 1:
            raise ValueError(f"{key} must be a positive integer.")

    policy["profile"] = profile_name
    policy["max_parallel_units_explicit"] = explicit_parallel
    return policy


def choose_model_tier(persona_id: str, strategy_ids: list[str], model_routing: str) -> str:
    if model_routing == "fixed":
        return "medium"

    base_tier = MODEL_TIER_BY_PERSONA.get(persona_id, DEFAULT_MODEL_TIER)
    tier_rank = TIER_RANK[base_tier]
    bump = max([STRATEGY_TIER_BUMP.get(item, 0) for item in strategy_ids], default=0)
    final_rank = min(3, tier_rank + bump)
    return RANK_TIER[final_rank]


def toon_trim_hints(hints: list[str], max_file_hints: int) -> list[str]:
    ranked = sorted(dict.fromkeys(hints), key=lambda item: (item.count("*"), len(item)))
    return ranked[:max_file_hints]
