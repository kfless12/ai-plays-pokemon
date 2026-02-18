"""
tests/test_context.py — Unit tests for Objective 1: Richer Context

Tests:
  - Context assembly from synthetic state.json data (Lua v3 format)
  - Prompt formatting
  - Species/move/item name resolution
  - Short-term memory buffer
  - New fields: facing, money, badges, full party, bag, sprites, warps, signs
"""

import json
import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from context.context_builder import (
    ContextBuilder,
    ContextSnapshot,
    build_context_snapshot,
    _species_name,
    _move_name,
    _item_name,
    _map_name,
    PartyMember,
    BattleInfo,
    BagItem,
    WarpPoint,
    SignPost,
    NearbySprite,
)
from context.prompt_templates import (
    format_context_prompt,
    format_battle_prompt,
    format_planner_prompt,
    PLANNER_SYSTEM_PROMPT,
)


# ---------------------------------------------------------------------------
# Synthetic state fixtures (Lua v3 format)
# ---------------------------------------------------------------------------

def make_overworld_state(map_id=0, x=9, y=16, party_count=0, facing="down",
                         money=3000, badges=0, party=None, bag=None,
                         sprites=None, warps=None, signs=None):
    """Create a synthetic state.json dict for overworld (no battle)."""
    return {
        "frame": 50000,
        "map": map_id,
        "x": x,
        "y": y,
        "facing": facing,
        "money": money,
        "badges": badges,
        "badge_bits": 0,
        "battleType": 0,
        "ui": {
            "in_battle": False,
            "battle_type": 0,
            "textbox_active": False,
            "textbox_id": 0,
            "text_printing": False,
            "menu_active": False,
            "input_ignored": False,
            "joy_flags": 0,
            "current_menu_item": 0,
            "menu_cursor_x": 0,
            "menu_cursor_y": 0,
            "menu_max_item": 0,
            "battle_menu_selection": "NONE",
            "move_list_index": 0,
            "startup_phase": "PLAYING",
            "party_count": party_count,
            "anim_counter": 1,
        },
        "party": party or [],
        "slot1": {
            "lvl": 0, "hp": 0, "maxhp": 0,
            "moves": [0, 0, 0, 0], "pps": [0, 0, 0, 0],
        },
        "enemy": None,
        "bag": bag or [],
        "sprites": sprites or [],
        "warps": warps or [],
        "signs": signs or [],
        "last_action": "DOWN 16",
    }


def make_battle_state(own_species=176, own_lvl=5, own_hp=20, own_maxhp=20,
                      own_moves=None, own_pps=None, own_status="OK",
                      enemy_species=165, enemy_lvl=3, enemy_hp=12, enemy_maxhp=12,
                      enemy_status="OK"):
    """Create a synthetic state.json dict for a battle (Lua v3 format)."""
    party = [{
        "species": own_species,
        "lvl": own_lvl,
        "hp": own_hp,
        "maxhp": own_maxhp,
        "status": own_status,
        "moves": own_moves or [33, 45, 0, 0],
        "pps": own_pps or [35, 40, 0, 0],
    }]
    return {
        "frame": 60000,
        "map": 12,
        "x": 10,
        "y": 20,
        "facing": "down",
        "money": 3000,
        "badges": 0,
        "badge_bits": 0,
        "battleType": 1,
        "ui": {
            "in_battle": True,
            "battle_type": 1,
            "textbox_active": False,
            "textbox_id": 0,
            "text_printing": False,
            "menu_active": True,
            "input_ignored": False,
            "joy_flags": 0,
            "current_menu_item": 0,
            "menu_cursor_x": 9,
            "menu_cursor_y": 0,
            "menu_max_item": 3,
            "battle_menu_selection": "FIGHT",
            "move_list_index": 0,
            "startup_phase": "PLAYING",
            "party_count": 1,
            "anim_counter": 1,
        },
        "party": party,
        "slot1": {
            "lvl": own_lvl, "hp": own_hp, "maxhp": own_maxhp,
            "moves": own_moves or [33, 45, 0, 0],
            "pps": own_pps or [35, 40, 0, 0],
        },
        "enemy": {
            "species": enemy_species,
            "lvl": enemy_lvl,
            "hp": enemy_hp,
            "maxhp": enemy_maxhp,
            "status": enemy_status,
        },
        "bag": [],
        "sprites": [],
        "warps": [],
        "signs": [],
        "last_action": "A",
    }


# ---------------------------------------------------------------------------
# Tests: Lookup tables
# ---------------------------------------------------------------------------

def test_species_name_known():
    assert _species_name(153) == "Bulbasaur"
    assert _species_name(176) == "Charmander"
    assert _species_name(177) == "Squirtle"
    assert _species_name(84) == "Pikachu"

