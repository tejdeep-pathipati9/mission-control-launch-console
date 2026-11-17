from app.config import Settings
from app.llm.anthropic_client import AnthropicModelClient
from app.llm.client import ModelClient
from app.llm.openai_client import OpenAIModelClient


def create_model_client(settings: Settings) -> ModelClient:
    if settings.model_provider in {"anthropic", "foundry"}:
        return AnthropicModelClient(settings)
    return OpenAIModelClient(settings)
