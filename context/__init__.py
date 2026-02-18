"""
context — Richer context assembly for the Pokemon Red AI.

Provides ContextBuilder (state.json → ContextSnapshot) and prompt templates
for LLM prompts.
"""

from context.context_builder import ContextBuilder, ContextSnapshot, build_context_snapshot
from context.prompt_templates import format_planner_prompt, PLANNER_SYSTEM_PROMPT