def test_species_name_unknown():
    result = _species_name(999)
    assert "999" in result

def test_species_name_zero():
    assert _species_name(0) == "(none)"

def test_move_name_known():
    assert _move_name(33) == "Tackle"
    assert _move_name(45) == "Growl"
    assert _move_name(85) == "Thunderbolt"

def test_move_name_zero():
    assert _move_name(0) == ""

def test_move_name_unknown():
    result = _move_name(999)
    assert "999" in result

def test_item_name_known():
    assert _item_name(4) == "Poke Ball"
    assert _item_name(20) == "Potion"
    assert _item_name(1) == "Master Ball"

def test_item_name_zero():
    assert _item_name(0) == ""

def test_item_name_unknown():
    result = _item_name(999)
    assert "999" in result

def test_map_name_known():
    assert _map_name(0) == "Pallet Town"
    assert _map_name(38) == "Player's House 2F"
    assert _map_name(40) == "Oak's Lab"

def test_map_name_unknown():
    result = _map_name(999)
    assert "999" in result


# ---------------------------------------------------------------------------
# Tests: ContextBuilder — overworld
# ---------------------------------------------------------------------------

def test_build_overworld_no_party():
    state = make_overworld_state(map_id=0, x=9, y=16, party_count=0)
    builder = ContextBuilder()
    snap = builder.build(state=state, available_actions=["GO_OAKS_LAB", "GO_SOUTH"])

    assert snap.map_name == "Pallet Town"
    assert snap.map_id == 0
    assert snap.x == 9
    assert snap.y == 16
    assert snap.facing == "down"
    assert snap.money == 3000
    assert snap.badges == 0
    assert snap.party_count == 0
    assert snap.party == []
    assert snap.battle is None
    assert snap.phase == "PLAYING"
    assert "GO_OAKS_LAB" in snap.available_actions

def test_build_overworld_with_party():
    party = [{
        "species": 176,  # Charmander
        "lvl": 5,
        "hp": 20,
        "maxhp": 20,
        "status": "OK",
        "moves": [33, 45, 0, 0],
        "pps": [35, 40, 0, 0],
    }]
    state = make_overworld_state(
        map_id=12, x=10, y=15, party_count=1, party=party,
        money=5000, badges=1,
    )
    builder = ContextBuilder()
    snap = builder.build(state=state)

    assert snap.party_count == 1
    assert snap.money == 5000
    assert snap.badges == 1
    assert len(snap.party) == 1
    member = snap.party[0]
    assert member.species == "Charmander"
    assert member.level == 5
    assert member.hp == 20
    assert member.max_hp == 20
    assert member.status == "OK"
    assert member.is_lead is True
    assert "Tackle" in member.moves
    assert "Growl" in member.moves

def test_build_overworld_with_bag():
    bag = [{"id": 4, "qty": 5}, {"id": 20, "qty": 3}]
    state = make_overworld_state(map_id=0, party_count=0, bag=bag)
    builder = ContextBuilder()
    snap = builder.build(state=state)

    assert len(snap.bag) == 2
    assert snap.bag[0].name == "Poke Ball"
    assert snap.bag[0].quantity == 5
    assert snap.bag[1].name == "Potion"
    assert snap.bag[1].quantity == 3

def test_build_overworld_with_warps():
    warps = [
        {"y": 7, "x": 2, "dest_map": 0},
        {"y": 1, "x": 7, "dest_map": 38},
    ]
    state = make_overworld_state(map_id=37, party_count=0, warps=warps)
    builder = ContextBuilder()
    snap = builder.build(state=state)

    assert len(snap.warps) == 2
    assert snap.warps[0].dest_map_name == "Pallet Town"
    assert snap.warps[1].dest_map_name == "Player's House 2F"

def test_build_overworld_with_signs():
    signs = [{"y": 10, "x": 5, "text_id": 3}]
    state = make_overworld_state(map_id=0, party_count=0, signs=signs)
    builder = ContextBuilder()
    snap = builder.build(state=state)

    assert len(snap.signs) == 1
    assert snap.signs[0].x == 5
    assert snap.signs[0].y == 10

def test_build_overworld_with_sprites():
    sprites = [
        {"sprite_id": 1, "picture_id": 5, "screen_y": 80, "screen_x": 64, "facing": "up"},
    ]
    state = make_overworld_state(map_id=0, party_count=0, sprites=sprites)
    builder = ContextBuilder()
    snap = builder.build(state=state)

    assert len(snap.sprites) == 1
    assert snap.sprites[0].picture_id == 5
    assert snap.sprites[0].facing == "up"

def test_build_overworld_facing():
    state = make_overworld_state(map_id=0, facing="left")
    builder = ContextBuilder()
    snap = builder.build(state=state)
    assert snap.facing == "left"

