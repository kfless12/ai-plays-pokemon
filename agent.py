"""
agent.py — Pokemon Red AI Agent v7.0 — Self-Directed AI with Goal Tracking

Architecture:
  - Startup: deterministic automation (title → intro → names)
  - Playing: The LLM decides its own goals and actions
  - GoalManager: remembers what the LLM decided across calls
  - Navigator: handles pathfinding when the LLM picks a navigation action
  - Completion: detected from game state changes (map, party, badges)

v7.0 changes (Objective 2 — AI-Directed Goals):
  - The LLM sets its own goals ("Go to Oak's Lab to get a starter")
  - GoalManager persists goals across LLM calls and agent restarts
  - The LLM is told its current goal each query so it stays focused
  - Completion detected from game state (not by asking the LLM)
  - Goal history shows the LLM what it already accomplished/tried
  - No hardcoded task library — the AI plays the game
"""

import json
import time
import sys
from pathlib import Path
from datetime import datetime

from llm_client import check_ollama_running, detect_model, get_llm_decision
from navigator import Navigator, get_available_actions, describe_location
from context.context_builder import ContextBuilder
from tasks.manager import GoalManager
from tasks.base import GoalStatus

STATE_PATH = Path("/home/kfless/pokemon_ai/state.json")
ACTION_PATH = Path("/home/kfless/pokemon_ai/action.txt")
AGENT_LOG_PATH = Path("/home/kfless/pokemon_ai/agent_log.txt")

LLM_ENABLED = True
LLM_TIMEOUT = 45.0
LLM_COOLDOWN = 2.0

# --------------- Logging ---------------

def agent_log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"[{ts}] {msg}\n"
    try:
        with open(AGENT_LOG_PATH, "a") as f:
            f.write(line)
    except Exception:
        pass
    print(line.strip())


# --------------- State Reading ---------------

def read_state() -> dict | None:
    try:
        text = STATE_PATH.read_text()
        if not text.strip():
            return None
        return json.loads(text)
    except (json.JSONDecodeError, FileNotFoundError, OSError):
        return None


def write_action(action: str):
    ACTION_PATH.write_text(action + "\n")


def get_ui(state: dict) -> dict:
    ui = state.get("ui", {})
    return {
        "in_battle":              ui.get("in_battle", False),
        "battle_type":            ui.get("battle_type", 0),
        "textbox_active":         ui.get("textbox_active", False),
        "textbox_id":             ui.get("textbox_id", 0),
        "text_printing":          ui.get("text_printing", False),
        "menu_active":            ui.get("menu_active", False),
        "input_ignored":          ui.get("joypad_disabled", ui.get("input_ignored", False)),
        "joy_flags":              ui.get("wd730", ui.get("joy_flags", 0)),
        "screen_text":            ui.get("screen_text", ""),
        "current_menu_item":      ui.get("current_menu_item", 0),
        "menu_cursor_x":          ui.get("menu_cursor_x", 0),
        "menu_cursor_y":          ui.get("menu_cursor_y", 0),
        "menu_max_item":          ui.get("menu_max_item", 0),
        "battle_menu_selection":  ui.get("battle_menu_selection", "NONE"),
        "move_list_index":        ui.get("move_list_index", 0),
        "startup_phase":          ui.get("startup_phase", "UNKNOWN"),
        "party_count":            ui.get("party_count", 0),
        "anim_counter":           ui.get("anim_counter", 0),
    }


# --------------- Name Entry System ---------------

KEYBOARD = {}
for i, ch in enumerate("ABCDEFGHI"):
    KEYBOARD[ch] = (0, i)
for i, ch in enumerate("JKLMNOPQR"):
    KEYBOARD[ch] = (1, i)
for i, ch in enumerate("STUVWXYZ "):
    KEYBOARD[ch] = (2, i)


def generate_name_sequence(name: str) -> list[tuple[str, float]]:
    seq: list[tuple[str, float]] = []
    cur_row, cur_col = 0, 0
    for ch in name.upper():
        if ch not in KEYBOARD:
            continue
        target_row, target_col = KEYBOARD[ch]
        for _ in range(abs(target_row - cur_row)):
            seq.append(("DOWN 6" if target_row > cur_row else "UP 6", 0.20))
        for _ in range(abs(target_col - cur_col)):
            seq.append(("RIGHT 6" if target_col > cur_col else "LEFT 6", 0.20))
        seq.append(("A 4", 0.30))
        cur_row, cur_col = target_row, target_col
    seq.append(("START 4", 0.5))
    return seq


