import asyncio
from collections import deque
from collections.abc import Mapping, Sequence

from app.llm.client import ModelClientError, ModelRequest, ModelResponse, Operation


class FakeModelClient:
    """Explicit scripted responses per operation; safe for concurrently scheduled checks."""

    model_name = "fake-model"

    def __init__(self, script: Mapping[Operation, Sequence[ModelResponse | ModelClientError]]):
        self._responses = {operation: deque(responses) for operation, responses in script.items()}
        self.calls: list[ModelRequest] = []
        self.closed = False

    async def generate(self, request: ModelRequest) -> ModelResponse:
        if self.closed:
            raise RuntimeError("Fake model client is closed")
        self.calls.append(request.model_copy(deep=True))
        responses = self._responses.get(request.operation)
        if not responses:
            raise AssertionError(f"No scripted model response for {request.operation}")
        # Reserve before yielding so concurrent operations keep separate scripts.
        response = responses.popleft()
        await asyncio.sleep(0)
        if isinstance(response, ModelClientError):
            raise response
        if response.provider != "fake":
            raise AssertionError("Fake responses must be explicitly labeled provider='fake'")
        return response.model_copy(deep=True)

    async def aclose(self) -> None:
        self.closed = True
