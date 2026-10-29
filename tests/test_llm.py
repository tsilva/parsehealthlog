"""Outbound reasoning settings for health-log processing and validation."""

import json

import httpx
import pytest
from openai import OpenAI

from parsehealthlog.main import LLM


@pytest.mark.parametrize(
    ("model", "resolved_model", "expected_effort"),
    [
        ("codex/gpt-6.1-sol", "codex/gpt-6.1-sol", "medium"),
        ("codex/gpt-6-astra", "codex/gpt-6-astra", "medium"),
        ("google/gemini-test", "openrouter/google/gemini-test", None),
        ("claudecode/sonnet", "claudecode/sonnet", None),
    ],
)
def test_llm_sends_medium_reasoning_for_codex(model, resolved_model, expected_effort):
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(200, json={
            "id": "synthetic-health-log",
            "object": "chat.completion",
            "created": 0,
            "model": payload["model"],
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "  Synthetic entry.  "},
                "finish_reason": "stop",
            }],
        })

    with OpenAI(
        api_key="synthetic",
        base_url="http://agentbridge.test/api/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(respond)),
    ) as client:
        result = LLM(client, model)([{"role": "user", "content": "Synthetic journal."}])

    assert result == "Synthetic entry."
    assert len(requests) == 1
    assert requests[0]["model"] == resolved_model
    assert requests[0].get("reasoning_effort") == expected_effort
