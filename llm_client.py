"""
llm_client.py — LLM interface for Pokemon Red AI

Communicates with Ollama's local API to get high-level game decisions.
The LLM picks from predefined actions (GO_STAIRS, EXIT_HOUSE, etc.)
and the navigator handles the actual pathfinding.

Refactored for Objective 1: uses ContextBuilder + prompt_templates for
richer, structured context instead of inline prompt building.
"""

import json
import time
import urllib.request
import urllib.error
from typing import Optional

from navigator import get_available_actions, describe_location
from context.context_builder import (
    ContextBuilder, ContextSnapshot,
    _map_name, _move_name, _species_name,
    PartyMember, BattleInfo,
)
from context.prompt_templates import (
    format_planner_prompt, format_context_prompt, format_battle_prompt,
    PLANNER_SYSTEM_PROMPT,
)

OLLAMA_URL = "http://localhost:11434"
GENERATE_URL = f"{OLLAMA_URL}/api/generate"

MODEL_PREFERENCE = [
    "qwen2.5:3b",
    "qwen2.5:1.5b",
    "qwen2.5:0.5b",
    "tinyllama",
    "smollm2:1.7b",
    "phi3:mini",
]

_active_model: Optional[str] = None


def _ollama_request(url: str, data: dict, timeout: float = 30.0) -> dict | None:
    try:
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _ollama_get(url: str, timeout: float = 5.0) -> dict | None:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def check_ollama_running() -> bool:
    try:
        req = urllib.request.Request(OLLAMA_URL, method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def detect_model() -> str | None:
    global _active_model
    if _active_model:
        return _active_model

    result = _ollama_get(f"{OLLAMA_URL}/api/tags")
    if not result or "models" not in result:
        return None

    installed = {m["name"] for m in result["models"]}
    installed_base = set()
    for name in installed:
        installed_base.add(name)
        installed_base.add(name.split(":")[0])

    for model in MODEL_PREFERENCE:
        if model in installed or model in installed_base:
            _active_model = model
            return model

    if installed:
        _active_model = next(iter(installed))
        return _active_model
    return None


def build_game_prompt(state: dict, ui: dict, action_history: list[str]) -> str:
    """
    Build a prompt for high-level decision making using the
    ContextBuilder and prompt templates.
    """
    map_id = state.get("map", 0)
    in_battle = ui.get("in_battle", False)

    # Determine available actions
    if in_battle:
        available_actions = ["FIGHT", "RUN"]
    else:
        available_actions = get_available_actions(map_id)

    # Build context snapshot directly from the state dict
    builder = ContextBuilder()
    # Seed history from the action_history list passed in
    if action_history:
        for a in action_history[-5:]:
            builder.record_action(a)

    snapshot = builder.build(state=state, available_actions=available_actions)

    # Format using prompt templates
    if in_battle:
        return format_battle_prompt(snapshot)
    else:
        return format_context_prompt(snapshot)


def query_llm(prompt: str, model: str | None = None, timeout: float = 30.0) -> str | None:
    if model is None:
        model = detect_model()
    if model is None:
        return None

    data = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.8,
            "num_predict": 30,
            "num_ctx": 512,
            "top_p": 0.9,
            "repeat_penalty": 1.3,
        },
    }

    result = _ollama_request(GENERATE_URL, data, timeout=timeout)
    if result and "response" in result:
        return result["response"].strip()
    return None


def parse_llm_response(response: str, valid_actions: list[str]) -> tuple[str | None, str]:
    """
    Parse the LLM's response into a high-level action name.
    Returns (action_name, reasoning) or (None, error_msg).
    """
    if not response:
        return None, "empty response"

    text = response.strip()

    # Remove markdown fences
    if text.startswith("```"):
        lines = text.split("\n")
        json_lines = []
        in_block = False
        for line in lines:
            if line.strip().startswith("```"):
                in_block = not in_block
                continue
            if in_block:
                json_lines.append(line)
        text = "\n".join(json_lines).strip()

    # Try to find JSON
    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end > start:
        json_str = text[start:end + 1]
        try:
            obj = json.loads(json_str)
        except json.JSONDecodeError:
            json_str = json_str.replace("'", '"')
            try:
                obj = json.loads(json_str)
            except json.JSONDecodeError:
                obj = {}

        if obj:
            action = str(obj.get("action", obj.get("command", ""))).upper().strip()
            reason = str(obj.get("reason", obj.get("reasoning", "")))

            # Check if it's a valid high-level action
            if action in valid_actions:
                return action, reason

            # Try fuzzy matching (LLM might say "go_stairs" or "GO STAIRS")
            action_clean = action.replace(" ", "_").replace("-", "_")
            for va in valid_actions:
                if action_clean == va or action_clean in va or va in action_clean:
                    return va, reason

            return None, f"invalid action '{action}', valid: {valid_actions}"

    # No JSON — try to find an action name in the raw text
    upper = text.upper()
    for va in valid_actions:
        if va in upper:
            return va, "parsed from text"

    return None, f"could not parse: {text[:100]}"


def get_llm_decision(state: dict, ui: dict, action_history: list[str],
                     timeout: float = 30.0) -> tuple[str | None, str]:
    """
    Ask the LLM for a high-level action decision.
    Returns (action_name, reasoning) or (None, error_msg).
    """
    map_id = state.get("map", 0)
    in_battle = ui.get("in_battle", False)

    if in_battle:
        valid_actions = ["FIGHT", "RUN"]
    else:
        valid_actions = get_available_actions(map_id)

    prompt = build_game_prompt(state, ui, action_history)
    response = query_llm(prompt, timeout=timeout)

    if response is None:
        return None, "LLM query failed"

    return parse_llm_response(response, valid_actions)
