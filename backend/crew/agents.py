import os
from crewai import Agent, LLM


def get_llm() -> LLM:
    model = os.getenv("MODEL", "gpt-4o")
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    return LLM(model=model, api_key=api_key)


def _agent(role: str, goal: str, backstory: str, llm: LLM, max_iter: int = 3) -> Agent:
    return Agent(
        role=role,
        goal=goal,
        backstory=backstory,
        verbose=True,
        max_iter=max_iter,
        max_execution_time=30,
        allow_delegation=False,
        llm=llm,
    )


def create_guard_agent(llm: LLM) -> Agent:
    return _agent(
        role="Inventory Item Validator",
        goal="Determine whether items in a kitchen worker's message are legitimate restaurant inventory items, or ambiguous/non-food items requiring clarification",
        backstory=(
            "You are a strict food safety and inventory validator for restaurants and hotels. "
            "Your expertise is identifying whether items mentioned by kitchen workers belong in a food "
            "inventory system, flagging ambiguous product names, and catching clearly non-food items "
            "before they pollute the inventory database."
        ),
        llm=llm,
        max_iter=2,
    )


def create_aria_agent(llm: LLM) -> Agent:
    return _agent(
        role="ARIA - Automated Restaurant Inventory Assistant",
        goal="Process kitchen worker voice commands and manage restaurant food inventory through intelligent, context-aware conversation",
        backstory=(
            "You are ARIA (Automated Restaurant Inventory Assistant), an expert AI inventory supervisor "
            "for hotels and restaurants. You help workers manage food, beverage, and kitchen supply "
            "inventory through natural voice conversation. You are professional, precise, and ALWAYS "
            "confirm before making changes. You remember context across messages, detect unit mismatches, "
            "flag suspicious quantities, and auto-categorize items based on your deep food industry knowledge."
        ),
        llm=llm,
    )
