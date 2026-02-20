"""
tasks/base.py — Persistent goal tracking for the AI agent.

This is NOT a pre-built task library. The LLM decides its own goals.
This module provides:
  - A Goal dataclass that stores what the LLM decided to work on
  - Deterministic completion checks so we know when a goal is done
    without asking the LLM (which would be unreliable)
  - Persistence so goals survive across LLM calls and agent restarts

The LLM sets goals like "Go to Oak's Lab to get a starter Pokemon"
and the system tracks whether that goal has been achieved by checking
game state (e.g., party count increased, map changed, etc.).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class GoalStatus(str, Enum):
    ACTIVE = "active"
    DONE = "done"
    FAILED = "failed"


@dataclass
class Goal:
    """
    A goal the LLM decided to pursue.

    The LLM creates these by describing what it wants to do.
    The system tracks progress and checks completion deterministically.
    """
    description: str                    # What the LLM wants to accomplish
    status: GoalStatus = GoalStatus.ACTIVE
    created_at: float = field(default_factory=time.time)
    completed_at: float = 0.0
    steps_taken: int = 0
    max_steps: int = 300                # Safety limit to prevent infinite loops
    reason_done: str = ""               # Why it completed/failed

    # Snapshot of game state when goal was created (for change detection)
    start_map: int = -1
    start_x: int = 0
    start_y: int = 0
    start_party_count: int = 0
    start_badges: int = 0

    def tick(self) -> GoalStatus:
        """Increment step counter; fail if over limit."""
        if self.status != GoalStatus.ACTIVE:
            return self.status
        self.steps_taken += 1
        if self.steps_taken >= self.max_steps:
            self.status = GoalStatus.FAILED
            self.completed_at = time.time()
            self.reason_done = f"exceeded {self.max_steps} step limit without completion"
        return self.status

    def complete(self, reason: str = "completed"):
        self.status = GoalStatus.DONE
        self.completed_at = time.time()
        self.reason_done = reason

    def fail(self, reason: str = "failed"):
        self.status = GoalStatus.FAILED
        self.completed_at = time.time()
        self.reason_done = reason

    def check_completion(self, state: dict, ui: dict) -> bool:
        """
        Check if the goal appears to be accomplished based on
        meaningful game state changes since the goal was created.

        This uses heuristics — if the game state changed significantly
        in a way that aligns with progress, the goal is likely done.
        We check multiple signals and let the LLM confirm/set new goals.
        """
        map_id = state.get("map", 0)
        party_count = ui.get("party_count", 0)
        badges = state.get("badges", 0)

        # Badge gained — always a major milestone
        if badges > self.start_badges:
            self.complete(f"badge gained ({self.start_badges} -> {badges})")
            return True

        # Party size increased — got a new Pokemon
        if party_count > self.start_party_count:
            self.complete(f"party grew ({self.start_party_count} -> {party_count})")
            return True

        # Map changed — likely reached a destination
        # (only count this after some steps to avoid false positives from warps)
        if self.steps_taken > 10 and map_id != self.start_map:
            self.complete(f"reached new area (map {self.start_map} -> {map_id})")
            return True

        return False

    def to_dict(self) -> dict:
        return {
            "description": self.description,
            "status": self.status.value,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "steps_taken": self.steps_taken,
            "max_steps": self.max_steps,
            "reason_done": self.reason_done,
            "start_map": self.start_map,
            "start_x": self.start_x,
            "start_y": self.start_y,
            "start_party_count": self.start_party_count,
            "start_badges": self.start_badges,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Goal:
        return cls(
            description=d.get("description", ""),
            status=GoalStatus(d.get("status", "active")),
            created_at=d.get("created_at", 0.0),
            completed_at=d.get("completed_at", 0.0),
            steps_taken=d.get("steps_taken", 0),
            max_steps=d.get("max_steps", 300),
            reason_done=d.get("reason_done", ""),
            start_map=d.get("start_map", -1),
            start_x=d.get("start_x", 0),
            start_y=d.get("start_y", 0),
            start_party_count=d.get("start_party_count", 0),
            start_badges=d.get("start_badges", 0),
        )
