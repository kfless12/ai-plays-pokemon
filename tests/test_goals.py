"""
tests/test_goals.py — Unit tests for Objective 2: Goal System

Tests:
  - Goal creation and state tracking
  - Goal completion detection from game state changes
  - Goal step limit / timeout
  - GoalManager lifecycle (set → tick → complete → archive → needs_new_goal)
  - GoalManager persistence (save/load)
  - Goal prompt line generation for LLM context
  - LLM response parsing (action + goal extraction)
"""

import json
import sys
import os
import time
import tempfile

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tasks.base import Goal, GoalStatus
from tasks.manager import GoalManager


# ---------------------------------------------------------------------------
# Synthetic state helpers
# ---------------------------------------------------------------------------

def make_state(map_id=0, x=9, y=16, badges=0):
    return {"map": map_id, "x": x, "y": y, "badges": badges}


def make_ui(party_count=0):
    return {"party_count": party_count}


# ===========================================================================
# Tests: Goal basics
# ===========================================================================

def test_goal_creation():
    g = Goal(description="Go to Oak's Lab")
    assert g.description == "Go to Oak's Lab"
    assert g.status == GoalStatus.ACTIVE
    assert g.steps_taken == 0
    assert g.reason_done == ""


def test_goal_tick_increments_steps():
    g = Goal(description="test", max_steps=100)
    g.tick()
    assert g.steps_taken == 1
    g.tick()
    assert g.steps_taken == 2
    assert g.status == GoalStatus.ACTIVE


def test_goal_tick_fails_at_step_limit():
    g = Goal(description="test", max_steps=5)
    for _ in range(4):
        status = g.tick()
        assert status == GoalStatus.ACTIVE
    status = g.tick()
    assert status == GoalStatus.FAILED
    assert "exceeded" in g.reason_done
    assert g.completed_at > 0


def test_goal_tick_no_op_after_done():
    g = Goal(description="test")
    g.complete("finished")
    status = g.tick()
    assert status == GoalStatus.DONE
    assert g.steps_taken == 0  # tick didn't increment


def test_goal_tick_no_op_after_failed():
    g = Goal(description="test")
    g.fail("gave up")
    status = g.tick()
    assert status == GoalStatus.FAILED


def test_goal_complete():
    g = Goal(description="test")
    g.complete("reached destination")
    assert g.status == GoalStatus.DONE
    assert g.reason_done == "reached destination"
    assert g.completed_at > 0


def test_goal_fail():
    g = Goal(description="test")
    g.fail("stuck in a wall")
    assert g.status == GoalStatus.FAILED
    assert g.reason_done == "stuck in a wall"


# ===========================================================================
# Tests: Goal completion detection
# ===========================================================================

def test_completion_badge_gained():
    g = Goal(description="Beat Brock", start_badges=0, start_map=51,
             start_x=5, start_y=5, start_party_count=1)
    state = make_state(map_id=51, badges=1)
    ui = make_ui(party_count=1)
    assert g.check_completion(state, ui) is True
    assert g.status == GoalStatus.DONE
    assert "badge gained" in g.reason_done


def test_completion_party_grew():
    g = Goal(description="Get starter Pokemon", start_badges=0, start_map=40,
             start_x=5, start_y=5, start_party_count=0)
    state = make_state(map_id=40, badges=0)
    ui = make_ui(party_count=1)
    assert g.check_completion(state, ui) is True
    assert g.status == GoalStatus.DONE
    assert "party grew" in g.reason_done


def test_completion_map_changed_after_steps():
    g = Goal(description="Leave the house", start_badges=0, start_map=37,
             start_x=2, start_y=5, start_party_count=0)
    # Simulate 15 steps taken
    g.steps_taken = 15
    state = make_state(map_id=0, badges=0)
    ui = make_ui(party_count=0)
    assert g.check_completion(state, ui) is True
    assert g.status == GoalStatus.DONE
    assert "reached new area" in g.reason_done


def test_completion_map_changed_too_early_ignored():
    """Map change within first 10 steps should NOT trigger completion."""
    g = Goal(description="Leave the house", start_badges=0, start_map=37,
             start_x=2, start_y=5, start_party_count=0)
    g.steps_taken = 5  # Too early
    state = make_state(map_id=0, badges=0)
    ui = make_ui(party_count=0)
    assert g.check_completion(state, ui) is False
    assert g.status == GoalStatus.ACTIVE


