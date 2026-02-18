"""
prompt_templates.py — Prompt formatting for the LLM.

Converts a ContextSnapshot into a structured prompt.
Prompt refinement will be done in a later iteration — this module
currently provides minimal formatting to get context to the LLM.
"""

from typing import Dict, List, Optional

from context.context_builder import ContextSnapshot, BattleInfo, PartyMember


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

PLANNER_SYSTEM_PROMPT = (
    "You are an AI playing Pokemon Red. Your objective is to complete the game "
    "and defeat the Elite Four. Pick the best action from the available list. "
    "Reply with ONLY valid JSON."
)


# ---------------------------------------------------------------------------
# Minimal formatting (to be expanded in a later iteration)
# ---------------------------------------------------------------------------

def format_context_prompt(snap: ContextSnapshot) -> str:
    """
    Format a ContextSnapshot into a text prompt for the LLM.
    Minimal formatting — prompt refinement comes later.
    """
    lines: List[str] = []

    lines.append(f"Location: {snap.map_name} ({snap.x},{snap.y}) facing {snap.facing}")
    lines.append(f"Money: {snap.money} | Badges: {snap.badges} | Time: {snap.play_time}")

    if snap.textbox_active:
        lines.append("Textbox is open")
        if snap.screen_text:
            lines.append(f"Text on screen: \"{snap.screen_text}\"")
    elif snap.screen_text:
        lines.append(f"Text on screen: \"{snap.screen_text}\"")
    if snap.menu_active:
        lines.append("Menu is open")
    if snap.input_ignored:
        lines.append("Input currently ignored")

    # Party
    if snap.party_count == 0:
        lines.append("Party: empty")
    else:
        lines.append(f"Party ({snap.party_count}):")
        for m in snap.party:
            lead = "*" if m.is_lead else " "
            moves_str = ", ".join(
                f"{name}({pp}pp)" for name, pp in zip(m.moves, m.pp)
            ) if m.moves else "no moves"
            lines.append(
                f" {lead} {m.species} L{m.level} {m.hp}/{m.max_hp}HP "
                f"[{m.status}] [{moves_str}]"
            )

    # Battle
    if snap.battle:
        b = snap.battle
        lines.append(f"BATTLE: {b.own_species} L{b.own_level} {b.own_hp}/{b.own_max_hp}HP")
        lines.append(f"vs {b.opponent_species} L{b.opponent_level} {b.opponent_hp}/{b.opponent_max_hp}HP [{b.opponent_status}]")
        if b.own_moves:
            moves_str = ", ".join(
                f"{name}({pp}pp)" for name, pp in zip(b.own_moves, b.own_pp)
            )
            lines.append(f"Moves: {moves_str}")

    # Bag (brief)
    if snap.bag:
        item_strs = [f"{it.name}x{it.quantity}" for it in snap.bag[:10]]
        lines.append(f"Bag: {', '.join(item_strs)}")

    # Nearby warps
    if snap.warps:
        warp_strs = [f"({w.x},{w.y})->{w.dest_map_name}" for w in snap.warps[:8]]
        lines.append(f"Warps: {', '.join(warp_strs)}")

    # Nearby sprites
    if snap.sprites:
        lines.append(f"Sprites nearby: {len(snap.sprites)}")

    # Signs
    if snap.signs:
        sign_strs = [f"({s.x},{s.y})" for s in snap.signs[:8]]
        lines.append(f"Signs: {', '.join(sign_strs)}")

    # Recent history
    if snap.recent_history:
        recent = snap.recent_history[-5:]
        lines.append(f"Recent: {' | '.join(recent)}")

    # Available actions
    if snap.available_actions:
        lines.append(f"Actions: {', '.join(snap.available_actions)}")

    lines.append("")
    lines.append('Reply with ONLY: {"action":"ACTION_NAME","reason":"brief why"}')

    return "\n".join(lines)


def format_battle_prompt(snap: ContextSnapshot) -> str:
    """
    Prompt for battle decisions.
    """
    lines: List[str] = []

    if snap.battle:
        b = snap.battle
        lines.append(f"BATTLE: {b.own_species} L{b.own_level} {b.own_hp}/{b.own_max_hp}HP")
        lines.append(f"vs {b.opponent_species} L{b.opponent_level} {b.opponent_hp}/{b.opponent_max_hp}HP [{b.opponent_status}]")
        if b.own_moves:
            moves_str = ", ".join(
                f"{name}({pp}pp)" for name, pp in zip(b.own_moves, b.own_pp)
            )
            lines.append(f"Moves: {moves_str}")

    lines.append("")
    lines.append("Actions: FIGHT, RUN")
    lines.append('Reply with ONLY: {"action":"FIGHT or RUN","reason":"brief why"}')

    return "\n".join(lines)


def format_planner_prompt(snap: ContextSnapshot) -> Dict[str, str]:
    """
    Build system + user prompt pair for the LLM.

    Returns:
        {"system": str, "user": str}
    """
    if snap.battle:
        user_prompt = format_battle_prompt(snap)
    else:
        user_prompt = format_context_prompt(snap)

    return {
        "system": PLANNER_SYSTEM_PROMPT,
        "user": user_prompt,
    }