def test_build_overworld_play_time():
    state = make_overworld_state(map_id=0)
    state["play_time"] = "2:15:30"
    builder = ContextBuilder()
    snap = builder.build(state=state)
    assert snap.play_time == "2:15:30"


# ---------------------------------------------------------------------------
# Tests: ContextBuilder — battle
# ---------------------------------------------------------------------------

def test_build_battle():
    state = make_battle_state(
        own_species=176, own_lvl=5, own_hp=20, own_maxhp=20,
        own_moves=[33, 45, 0, 0], own_pps=[35, 40, 0, 0],
        enemy_species=165, enemy_lvl=3, enemy_hp=12, enemy_maxhp=12,
    )
    builder = ContextBuilder()
    snap = builder.build(state=state, available_actions=["FIGHT", "RUN"])

    assert snap.battle is not None
    b = snap.battle
    assert b.opponent_species == "Rattata"
    assert b.opponent_level == 3
    assert b.opponent_hp == 12
    assert b.opponent_max_hp == 12
    assert b.own_species == "Charmander"
    assert b.own_level == 5
    assert b.own_hp == 20
    assert b.own_max_hp == 20
    assert "Tackle" in b.own_moves
    assert "Growl" in b.own_moves

def test_battle_has_exact_hp():
    """Verify battle info includes exact HP values (no redaction)."""
    state = make_battle_state(enemy_hp=7, enemy_maxhp=12)
    builder = ContextBuilder()
    snap = builder.build(state=state)

    b = snap.battle
    assert b.opponent_hp == 7
    assert b.opponent_max_hp == 12

def test_battle_status():
    state = make_battle_state(enemy_status="PSN", own_status="PAR")
    builder = ContextBuilder()
    snap = builder.build(state=state)

    b = snap.battle
    assert b.opponent_status == "PSN"


# ---------------------------------------------------------------------------
# Tests: Full party (multiple slots)
# ---------------------------------------------------------------------------

def test_build_full_party():
    party = [
        {"species": 176, "lvl": 10, "hp": 30, "maxhp": 30, "status": "OK",
         "moves": [33, 52, 45, 0], "pps": [35, 25, 40, 0]},
        {"species": 36, "lvl": 8, "hp": 22, "maxhp": 25, "status": "OK",
         "moves": [33, 16, 28, 0], "pps": [35, 35, 15, 0]},
    ]
    state = make_overworld_state(map_id=12, party_count=2, party=party)
    builder = ContextBuilder()
    snap = builder.build(state=state)

    assert snap.party_count == 2
    assert len(snap.party) == 2
    assert snap.party[0].species == "Charmander"
    assert snap.party[0].is_lead is True
    assert snap.party[1].species == "Pidgey"
    assert snap.party[1].is_lead is False
    assert snap.party[1].hp == 22
    assert snap.party[1].max_hp == 25


# ---------------------------------------------------------------------------
# Tests: Short-term memory buffer
# ---------------------------------------------------------------------------

def test_history_recording():
    builder = ContextBuilder()
    builder.record_action("DOWN 16", "moved")
    builder.record_action("A", "textbox")
    builder.record_action("RIGHT 16")

    history = builder.get_recent_history(10)
    assert len(history) == 3
    assert "DOWN 16 → moved" in history[0]
    assert "A → textbox" in history[1]
    assert history[2] == "RIGHT 16"

def test_history_bounded():
    builder = ContextBuilder()
    for i in range(30):
        builder.record_action(f"action_{i}")
    history = builder.get_recent_history(10)
    assert len(history) == 10
    assert history[-1] == "action_29"

def test_history_in_snapshot():
    state = make_overworld_state(map_id=0, party_count=0)
    builder = ContextBuilder()
    builder.record_action("DOWN 16", "moved south")
    builder.record_action("A", "talked to NPC")
    snap = builder.build(state=state)
    assert len(snap.recent_history) == 2


# ---------------------------------------------------------------------------
# Tests: Prompt formatting
# ---------------------------------------------------------------------------

def test_format_context_prompt_overworld():
    snap = ContextSnapshot(
        map_name="Pallet Town", map_id=0, x=9, y=16, facing="down",
        money=3000, badges=0,
        phase="PLAYING", party_count=0, party=[],
        available_actions=["GO_OAKS_LAB", "GO_SOUTH", "INTERACT", "WAIT"],
    )
    prompt = format_context_prompt(snap)

    assert "Pallet Town" in prompt
    assert "GO_OAKS_LAB" in prompt
    assert "GO_SOUTH" in prompt
    assert '{"action"' in prompt
    assert "Party: empty" in prompt
    assert "3000" in prompt

