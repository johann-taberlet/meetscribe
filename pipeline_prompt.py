"""Load and build system prompts for the meeting intelligence pipeline."""

from pathlib import Path

_DIR = Path(__file__).parent
PIPELINE_PROMPT_FILE = _DIR / "PIPELINE_PROMPT.md"
SEGMENT_PROMPT_FILE = _DIR / "SEGMENT_PROMPT.md"


def load_pipeline_prompt() -> str:
    return PIPELINE_PROMPT_FILE.read_text(encoding="utf-8")


def load_segment_prompt() -> str:
    return SEGMENT_PROMPT_FILE.read_text(encoding="utf-8")


def build_system_prompt(
    *,
    context: str | None = None,
) -> str:
    """Build the full system prompt with optional user-provided context."""
    prompt = load_pipeline_prompt()

    if context:
        prompt += (
            "\n\n## Contexte fourni par l'utilisateur\n\n"
            + context
        )

    return prompt