def test_completion_no_change():
    """No state change means goal is not complete."""
    g = Goal(description="Explore", start_badges=0, start_map=0,
             start_x=9, start_y=16, start_party_count=1)
    g.steps_taken = 50
    state = make_state(map_id=0, badges=0)
    ui = make_ui(party_count=1)
    assert g.check_completion(state, ui) is False
    assert g.status == GoalStatus.ACTIVE


def test_completion_multiple_badges():
    """Badge check works when going from 3 to 4."""
    g = Goal(description="Beat Erika", start_badges=3, start_map=6,
             start_x=5, start_y=5, start_party_count=3)
    state = make_state(map_id=6, badges=4)
    ui = make_ui(party_count=3)
    assert g.check_completion(state, ui) is True
    assert "badge gained" in g.reason_done


# ===========================================================================
# Tests: Goal serialization
# ===========================================================================

def test_goal_to_dict():
    g = Goal(description="Test goal", start_map=38, start_x=3, start_y=6,
             start_party_count=0, start_badges=0)
    g.steps_taken = 10
    d = g.to_dict()
    assert d["description"] == "Test goal"
    assert d["status"] == "active"
    assert d["steps_taken"] == 10
    assert d["start_map"] == 38


def test_goal_from_dict():
    d = {
        "description": "Get Pikachu",
        "status": "done",
        "created_at": 1000.0,
        "completed_at": 1050.0,
        "steps_taken": 42,
        "max_steps": 300,
        "reason_done": "party grew",
        "start_map": 0,
        "start_x": 9,
        "start_y": 16,
        "start_party_count": 1,
        "start_badges": 0,
    }
    g = Goal.from_dict(d)
    assert g.description == "Get Pikachu"
    assert g.status == GoalStatus.DONE
    assert g.steps_taken == 42
    assert g.reason_done == "party grew"


def test_goal_roundtrip():
    g = Goal(description="Roundtrip test", start_map=12, start_x=10, start_y=20,
             start_party_count=2, start_badges=1)
    g.steps_taken = 25
    g.complete("done")
    d = g.to_dict()
    g2 = Goal.from_dict(d)
    assert g2.description == g.description
    assert g2.status == g.status
    assert g2.steps_taken == g.steps_taken
    assert g2.reason_done == g.reason_done
    assert g2.start_map == g.start_map


# ===========================================================================
# Tests: GoalManager lifecycle
# ===========================================================================

def test_manager_starts_empty():
    mgr = GoalManager()
    assert mgr.current_goal is None
    assert mgr.needs_new_goal() is True


def test_manager_set_goal():
    mgr = GoalManager()
    state = make_state(map_id=38, x=3, y=6)
    ui = make_ui(party_count=0)
    mgr.set_goal("Leave the bedroom", state, ui)

    assert mgr.current_goal is not None
    assert mgr.current_goal.description == "Leave the bedroom"
    assert mgr.current_goal.start_map == 38
    assert mgr.current_goal.start_party_count == 0
    assert mgr.needs_new_goal() is False


def test_manager_tick_active():
    mgr = GoalManager()
    state = make_state(map_id=38)
    ui = make_ui(party_count=0)
    mgr.set_goal("Leave the bedroom", state, ui)

    # Tick with no state change
    status = mgr.tick(state, ui)
    assert status == GoalStatus.ACTIVE
    assert mgr.needs_new_goal() is False


def test_manager_tick_completes_on_map_change():
    mgr = GoalManager()
    state = make_state(map_id=38)
    ui = make_ui(party_count=0)
    mgr.set_goal("Leave the bedroom", state, ui)

    # Simulate enough steps
    mgr.current_goal.steps_taken = 15

    # Now map changed
    new_state = make_state(map_id=37)
    status = mgr.tick(new_state, ui)
    assert status == GoalStatus.DONE
    assert mgr.needs_new_goal() is True
    assert len(mgr.goal_history) == 1
    assert mgr.goal_history[0]["status"] == "done"


def test_manager_tick_completes_on_party_change():
    mgr = GoalManager()
    state = make_state(map_id=40)
    ui = make_ui(party_count=0)
    mgr.set_goal("Get a starter Pokemon", state, ui)

    # Party count increased
    new_ui = make_ui(party_count=1)
    status = mgr.tick(state, new_ui)
    assert status == GoalStatus.DONE
    assert mgr.needs_new_goal() is True