def test_format_battle_prompt():
    snap = ContextSnapshot(
        map_name="Route 1", map_id=12, x=10, y=20, facing="down",
        phase="PLAYING", party_count=1,
        battle=BattleInfo(
            opponent_species="Rattata", opponent_level=3,
            opponent_hp=12, opponent_max_hp=12, opponent_status="OK",
            own_species="Charmander", own_level=5,
            own_hp=20, own_max_hp=20,
            own_moves=["Tackle", "Growl"], own_pp=[35, 40],
            battle_menu="FIGHT",
        ),
        available_actions=["FIGHT", "RUN"],
    )
    prompt = format_battle_prompt(snap)

    assert "Rattata" in prompt
    assert "Tackle" in prompt
    assert "FIGHT" in prompt
    assert "RUN" in prompt
    assert "12/12" in prompt  # exact HP shown

def test_format_planner_prompt_returns_system_and_user():
    snap = ContextSnapshot(
        map_name="Pallet Town", map_id=0, x=9, y=16,
        phase="PLAYING", party_count=0,
        available_actions=["GO_OAKS_LAB"],
    )
    result = format_planner_prompt(snap)
    assert "system" in result
    assert "user" in result
    assert len(result["system"]) > 0
    assert len(result["user"]) > 0

def test_system_prompt_has_elite_four():
    assert "Elite Four" in PLANNER_SYSTEM_PROMPT

def test_system_prompt_no_human_sees():
    """Verify 'human player sees' language was removed."""
    assert "human player sees" not in PLANNER_SYSTEM_PROMPT

def test_prompt_no_hints():
    """Verify no hardcoded goal hints appear in the prompt."""
    snap = ContextSnapshot(
        map_name="Pallet Town", map_id=0, x=9, y=16,
        phase="PLAYING", party_count=0, party=[],
        available_actions=["GO_OAKS_LAB"],
    )
    prompt = format_context_prompt(snap)
    assert "GOAL:" not in prompt
    assert "Go downstairs" not in prompt
    assert "Exit the house" not in prompt


# ---------------------------------------------------------------------------
# Tests: JSON serialization
# ---------------------------------------------------------------------------

def test_snapshot_to_json():
    state = make_overworld_state(map_id=0, party_count=0, money=5000, badges=2)
    builder = ContextBuilder()
    snap = builder.build(state=state)
    json_str = snap.to_json()
    parsed = json.loads(json_str)
    assert parsed["map_name"] == "Pallet Town"
    assert parsed["money"] == 5000
    assert parsed["badges"] == 2

def test_snapshot_to_dict():
    state = make_battle_state()
    builder = ContextBuilder()
    snap = builder.build(state=state)
    d = snap.to_dict()
    assert isinstance(d, dict)
    assert d["battle"] is not None
    assert d["battle"]["opponent_species"] == "Rattata"


# ---------------------------------------------------------------------------
# Tests: Convenience function
# ---------------------------------------------------------------------------

def test_build_context_snapshot_convenience():
    state = make_overworld_state(map_id=38, x=3, y=5, party_count=0)
    snap = build_context_snapshot(
        state=state,
        available_actions=["GO_STAIRS"],
        action_history=["DOWN 16", "A"],
    )
    assert snap.map_name == "Player's House 2F"
    assert len(snap.recent_history) == 2


# ---------------------------------------------------------------------------
# Tests: Backward compat (Lua v2 state without party array)
# ---------------------------------------------------------------------------

def test_build_fallback_slot1():
    """When party array is missing, fall back to slot1."""
    state = {
        "frame": 50000,
        "map": 12,
        "x": 10,
        "y": 15,
        "battleType": 0,
        "ui": {
            "in_battle": False, "battle_type": 0,
            "textbox_active": False, "textbox_id": 0,
            "text_printing": False, "menu_active": False,
            "input_ignored": False, "joy_flags": 0,
            "current_menu_item": 0, "menu_cursor_x": 0,
            "menu_cursor_y": 0, "menu_max_item": 0,
            "battle_menu_selection": "NONE", "move_list_index": 0,
            "startup_phase": "PLAYING", "party_count": 1,
            "anim_counter": 1,
        },
        "slot1": {
            "lvl": 5, "hp": 20, "maxhp": 20,
            "moves": [33, 45, 0, 0], "pps": [35, 40, 0, 0],
        },
        "enemy": None,
        "last_action": "DOWN 16",
    }
    builder = ContextBuilder()
    snap = builder.build(state=state)

    assert snap.party_count == 1
    assert len(snap.party) == 1
    assert snap.party[0].species == "Lead Pokemon"  # no species ID in slot1
    assert snap.party[0].level == 5
    assert "Tackle" in snap.party[0].moves


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
