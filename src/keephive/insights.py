"""Pure deterministic aggregation of Claude Code /insights session data.

Reads facets + session-meta JSON files from ~/.claude/usage-data/ and
produces distributions, cross-field correlations, and per-project breakdowns.
No LLM calls. All computation is local.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path


def _usage_data_dir() -> Path:
    """Root of Claude Code usage data."""
    return Path.home() / ".claude" / "usage-data"


def _session_meta_dir() -> Path:
    """Session-meta directory, respecting HIVE_CC_META_DIR for test isolation."""
    env = os.environ.get("HIVE_CC_META_DIR")
    return Path(env) if env else _usage_data_dir() / "session-meta"


def read_facets_full() -> dict[str, dict]:
    """Read all facets JSON files, keyed by session_id.

    Each value is the full facets dict (outcome, session_type,
    friction_counts, goal_categories, user_satisfaction_counts, etc.).
    Silently skips malformed files.
    """
    facets_dir = _usage_data_dir() / "facets"
    if not facets_dir.exists():
        return {}

    result: dict[str, dict] = {}
    for fpath in facets_dir.glob("*.json"):
        try:
            data = json.loads(fpath.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        sid = data.get("session_id", fpath.stem)
        result[sid] = data

    return result


def read_session_meta() -> dict[str, dict]:
    """Read all session-meta JSON files, keyed by session_id.

    Each value includes project_path, duration_minutes, tool_counts,
    user_message_count, start_time, etc. Silently skips malformed files.
    """
    meta_dir = _session_meta_dir()
    if not meta_dir.exists():
        return {}

    result: dict[str, dict] = {}
    for fpath in meta_dir.glob("*.json"):
        try:
            data = json.loads(fpath.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        sid = data.get("session_id", fpath.stem)
        result[sid] = data

    return result


def read_joined_sessions() -> list[dict]:
    """Inner-join facets + session-meta on session_id.

    Returns list of merged dicts. Each has all facets fields plus
    meta fields (project_path, duration_minutes, tool_counts, etc.)
    and a derived 'project_name' (last path component of project_path).
    Sessions without a match in both sets are excluded.
    """
    facets = read_facets_full()
    meta = read_session_meta()

    joined: list[dict] = []
    for sid in facets:
        if sid not in meta:
            continue
        merged = {**facets[sid], **meta[sid]}
        # Derive short project name from project_path
        project_path = merged.get("project_path", "")
        if project_path:
            merged["project_name"] = Path(project_path).name
        else:
            merged["project_name"] = ""
        joined.append(merged)

    return joined


def aggregate_insights(sessions: list[dict]) -> dict:
    """Produce aggregated insight distributions from joined session data.

    Returns dict with:
      total_sessions: int
      outcome_dist: {outcome: count}
      type_dist: {session_type: count}
      satisfaction_dist: {level: total_count}
      goal_dist: {category: count}  (top 10)
      friction_dist: {type: {"count": N, "sessions": N}}
      helpfulness_dist: {level: count}

      patterns: list of detected pattern dicts:
        - type_outcome: per session_type, outcome distribution + achieved rate
        - goal_satisfaction: per goal_category (3+ sessions), avg satisfaction
        - friction_by_type: session_types with highest friction rates
        - duration_by_outcome: avg duration per outcome bucket

      per_project: {project_name: {total, outcomes, types}}
    """
    if not sessions:
        return {
            "total_sessions": 0,
            "outcome_dist": {},
            "type_dist": {},
            "satisfaction_dist": {},
            "goal_dist": {},
            "friction_dist": {},
            "helpfulness_dist": {},
            "patterns": [],
            "per_project": {},
        }

    total = len(sessions)

    # Basic distributions
    outcome_dist: dict[str, int] = defaultdict(int)
    type_dist: dict[str, int] = defaultdict(int)
    satisfaction_dist: dict[str, int] = defaultdict(int)
    goal_dist: dict[str, int] = defaultdict(int)
    friction_dist: dict[str, dict[str, int]] = {}
    helpfulness_dist: dict[str, int] = defaultdict(int)

    # For pattern detection
    type_outcomes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    type_friction_counts: dict[str, list[int]] = defaultdict(list)
    goal_sessions: dict[str, list[dict]] = defaultdict(list)
    outcome_durations: dict[str, list[float]] = defaultdict(list)
    per_project: dict[str, dict] = {}

    for s in sessions:
        outcome = s.get("outcome", "unknown")
        stype = s.get("session_type", "unknown")
        helpfulness = s.get("claude_helpfulness", "unknown")

        outcome_dist[outcome] += 1
        type_dist[stype] += 1
        helpfulness_dist[helpfulness] += 1

        # Satisfaction: sum counts across levels
        for level, count in s.get("user_satisfaction_counts", {}).items():
            satisfaction_dist[level] += count

        # Goals: each category key
        for cat in s.get("goal_categories", {}):
            goal_dist[cat] += 1

        # Friction
        session_friction_total = 0
        for ftype, count in s.get("friction_counts", {}).items():
            if ftype not in friction_dist:
                friction_dist[ftype] = {"count": 0, "sessions": 0}
            friction_dist[ftype]["count"] += count
            friction_dist[ftype]["sessions"] += 1
            session_friction_total += count

        # Pattern tracking
        type_outcomes[stype][outcome] += 1
        type_friction_counts[stype].append(session_friction_total)

        for cat in s.get("goal_categories", {}):
            goal_sessions[cat].append(s)

        duration = s.get("duration_minutes")
        if duration is not None:
            outcome_durations[outcome].append(float(duration))

        # Per-project aggregation
        proj = s.get("project_name", "")
        if proj:
            if proj not in per_project:
                per_project[proj] = {
                    "total": 0,
                    "outcomes": defaultdict(int),
                    "types": defaultdict(int),
                }
            per_project[proj]["total"] += 1
            per_project[proj]["outcomes"][outcome] += 1
            per_project[proj]["types"][stype] += 1

    # Detect patterns
    patterns: list[dict] = []

    # Pattern 1: Per session_type, outcome distribution with achieved rate
    for stype, outcomes in type_outcomes.items():
        total_for_type = sum(outcomes.values())
        if total_for_type < 3:
            continue
        achieved = outcomes.get("fully_achieved", 0) + outcomes.get("mostly_achieved", 0)
        achieved_rate = achieved / total_for_type
        patterns.append(
            {
                "type": "type_outcome",
                "session_type": stype,
                "total": total_for_type,
                "achieved_rate": round(achieved_rate, 2),
                "outcomes": dict(outcomes),
            }
        )

    # Pattern 2: Per goal_category (3+ sessions), flag high-dissatisfaction
    _SATISFACTION_SCORE = {
        "frustrated": 1,
        "dissatisfied": 2,
        "neutral": 3,
        "likely_satisfied": 4,
        "satisfied": 5,
    }
    for cat, cat_sessions in goal_sessions.items():
        if len(cat_sessions) < 3:
            continue
        scores: list[float] = []
        for s in cat_sessions:
            for level, count in s.get("user_satisfaction_counts", {}).items():
                scores.extend([_SATISFACTION_SCORE.get(level, 3)] * count)
        if scores:
            avg_score = sum(scores) / len(scores)
            patterns.append(
                {
                    "type": "goal_satisfaction",
                    "goal_category": cat,
                    "sessions": len(cat_sessions),
                    "avg_satisfaction": round(avg_score, 2),
                }
            )

    # Pattern 3: Friction rate by session_type
    for stype, friction_list in type_friction_counts.items():
        if len(friction_list) < 3:
            continue
        with_friction = sum(1 for f in friction_list if f > 0)
        rate = with_friction / len(friction_list)
        if rate > 0:
            patterns.append(
                {
                    "type": "friction_by_type",
                    "session_type": stype,
                    "sessions": len(friction_list),
                    "friction_rate": round(rate, 2),
                    "avg_friction_count": round(sum(friction_list) / len(friction_list), 1),
                }
            )

    # Pattern 4: Avg duration by outcome bucket
    for outcome, durations in outcome_durations.items():
        if len(durations) < 2:
            continue
        avg_dur = sum(durations) / len(durations)
        patterns.append(
            {
                "type": "duration_by_outcome",
                "outcome": outcome,
                "sessions": len(durations),
                "avg_duration_minutes": round(avg_dur, 1),
            }
        )

    # Sort goals by frequency, keep top 10
    sorted_goals = dict(sorted(goal_dist.items(), key=lambda x: -x[1])[:10])

    # Finalize per_project: convert defaultdicts to dicts
    for proj in per_project.values():
        proj["outcomes"] = dict(proj["outcomes"])
        proj["types"] = dict(proj["types"])

    return {
        "total_sessions": total,
        "outcome_dist": dict(outcome_dist),
        "type_dist": dict(type_dist),
        "satisfaction_dist": dict(satisfaction_dist),
        "goal_dist": sorted_goals,
        "friction_dist": friction_dist,
        "helpfulness_dist": dict(helpfulness_dist),
        "patterns": patterns,
        "per_project": per_project,
    }
