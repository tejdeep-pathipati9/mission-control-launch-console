from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue

Operation = Literal[
    "classification", "injection", "missing_details", "panic", "draft", "evaluation"
]
TokenCount = Annotated[int, Field(strict=True, ge=0)]


class ModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: Operation
    instructions: Annotated[str, Field(min_length=1)]
    payload: dict[str, JsonValue]
    response_schema: dict[str, JsonValue]


class TokenUsage(BaseModel):
    """Only provider-reported counts. Missing fields stay unavailable, never inferred."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: TokenCount | None = None
    output_tokens: TokenCount | None = None
    total_tokens: TokenCount | None = None
    cached_input_tokens: TokenCount | None = None
    cache_write_input_tokens: TokenCount | None = None
    cache_write_5m_input_tokens: TokenCount | None = None
    cache_write_1h_input_tokens: TokenCount | None = None
    reasoning_output_tokens: TokenCount | None = None

    @property
    def complete(self) -> bool:
        return self.input_tokens is not None and self.output_tokens is not None


class ModelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["openai", "anthropic", "foundry", "fake"]
    model: str
    status: Literal["completed", "incomplete", "refused"]
    text: str | None
    usage: TokenUsage | None = None
    request_id: str | None = None
    reason: str | None = None


class ModelConfigurationError(ValueError):
    """Live configuration is missing or invalid; never silently substitute a fake."""


class ModelClientError(Exception):
    """Stable, sanitized failure metadata for application fallback and trace recording."""

    def __init__(
        self, reason: str, *, status_code: int | None = None, request_id: str | None = None
    ):
        self.reason = reason
        self.status_code = status_code
        self.request_id = request_id
        super().__init__(f"Model request failed: {reason}")


class ModelClient(Protocol):
    @property
    def model_name(self) -> str: ...

    async def generate(self, request: ModelRequest) -> ModelResponse: ...

    async def aclose(self) -> None: ...
