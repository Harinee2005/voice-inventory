import json
import os
import re

# Disable CrewAI's OpenTelemetry trace prompt that blocks for 20s after each run
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

from crewai import Crew, Process
from .agents import get_llm, create_guard_agent, create_aria_agent
from .tasks import make_guard_task, make_aria_task
from .schemas import GuardResult, ARIAResult


def _extract(result, model_cls):
    """Pull a Pydantic model from a CrewAI result, handling all output shapes."""
    if hasattr(result, "pydantic") and result.pydantic is not None:
        return result.pydantic
    if hasattr(result, "json_dict") and result.json_dict:
        return model_cls(**result.json_dict)
    raw = result.raw if hasattr(result, "raw") else str(result)
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw)
    return model_cls(**json.loads(raw))


def _run_single(task) -> object:
    """Run a single-agent, single-task crew sequentially and return the result."""
    crew = Crew(
        agents=[task.agent],
        tasks=[task],
        process=Process.sequential,
        verbose=True,
    )
    return crew.kickoff()


def guard_validate(text: str) -> dict:
    """
    Guard validation via CrewAI — checks whether items are valid restaurant inventory.
    Returns a dict matching the legacy _guard_validate_items output format.
    """
    llm = get_llm()
    agent = create_guard_agent(llm)
    task = make_guard_task(text, agent)
    try:
        result = _run_single(task)
        guard: GuardResult = _extract(result, GuardResult)
        return guard.model_dump()
    except Exception as e:
        print(f"[Guard Crew] Failed: {e} — passing through to ARIA")
        return {"has_items": False, "all_valid": True, "items": [], "guard_message": ""}


def aria_process(
    text: str,
    inventory_context: str,
    item_history_context: str,
    conversation_history: str,
    workspace_context: str,
    pending_action_context: str,
    worker_id: str,
    today: str,
    user_profile_context: str = "",
    fuzzy_units_hint: str = "",
) -> dict:
    """
    Main ARIA processing via CrewAI.
    Returns a dict with message, action, intent, data, user_emotion, new_lexicons, personality_note.
    """
    llm = get_llm()
    agent = create_aria_agent(llm)
    task = make_aria_task(
        text=text,
        inventory_context=inventory_context,
        item_history_context=item_history_context,
        conversation_history_json=conversation_history,
        workspace_context=workspace_context,
        pending_action_context=pending_action_context,
        worker_id=worker_id,
        today=today,
        agent=agent,
        user_profile_context=user_profile_context,
        fuzzy_units_hint=fuzzy_units_hint,
    )
    result = _run_single(task)
    aria: ARIAResult = _extract(result, ARIAResult)
    return aria.model_dump()
