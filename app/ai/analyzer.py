"""AI-driven analysis using Google Gemini."""
from __future__ import annotations

import json

import google.generativeai as genai

from app.ai.prompts import (
    WEEKLY_INSIGHT_SYSTEM_PROMPT,
    OBJECTION_INSIGHT_SYSTEM_PROMPT,
)
from app.config.settings import settings


genai.configure(api_key=settings.AI_API_KEY)


def _generate(prompt: str, max_tokens: int) -> str:
    model = genai.GenerativeModel(
        model_name=settings.AI_MODEL,
        system_instruction=prompt,
    )

    response = model.generate_content(
        prompt,
        generation_config=genai.types.GenerationConfig(
            max_output_tokens=max_tokens,
        ),
    )

    return response.text.strip()


def generate_weekly_insight(data: dict) -> str:
    """Generate a weekly insight from structured metrics."""
    try:
        user_prompt = json.dumps(data, default=str)
        return _generate(
            f"{WEEKLY_INSIGHT_SYSTEM_PROMPT}\n\nData:\n{user_prompt}",
            500,
        )
    except Exception:
        return (
            "AI insight generation is temporarily unavailable, but your raw "
            "numbers above are accurate and saved."
        )


def answer_objection_question(question: str, objections: list[str]) -> str:
    """Answer a sales-objection analysis question."""
    try:
        payload = {
            "question": question,
            "objections": objections,
        }

        return _generate(
            f"{OBJECTION_INSIGHT_SYSTEM_PROMPT}\n\n"
            f"Question and objections:\n{json.dumps(payload)}",
            300,
        )
    except Exception:
        return "I couldn't generate that analysis right now. Please try again in a moment."
    