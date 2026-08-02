from langchain_anthropic import ChatAnthropic

from research_brain.config import ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, MODEL_NAME


def get_llm(temperature: float = 0.0) -> ChatAnthropic:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and fill in your "
            "Pair Foundry credentials."
        )
    kwargs = {
        "model": MODEL_NAME,
        "api_key": ANTHROPIC_API_KEY,
        "temperature": temperature,
    }
    if ANTHROPIC_BASE_URL:
        kwargs["base_url"] = ANTHROPIC_BASE_URL
    return ChatAnthropic(**kwargs)