PLAYER_NAME = "REDAI"
RIVAL_NAME = "BLUEAI"
PLAYER_NAME_SEQ = generate_name_sequence(PLAYER_NAME)
RIVAL_NAME_SEQ = generate_name_sequence(RIVAL_NAME)


# --------------- Action Queue ---------------

class ActionQueue:
    def __init__(self):
        self.queue: list[tuple[str, float]] = []
        self.wait_until: float = 0.0

    def load(self, seq):
        self.queue = list(seq)
        self.wait_until = 0.0

    def is_active(self):
        return len(self.queue) > 0

    def next_action(self) -> str | None:
        if not self.queue or time.time() < self.wait_until:
            return None
        action_str, delay = self.queue.pop(0)
        self.wait_until = time.time() + delay
        return action_str


# --------------- Startup State Machine ---------------

class StartupStateMachine:
    def __init__(self):
        self.last_phase = None
        self.cooldown_until: float = 0.0
        self.action_queue = ActionQueue()
        self.name_entry_count = 0

    def _set_cooldown(self, s):
        self.cooldown_until = time.time() + s

    def _in_cooldown(self):
        return time.time() < self.cooldown_until

    def update(self, state, ui) -> str | None:
        phase = ui["startup_phase"]
        if phase != self.last_phase:
            agent_log(f"STARTUP_PHASE: {self.last_phase} -> {phase}")
            self.last_phase = phase

        if phase == "PLAYING":
            return None

        if self.action_queue.is_active():
            action = self.action_queue.next_action()
            if action:
                agent_log(f"NAME_ENTRY: '{action}' ({len(self.action_queue.queue)} left)")
                return action
            return "WAIT"

        if self._in_cooldown():
            return "WAIT"

        if phase == "TITLE_SCREEN":
            self._set_cooldown(0.3)
            return "START"

        if phase == "INTRO_SCRIPT":
            mm = ui["menu_max_item"]
            if mm == 3 and ui["startup_phase"] == "INTRO_SCRIPT":
                mi = ui["current_menu_item"]
                if mi != 0:
                    self._set_cooldown(0.25)
                    return "UP 6"
                if self.name_entry_count == 0:
                    agent_log(f"NAME_SELECT: player '{PLAYER_NAME}'")
                    self.action_queue.load([("A 4", 1.0)] + PLAYER_NAME_SEQ)
                    self.name_entry_count = 1
                    return "WAIT"
                elif self.name_entry_count == 1:
                    agent_log(f"NAME_SELECT: rival '{RIVAL_NAME}'")
                    self.action_queue.load([("A 4", 1.0)] + RIVAL_NAME_SEQ)
                    self.name_entry_count = 2
                    return "WAIT"
            self._set_cooldown(0.25)
            return "A"

        if phase == "UNKNOWN":
            self._set_cooldown(0.4)
            return "A"

        return "WAIT"


# --------------- Game Policy: LLM-Directed with Goal Tracking ---------------