def test_manager_tick_fails_at_step_limit():
    mgr = GoalManager()
    state = make_state(map_id=0)
    ui = make_ui(party_count=0)
    mgr.set_goal("Explore forever", state, ui)
    mgr.current_goal.max_steps = 5

    for _ in range(4):
        status = mgr.tick(state, ui)
        assert status == GoalStatus.ACTIVE

    status = mgr.tick(state, ui)
    assert status == GoalStatus.FAILED
    assert mgr.needs_new_goal() is True
    assert len(mgr.goal_history) == 1
    assert mgr.goal_history[0]["status"] == "failed"


def test_manager_replace_goal_archives_old():
    mgr = GoalManager()
    state = make_state(map_id=0)
    ui = make_ui(party_count=0)

    mgr.set_goal("First goal", state, ui)
    mgr.set_goal("Second goal", state, ui)

    assert mgr.current_goal.description == "Second goal"
    assert len(mgr.goal_history) == 1
    assert mgr.goal_history[0]["description"] == "First goal"
    assert mgr.goal_history[0]["status"] == "failed"  # replaced = failed


def test_manager_cancel_goal():
    mgr = GoalManager()
    state = make_state(map_id=0)
    ui = make_ui(party_count=0)
    mgr.set_goal("Cancelled goal", state, ui)
    mgr.cancel_goal("user cancelled")

    assert mgr.current_goal is None
    assert mgr.needs_new_goal() is True
    assert len(mgr.goal_history) == 1
    assert mgr.goal_history[0]["reason_done"] == "user cancelled"


def test_manager_no_goal_tick_returns_none():
    mgr = GoalManager()
    state = make_state()
    ui = make_ui()
    assert mgr.tick(state, ui) is None


# ===========================================================================
# Tests: GoalManager prompt lines
# ===========================================================================

def test_prompt_lines_no_goal():
    mgr = GoalManager()
    lines = mgr.get_goal_prompt_lines()
    assert any("NO CURRENT GOAL" in line for line in lines)


def test_prompt_lines_active_goal():
    mgr = GoalManager()
    state = make_state(map_id=38)
    ui = make_ui(party_count=0)
    mgr.set_goal("Go to Oak's Lab", state, ui)
    mgr.current_goal.steps_taken = 15

    lines = mgr.get_goal_prompt_lines()
    assert any("Go to Oak's Lab" in line for line in lines)
    assert any("15 steps" in line for line in lines)


def test_prompt_lines_with_history():
    mgr = GoalManager()
    state = make_state(map_id=38)
    ui = make_ui(party_count=0)

    # Create and complete a goal
    mgr.set_goal("Leave bedroom", state, ui)
    mgr.current_goal.complete("reached new area")
    mgr.goal_history.append({
        "description": "Leave bedroom",
        "status": "done",
        "reason_done": "reached new area",
        "steps_taken": 20,
        "completed_at": time.time(),
    })
    mgr.current_goal = None

    lines = mgr.get_goal_prompt_lines()
    joined = " ".join(lines)
    assert "Leave bedroom" in joined
    assert "done" in joined


# ===========================================================================
# Tests: GoalManager persistence
# ===========================================================================

def test_manager_save_load():
    """Test save and load roundtrip."""
    import tasks.manager as mgr_module
    # Use a temp file
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    original_path = mgr_module.GOALS_STATE_PATH
    mgr_module.GOALS_STATE_PATH = type(original_path)(tmp.name)

    try:
        mgr = GoalManager()
        state = make_state(map_id=12, x=10, y=20)
        ui = make_ui(party_count=1)
        mgr.set_goal("Travel to Viridian City", state, ui)
        mgr.current_goal.steps_taken = 42
        mgr.goal_history.append({
            "description": "Got starter",
            "status": "done",
            "reason_done": "party grew",
            "steps_taken": 30,
            "completed_at": time.time(),
        })
        mgr.save()

        # Load into a new manager
        mgr2 = GoalManager()
        assert mgr2.load() is True
        assert mgr2.current_goal is not None
        assert mgr2.current_goal.description == "Travel to Viridian City"
        assert mgr2.current_goal.steps_taken == 42
        assert mgr2.current_goal.start_map == 12
        assert len(mgr2.goal_history) == 1
        assert mgr2.goal_history[0]["description"] == "Got starter"
    finally:
        mgr_module.GOALS_STATE_PATH = original_path
        os.unlink(tmp.name)


