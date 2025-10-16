from openai import OpenAI
from typing import Optional
from app.classes.slot import Slot
import runtime_settings as rt

oai = OpenAI(api_key=rt.OPENAI_API_KEY)


def extract_slot_from_text(slot: Slot, text: str) -> Optional[str]:
    """
    Uses the LLM to extract the value for a specific slot from the user's text.

    Args:
        slot: The Slot object (with .name and .description)
        text: The raw user utterance

    Returns:
        The extracted string value, or None if not found.
    """
    if not text.strip():
        return None

    system_prompt = (
        f"You are a precise information extractor. "
        f"Given a caller's message, extract {slot.description}. "
        f"If it is not present, respond with the single word 'None'. "
        f"Return ONLY the extracted {slot.name} string — no explanations or punctuation."
    )

    try:
        completion = oai.chat.completions.create(
            model=rt.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            temperature=0,
            max_tokens=25,
        )

        result = completion.choices[0].message.content.strip()
        if not result or result.lower() == "none":
            return None

        return result

    except Exception as e:
        print(f"[LLM SLOT EXTRACT ERROR] {slot.name} → {e}")
        return None
