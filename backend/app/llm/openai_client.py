import asyncio
import json

from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, AsyncOpenAI
from openai.types.responses import Response
from pydantic import ValidationError

from app.config import Settings
from app.llm.client import (
    ModelClientError,
    ModelConfigurationError,
    ModelRequest,
    ModelResponse,
    TokenUsage,
)


def _token_count(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _read_usage(response: Response) -> TokenUsage | None:
    usage = response.usage
    if usage is None:
        return None
    return TokenUsage(
        input_tokens=_token_count(getattr(usage, "input_tokens", None)),
        output_tokens=_token_count(getattr(usage, "output_tokens", None)),
        total_tokens=_token_count(getattr(usage, "total_tokens", None)),
        cached_input_tokens=_token_count(
            getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", None)
        ),
        reasoning_output_tokens=_token_count(
            getattr(getattr(usage, "output_tokens_details", None), "reasoning_tokens", None)
        ),
    )


def _read_response(response: Response) -> ModelResponse:
    refusal = next(
        (
            part.refusal
            for item in response.output
            if item.type == "message"
            for part in item.content
            if part.type == "refusal"
        ),
        None,
    )
    text = response.output_text or None
    reason = None
    if refusal is not None:
        status = "refused"
        reason = "model_refusal"
        text = None
    elif response.status != "completed" or text is None:
        status = "incomplete"
        reason = getattr(response.incomplete_details, "reason", None) or "incomplete_response"
    else:
        status = "completed"
    return ModelResponse(
        provider="openai",
        model=response.model,
        status=status,
        text=text,
        reason=reason,
        usage=_read_usage(response),
        request_id=getattr(response, "_request_id", None),
    )


class OpenAIModelClient:
    def __init__(self, settings: Settings, *, sdk_client: AsyncOpenAI | None = None):
        if not settings.openai_api_key or not settings.openai_api_key.strip():
            raise ModelConfigurationError("OPENAI_API_KEY is required for the live model client")
        if not settings.openai_model or not settings.openai_model.strip():
            raise ModelConfigurationError("OPENAI_MODEL is required for the live model client")
        self._model_name = settings.openai_model.strip()
        self._timeout_seconds = settings.model_timeout_seconds
        self._max_output_tokens = settings.model_max_output_tokens
        self._client = (
            sdk_client
            or AsyncOpenAI(
                api_key=settings.openai_api_key,
                base_url="https://api.openai.com/v1",
            )
        ).with_options(timeout=self._timeout_seconds, max_retries=0)

    @property
    def model_name(self) -> str:
        return self._model_name

    async def generate(self, request: ModelRequest) -> ModelResponse:
        try:
            # Bound the entire operation, including transport, rather than individual reads only.
            async with asyncio.timeout(self._timeout_seconds):
                response = await self._client.responses.create(
                    model=self.model_name,
                    instructions=request.instructions,
                    input=[
                        {"role": "user", "content": json.dumps(request.payload, allow_nan=False)}
                    ],
                    text={
                        "format": {
                            "type": "json_schema",
                            "name": request.operation,
                            "schema": request.response_schema,
                            "strict": True,
                        }
                    },
                    max_output_tokens=self._max_output_tokens,
                    store=False,
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
            # Keep raw JSON text and usage together. Services validate their own output schemas.
            return _read_response(response)
        except (AttributeError, TypeError, ValidationError):
            raise ModelClientError(
                "invalid_response", request_id=getattr(response, "_request_id", None)
            ) from None

    async def aclose(self) -> None:
        await self._client.close()
