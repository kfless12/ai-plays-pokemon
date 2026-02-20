"""
tasks/manager.py — GoalManager: persistent memory for the AI's self-directed goals.

The LLM decides what to do. This module remembers that decision across
multiple LLM calls so the AI doesn't lose track of what it's working on.

Key principles:
  - The LLM creates goals, not us
  - Completion is detected from game state changes (not by asking the LLM)
  - Goal history provides context so the LLM knows what it already tried
  - Persists to disk so goals survive agent restarts
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from tasks.base import Goal, GoalStatus


GOALS_STATE_PATH = Path("/home/kfless/pokemon_ai/tasks_state.json")
MAX_GOAL_HISTORY = 20


class GoalManager:
    """
    Tracks the AI's current goal and goal history.

    The LLM tells us what it wants to do (a description string).
    We store it, track progress, check for completion, and provide
    context back to the LLM on subsequent calls.
    """

    def __init__(self):
        self.current_goal: Goal | None = None
        self.goal_history: list[dict] = []  # Summaries of past goals
        self._last_save: float = 0.0

    # ------------------------------------------------------------------
    # Goal lifecycle
    # ------------------------------------------------------------------

    def set_goal(self, description: str, state: dict, ui: dict):
        """
        Set a new goal from the LLM's decision.
        Archives the current goal if one exists.
        """
        # Archive current goal
        if self.current_goal and self.current_goal.status == GoalStatus.ACTIVE:
            self.current_goal.fail("replaced by new goal")
            self._archive(self.current_goal)

        self.current_goal = Goal(
            description=description,
            start_map=state.get("map", 0),
            start_x=state.get("x", 0),
            start_y=state.get("y", 0),
            start_party_count=ui.get("party_count", 0),
            start_badges=state.get("badges", 0),
        )
        self._save_soon()

    def tick(self, state: dict, ui: dict) -> GoalStatus | None:
        """
        Called each agent cycle. Checks completion and step limits.
        Returns the goal status, or None if no active goal.
        """
        if self.current_goal is None:
            return None
        if self.current_goal.status != GoalStatus.ACTIVE:
            return self.current_goal.status

        # Check if game state indicates the goal is accomplished
        if self.current_goal.check_completion(state, ui):
            self._archive(self.current_goal)
            self._save_soon()
            return self.current_goal.status

        # Increment step counter (may fail the goal if over limit)
        status = self.current_goal.tick()
        if status == GoalStatus.FAILED:
            self._archive(self.current_goal)
            self._save_soon()

        # Periodic save
        if time.time() - self._last_save > 15.0:
            self.save()

        return status

    def needs_new_goal(self) -> bool:
        """True if there's no active goal and the LLM should decide what to do."""
        if self.current_goal is None:
            return True
        return self.current_goal.status != GoalStatus.ACTIVE

    def cancel_goal(self, reason: str = "cancelled"):
        """Cancel the current goal."""
        if self.current_goal and self.current_goal.status == GoalStatus.ACTIVE:
            self.current_goal.fail(reason)
            self._archive(self.current_goal)
            self.current_goal = None
            self._save_soon()

    # ------------------------------------------------------------------
    # Context for LLM prompts
    # ------------------------------------------------------------------

    def get_goal_prompt_lines(self) -> list[str]:
        """
        Formatted lines to include in the LLM prompt.
        Tells the LLM what it's currently working on and what it already did.
        """
        lines = []

        if self.current_goal and self.current_goal.status == GoalStatus.ACTIVE:
            lines.append(f"YOUR CURRENT GOAL: {self.current_goal.description}")
            lines.append(f"  (working on this for {self.current_goal.steps_taken} steps)")
        else:
            lines.append("You have NO CURRENT GOAL. Decide what to do next.")

        # Show recent goal history so the LLM knows what it already accomplished/tried
        if self.goal_history:
            recent = self.goal_history[-5:]
            history_parts = []
            for entry in recent:
                desc = entry.get("description", "?")
                status = entry.get("status", "?")
                reason = entry.get("reason_done", "")
                if reason:
                    history_parts.append(f"{desc} [{status}: {reason}]")
                else:
                    history_parts.append(f"{desc} [{status}]")
            lines.append(f"Recent goals: {' → '.join(history_parts)}")

        return lines

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self):
        """Save goal state to disk."""
        data = {
            "current_goal": self.current_goal.to_dict() if self.current_goal else None,
            "goal_history": self.goal_history[-MAX_GOAL_HISTORY:],
            "saved_at": time.time(),
        }
        try:
            GOALS_STATE_PATH.write_text(json.dumps(data, indent=2))
            self._last_save = time.time()
        except OSError:
            pass

    def load(self) -> bool:
        """Load goal state from disk. Returns True if loaded successfully."""
        try:
            text = GOALS_STATE_PATH.read_text()
            data = json.loads(text)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return False

        if data.get("current_goal"):
            self.current_goal = Goal.from_dict(data["current_goal"])
        self.goal_history = data.get("goal_history", [])
        return True

    def _save_soon(self):
        self._last_save = 0  # Force save on next tick

    def _archive(self, goal: Goal):
        """Add a completed/failed goal to history."""
        self.goal_history.append({
            "description": goal.description,
            "status": goal.status.value,
            "reason_done": goal.reason_done,
            "steps_taken": goal.steps_taken,
            "completed_at": goal.completed_at,
        })
        if len(self.goal_history) > MAX_GOAL_HISTORY:
            self.goal_history = self.goal_history[-MAX_GOAL_HISTORY:]
