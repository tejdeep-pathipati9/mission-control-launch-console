import asyncio
import json

from anthropic import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AsyncAnthropic,
    transform_schema,
)
from pydantic import ValidationError

from app.config import Settings
from app.llm.client import (
    ModelClientError,
    ModelConfigurationError,
    ModelRequest,
    ModelResponse,
    TokenUsage,
)


def _count(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


class AnthropicModelClient:
    def __init__(self, settings: Settings, *, sdk_client: AsyncAnthropic | None = None):
        self._provider = "foundry" if settings.model_provider == "foundry" else "anthropic"
        if self._provider == "foundry":
            key = settings.anthropic_foundry_api_key
            if not key or not key.strip():
                raise ModelConfigurationError("ANTHROPIC_FOUNDRY_API_KEY is required")
            resource = settings.anthropic_foundry_resource
            base_url = settings.anthropic_foundry_base_url
            if bool(resource) == bool(base_url):
                raise ModelConfigurationError(
                    "Set exactly one of ANTHROPIC_FOUNDRY_RESOURCE or ANTHROPIC_FOUNDRY_BASE_URL"
                )
        elif not settings.anthropic_api_key or not settings.anthropic_api_key.strip():
            raise ModelConfigurationError("ANTHROPIC_API_KEY is required for the live model client")
        if not settings.anthropic_model or not settings.anthropic_model.strip():
            raise ModelConfigurationError("ANTHROPIC_MODEL is required for the live model client")
        self.model_name = settings.anthropic_model.strip()
        self._timeout = settings.model_timeout_seconds
        self._max_tokens = settings.model_max_output_tokens
        if sdk_client is None:
            if self._provider == "foundry":
                # Foundry accepts the SDK's x-api-key header. Resolve the endpoint from
                # normalized settings rather than letting SDK environment defaults conflict.
                sdk_client = AsyncAnthropic(
                    api_key=key,
                    base_url=base_url or f"https://{resource}.services.ai.azure.com/anthropic/",
                    timeout=self._timeout,
                    max_retries=0,
                )
            else:
                sdk_client = AsyncAnthropic(
                    api_key=settings.anthropic_api_key,
                    base_url="https://api.anthropic.com",
                    timeout=self._timeout,
                    max_retries=0,
                )
        self._client = sdk_client

    async def generate(self, request: ModelRequest) -> ModelResponse:
        try:
            async with asyncio.timeout(self._timeout):
                message = await self._client.messages.create(
                    model=self.model_name,
                    max_tokens=self._max_tokens,
                    system=request.instructions,
                    messages=[
                        {"role": "user", "content": json.dumps(request.payload, allow_nan=False)}
                    ],
                    thinking={"type": "disabled"},
                    output_config={
                        "format": {
                            "type": "json_schema",
                            "schema": transform_schema(request.response_schema),
                        }
                    },
                )
        except (TimeoutError, APITimeoutError):
            raise ModelClientError("timeout") from None
        except APIConnectionError:
            raise ModelClientError("connection_error") from None
        except APIStatusError as exc:
            reason = {
                401: "authentication_error",
                403: "permission_denied",
                429: "rate_limited",
            }.get(exc.status_code, "provider_error")
            raise ModelClientError(
                reason, status_code=exc.status_code, request_id=exc.request_id
            ) from None
        except APIError:
            raise ModelClientError("provider_error") from None
        try:
            text = "".join(block.text for block in message.content if block.type == "text") or None
            stop = message.stop_reason
            refused = stop == "refusal"
            completed = stop in {"end_turn", "stop_sequence"} and text is not None
            usage = message.usage
            cache = getattr(usage, "cache_creation", None)
            result_usage = (
                TokenUsage(
                    input_tokens=_count(getattr(usage, "input_tokens", None)),
                    output_tokens=_count(getattr(usage, "output_tokens", None)),
                    cached_input_tokens=_count(getattr(usage, "cache_read_input_tokens", None)),
                    cache_write_input_tokens=_count(
                        getattr(usage, "cache_creation_input_tokens", None)
                    ),
                    cache_write_5m_input_tokens=_count(
                        getattr(cache, "ephemeral_5m_input_tokens", None)
                    ),
                    cache_write_1h_input_tokens=_count(
                        getattr(cache, "ephemeral_1h_input_tokens", None)
                    ),
                )
                if usage is not None
                else None
            )
            return ModelResponse(
                provider=self._provider,
                model=message.model,
                status="refused" if refused else ("completed" if completed else "incomplete"),
                text=None if refused else text,
                usage=result_usage,
                request_id=getattr(message, "_request_id", None),
                reason="model_refusal"
                if refused
                else (None if completed else stop or "empty_output"),
            )
        except (AttributeError, TypeError, ValidationError):
            raise ModelClientError(
                "invalid_response", request_id=getattr(message, "_request_id", None)
            ) from None

    async def aclose(self) -> None:
        await self._client.close()
