"""
llm_client.py — LLM interface for Pokemon Red AI

Communicates with Ollama's local API to get game decisions.
The LLM plays the game — it decides its own goals and picks actions.
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


def build_game_prompt(state: dict, ui: dict, action_history: list[str]) -> dict:
    """
    Build a prompt for the LLM using ContextBuilder and prompt templates.

    Returns:
        {"system": str, "user": str}
    """
    map_id = state.get("map", 0)
    in_battle = ui.get("in_battle", False)

    if in_battle:
        available_actions = ["FIGHT", "RUN"]
    else:
        available_actions = get_available_actions(map_id)

    builder = ContextBuilder()
    if action_history:
        for a in action_history[-5:]:
            builder.record_action(a)

    snapshot = builder.build(state=state, available_actions=available_actions)
    return format_planner_prompt(snapshot)


def query_llm(prompt: str, system: str | None = None,
              model: str | None = None, timeout: float = 30.0) -> str | None:
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
            "num_predict": 80,
            "num_ctx": 2048,
            "top_p": 0.9,
            "repeat_penalty": 1.3,
        },
    }
    if system:
        data["system"] = system

    result = _ollama_request(GENERATE_URL, data, timeout=timeout)
    if result and "response" in result:
        return result["response"].strip()
    return None


def parse_llm_response(response: str, valid_actions: list[str]) -> dict:
    """
    Parse the LLM's response into an action (and optionally a goal).

    Returns a dict with:
      - "action": str | None — the action name
      - "goal": str | None — a new goal the LLM wants to set
      - "reason": str — reasoning
      - "error": str — error message if parsing failed
    """
    result = {"action": None, "goal": None, "reason": "", "error": ""}

    if not response:
        result["error"] = "empty response"
        return result

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
            # Extract goal if present
            goal = obj.get("goal", obj.get("new_goal", None))
            if goal and isinstance(goal, str) and len(goal.strip()) > 3:
                result["goal"] = goal.strip()

            # Extract action
            action = str(obj.get("action", obj.get("command", ""))).strip()
            result["reason"] = str(obj.get("reason", obj.get("reasoning", "")))

            if action:
                action_upper = action.upper().replace(" ", "_").replace("-", "_")

                # Check against valid actions
                if action_upper in [va.upper() for va in valid_actions]:
                    result["action"] = action_upper
                    return result

                # Fuzzy match
                for va in valid_actions:
                    va_upper = va.upper()
                    if action_upper in va_upper or va_upper in action_upper:
                        result["action"] = va_upper
                        return result

                # Return the raw action even if not in valid list
                # (the agent can try to use it)
                result["action"] = action_upper
                return result

            # Got a goal but no action — still a valid response
            if result["goal"]:
                return result

    # No JSON — try to find an action name in the raw text
    upper = text.upper()
    for va in valid_actions:
        if va.upper() in upper:
            result["action"] = va.upper()
            result["reason"] = "parsed from text"
            return result

    result["error"] = f"could not parse: {text[:100]}"
    return result


def get_llm_decision(state: dict, ui: dict, action_history: list[str],
                     timeout: float = 30.0) -> dict:
    """
    Ask the LLM for a decision.

    Returns a dict with:
      - "action": str | None
      - "goal": str | None
      - "reason": str
      - "error": str
    """
    map_id = state.get("map", 0)
    in_battle = ui.get("in_battle", False)

    if in_battle:
        valid_actions = ["FIGHT", "RUN"]
    else:
        valid_actions = get_available_actions(map_id)

    prompt_pair = build_game_prompt(state, ui, action_history)
    response = query_llm(
        prompt=prompt_pair["user"],
        system=prompt_pair["system"],
        timeout=timeout,
    )

    if response is None:
        return {"action": None, "goal": None, "reason": "", "error": "LLM query failed"}

    return parse_llm_response(response, valid_actions)