class GamePolicy:
    """
    The AI plays the game. This class:
    1. Provides game state context to the LLM
    2. Lets the LLM set its own goals and pick actions
    3. Tracks goals persistently so the LLM stays focused
    4. Detects goal completion from game state changes
    5. Uses the Navigator for pathfinding when the LLM picks a nav action
    """

    def __init__(self):
        self.navigator = Navigator()
        self.context_builder = ContextBuilder()
        self.goal_manager = GoalManager()
        self.llm_available = False
        self.model_name: str | None = None
        self.last_llm_query: float = 0.0
        self.action_history: list[str] = []
        self.consecutive_failures: int = 0
        self.last_map: int = -1

        # Load persisted goal state
        if self.goal_manager.load():
            agent_log("GOALS: Loaded persisted state")
            if self.goal_manager.current_goal:
                g = self.goal_manager.current_goal
                agent_log(f"GOALS: Resuming '{g.description}' ({g.status.value}, {g.steps_taken} steps)")

    def initialize_llm(self) -> bool:
        if not check_ollama_running():
            agent_log("LLM: Ollama not running")
            return False
        self.model_name = detect_model()
        if not self.model_name:
            agent_log("LLM: No model found")
            return False
        agent_log(f"LLM: Connected, model={self.model_name}")
        self.llm_available = True
        return True

    def _add_history(self, action: str, outcome: str = ""):
        self.action_history.append(action)
        if len(self.action_history) > 30:
            self.action_history = self.action_history[-30:]
        self.context_builder.record_action(action, outcome)

    def update(self, state: dict, ui: dict) -> tuple[str, str]:
        """Returns (action_string, source_description)."""
        map_id = state.get("map", 0)
        x = state.get("x", 0)
        y = state.get("y", 0)

        # Cancel navigation if map changed
        if map_id != self.last_map and self.last_map != -1:
            if self.navigator.is_active():
                agent_log(f"NAV: Map changed {self.last_map}->{map_id}, cancelling nav")
                self.navigator.cancel()
        self.last_map = map_id

        # Handle input-ignored states
        if ui["input_ignored"]:
            return "WAIT", "input_ignored"

        # Handle textboxes — press A to advance dialog
        if ui["textbox_active"]:
            screen_text = ui.get("screen_text", "")
            if screen_text:
                self._add_history("A", f"text: {screen_text[:60]}")
            else:
                self._add_history("A")
            return "A", "textbox"

        # Handle battles
        if ui["in_battle"]:
            return self._battle_policy(state, ui)

        # --- Tick the goal manager: check completion ---
        goal_status = self.goal_manager.tick(state, ui)
        if goal_status == GoalStatus.DONE:
            g = self.goal_manager.current_goal
            agent_log(f"GOAL DONE: '{g.description}' — {g.reason_done}")
            self._add_history("goal_done", g.description)
            self.navigator.cancel()
        elif goal_status == GoalStatus.FAILED:
            g = self.goal_manager.current_goal
            agent_log(f"GOAL FAILED: '{g.description}' — {g.reason_done}")
            self._add_history("goal_failed", g.description)
            self.navigator.cancel()

        # --- Navigator active: execute next step ---
        if self.navigator.is_active():
            step = self.navigator.next_step(x, y, map_id)
            if step:
                self._add_history(step)
                return step, f"nav:{self.navigator.get_status()}"
            else:
                agent_log(f"NAV: Completed — {self.navigator.get_status()}")
                self.navigator.cancel()

        # --- Ask the LLM what to do ---
        return self._ask_llm(state, ui, map_id)

    def _battle_policy(self, state: dict, ui: dict) -> tuple[str, str]:
        """Simple battle handling — press A to fight."""
        if ui["text_printing"]:
            return "WAIT", "battle_text"
        self._add_history("A")
        return "A", "battle"

    def _ask_llm(self, state: dict, ui: dict, map_id: int) -> tuple[str, str]:
        """
        Ask the LLM what to do. The LLM may:
        - Set a new goal (if it has none or wants to change)
        - Pick an action to work toward its current goal
        """
        now = time.time()

        # Cooldown between LLM queries
        if now - self.last_llm_query < LLM_COOLDOWN:
            return self._fallback_action(map_id), "cooldown"

        if not (LLM_ENABLED and self.llm_available):
            return self._fallback_action(map_id), "llm_unavailable"

        self.last_llm_query = now

        # Build context snapshot with goal info
        available_actions = get_available_actions(map_id)
        snapshot = self.context_builder.build(
            state=state,
            available_actions=available_actions,
        )
        # Inject goal context so the LLM knows what it's working on
        snapshot.task_lines = self.goal_manager.get_goal_prompt_lines()

        goal_desc = ""
        if self.goal_manager.current_goal and self.goal_manager.current_goal.status == GoalStatus.ACTIVE:
            goal_desc = f" goal='{self.goal_manager.current_goal.description}'"

        agent_log(
            f"LLM: Querying on {snapshot.map_name} ({map_id}){goal_desc} "
            f"party={snapshot.party_count} history={len(snapshot.recent_history)}"
        )

        start = time.time()
        decision = get_llm_decision(state, ui, self.action_history, timeout=LLM_TIMEOUT)
        elapsed = time.time() - start

        action_name = decision.get("action")
        new_goal = decision.get("goal")
        reason = decision.get("reason", "")
        error = decision.get("error", "")

        # Handle new goal from the LLM
        if new_goal:
            self.goal_manager.set_goal(new_goal, state, ui)
            agent_log(f"GOAL SET: '{new_goal}' in {elapsed:.1f}s")

        if error:
            self.consecutive_failures += 1
            agent_log(f"LLM: Failed — {error} (fails={self.consecutive_failures})")
            if self.consecutive_failures >= 5:
                agent_log("LLM: Too many failures, disabling 60s")
                self.llm_available = False
            return self._fallback_action(map_id), "llm_error"

        if not action_name:
            return self._fallback_action(map_id), "no_action"

        self.consecutive_failures = 0
        agent_log(f"LLM: Action='{action_name}' in {elapsed:.1f}s — {reason}")

        # Handle special actions
        if action_name == "INTERACT":
            self._add_history("A")
            return "A", f"LLM:interact ({elapsed:.1f}s)"
        if action_name == "WAIT":
            return "WAIT", f"LLM:wait ({elapsed:.1f}s)"
        if action_name == "FIGHT":
            self._add_history("A")
            return "A", f"LLM:fight ({elapsed:.1f}s)"
        if action_name == "RUN":
            self._add_history("DOWN 16")
            return "DOWN 16", f"LLM:run ({elapsed:.1f}s)"

        # Try to use navigator for the action
        if self.navigator.set_goal(action_name, map_id):
            agent_log(f"NAV: Goal set — {action_name}")
            x = state.get("x", 0)
            y = state.get("y", 0)
            step = self.navigator.next_step(x, y, map_id)
            if step:
                self._add_history(step)
                return step, f"LLM→nav:{action_name} ({elapsed:.1f}s)"

        # Navigator doesn't know this action — just use fallback
        agent_log(f"NAV: No waypoint for '{action_name}' on map {map_id}")
        return self._fallback_action(map_id), f"LLM:{action_name} (no nav)"

    def _fallback_action(self, map_id: int) -> str:
        """Simple fallback when LLM is unavailable or between queries."""
        defaults = {
            38: "RIGHT 16",   # bedroom: go right toward stairs
            37: "DOWN 16",    # house 1F: go down toward exit
            0:  "DOWN 16",    # Pallet Town: go south
            40: "UP 16",      # Oak's lab: go toward Oak
            12: "UP 16",      # Route 1: go north
        }
        action = defaults.get(map_id, "DOWN 16")
        self._add_history(action)
        return action