def test_manager_load_missing_file():
    """Loading from a nonexistent file returns False."""
    import tasks.manager as mgr_module
    original_path = mgr_module.GOALS_STATE_PATH
    mgr_module.GOALS_STATE_PATH = type(original_path)("/tmp/nonexistent_goals_test.json")
    try:
        mgr = GoalManager()
        assert mgr.load() is False
        assert mgr.current_goal is None
    finally:
        mgr_module.GOALS_STATE_PATH = original_path


# ===========================================================================
# Tests: LLM response parsing
# ===========================================================================

def test_parse_action_only():
    from llm_client import parse_llm_response
    result = parse_llm_response(
        '{"action":"GO_STAIRS","reason":"need to go downstairs"}',
        ["GO_STAIRS", "INTERACT", "WAIT"]
    )
    assert result["action"] == "GO_STAIRS"
    assert result["reason"] == "need to go downstairs"
    assert result["goal"] is None


def test_parse_action_and_goal():
    from llm_client import parse_llm_response
    result = parse_llm_response(
        '{"goal":"Get a starter Pokemon from Oak","action":"EXIT_HOUSE","reason":"need to leave first"}',
        ["EXIT_HOUSE", "GO_UPSTAIRS", "INTERACT"]
    )
    assert result["action"] == "EXIT_HOUSE"
    assert result["goal"] == "Get a starter Pokemon from Oak"
    assert result["reason"] == "need to leave first"


def test_parse_goal_only_no_action():
    from llm_client import parse_llm_response
    result = parse_llm_response(
        '{"goal":"Explore Pallet Town","reason":"just started"}',
        ["GO_SOUTH", "INTERACT"]
    )
    # No action field → action should be None
    assert result["action"] is None or result["action"] == ""
    assert result["goal"] == "Explore Pallet Town"


def test_parse_fuzzy_match():
    from llm_client import parse_llm_response
    result = parse_llm_response(
        '{"action":"go_stairs","reason":"going down"}',
        ["GO_STAIRS", "INTERACT"]
    )
    assert result["action"] == "GO_STAIRS"


def test_parse_markdown_wrapped():
    from llm_client import parse_llm_response
    response = '```json\n{"action":"INTERACT","reason":"talk to NPC"}\n```'
    result = parse_llm_response(response, ["INTERACT", "WAIT"])
    assert result["action"] == "INTERACT"


def test_parse_action_from_text():
    from llm_client import parse_llm_response
    result = parse_llm_response(
        "I think we should INTERACT with the nearby NPC",
        ["INTERACT", "WAIT", "GO_SOUTH"]
    )
    assert result["action"] == "INTERACT"


def test_parse_empty_response():
    from llm_client import parse_llm_response
    result = parse_llm_response("", ["INTERACT"])
    assert result["action"] is None
    assert result["error"] == "empty response"


def test_parse_garbage_response():
    from llm_client import parse_llm_response
    result = parse_llm_response("asdfghjkl random noise", ["INTERACT", "WAIT"])
    assert result["action"] is None
    assert "could not parse" in result["error"]


def test_parse_unknown_action_returned_raw():
    """When the LLM returns an action not in valid_actions, it should still be returned."""
    from llm_client import parse_llm_response
    result = parse_llm_response(
        '{"action":"EXPLORE_NORTH","reason":"want to see what is up there"}',
        ["GO_SOUTH", "INTERACT"]
    )
    # The raw action should be returned so the agent can try to resolve it
    assert result["action"] == "EXPLORE_NORTH"
    assert result["error"] == ""


def test_parse_short_goal_ignored():
    """Goals shorter than 4 chars should be ignored (likely garbage)."""
    from llm_client import parse_llm_response
    result = parse_llm_response(
        '{"goal":"go","action":"INTERACT","reason":"test"}',
        ["INTERACT"]
    )
    assert result["goal"] is None  # "go" is too short
    assert result["action"] == "INTERACT"


def test_parse_single_quotes_json():
    """Handle JSON with single quotes (common LLM mistake)."""
    from llm_client import parse_llm_response
    result = parse_llm_response(
        "{'action':'GO_STAIRS','reason':'going down'}",
        ["GO_STAIRS", "INTERACT"]
    )
    assert result["action"] == "GO_STAIRS"


# ---------------------------------------------------------------------------
# Run all tests
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import traceback

    test_functions = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    failed = 0

    for test_fn in test_functions:
        try:
            test_fn()
            print(f"  ✓ {test_fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ✗ {test_fn.__name__}: {e}")
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed, {passed + failed} total")
    if failed > 0:
        sys.exit(1)
    else:
        print("All tests passed!")