# --------------- Main Loop ---------------

def main():
    print("=" * 60)
    print("Pokemon Red AI Agent v7.0 — Self-Directed AI")
    print(f"  Player: {PLAYER_NAME} | Rival: {RIVAL_NAME}")
    print(f"  LLM: {'enabled' if LLM_ENABLED else 'disabled'}")
    print(f"  Goals: AI sets its own goals; tracked persistently")
    print(f"  Context: ContextBuilder + goal memory + action history")
    print("=" * 60)
    agent_log("Agent v7.0 started — Self-Directed AI with Goal Tracking")

    startup_sm = StartupStateMachine()
    game_policy = GamePolicy()

    if LLM_ENABLED:
        if game_policy.initialize_llm():
            print(f"  Model: {game_policy.model_name}")
        else:
            print("  LLM: NOT AVAILABLE (using fallback)")
            print("  Run: ollama serve & ollama pull qwen2.5:1.5b")
    print("=" * 60)

    last_frame = None
    wait_streak = 0

    while True:
        state = read_state()
        if not state:
            time.sleep(0.05)
            continue

        frame = state.get("frame")
        if frame == last_frame:
            time.sleep(0.03)
            continue
        last_frame = frame

        ui = get_ui(state)

        # Startup automation
        startup_action = startup_sm.update(state, ui)
        if startup_action is not None:
            if startup_action == "WAIT":
                wait_streak += 1
                if wait_streak > 30:
                    startup_action = "A"
                    wait_streak = 0
                else:
                    time.sleep(0.05)
                    continue
            else:
                wait_streak = 0

            phase = ui.get("startup_phase", "?")
            agent_log(f"f={frame} [{phase}] -> {startup_action}")
            write_action(startup_action)
            time.sleep(0.15)
            continue

        # --- PLAYING phase: game policy ---
        action, source = game_policy.update(state, ui)

        if action == "WAIT":
            wait_streak += 1
            if wait_streak > 30:
                action = "A"
                source = "wait_timeout"
                wait_streak = 0
            else:
                time.sleep(0.05)
                continue
        else:
            wait_streak = 0

        # Log
        map_id = state.get("map", 0)
        x = state.get("x", 0)
        y = state.get("y", 0)
        loc = describe_location(map_id, x, y)

        agent_log(f"f={frame} {loc} [{source}] -> {action}")
        write_action(action)

        # Rate limit
        if "LLM" in source:
            time.sleep(0.1)
        elif "nav:" in source:
            time.sleep(0.3)
        else:
            time.sleep(0.25)

        # Retry LLM if disabled
        if LLM_ENABLED and not game_policy.llm_available:
            if time.time() - game_policy.last_llm_query > 60:
                agent_log("LLM: Retrying...")
                game_policy.initialize_llm()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        agent_log("Agent stopped by user")
        print("\nAgent stopped.")
        sys.exit(0)
