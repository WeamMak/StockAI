"""Public behavior of the Bedrock GPT-OSS structured LLM adapter."""

from __future__ import annotations

import json
from dataclasses import replace
from time import sleep as blocking_sleep
from typing import Any

import pytest
from botocore.exceptions import (  # type: ignore[import-untyped]
    ClientError,
    EndpointConnectionError,
)
from tests.support.recommendations import (
    t27_manual_review_payload,
    t27_payload,
    t27_request,
)

from procurement.adapters.aws.bedrock import (
    APPROVED_MODEL_ID,
    BedrockStructuredLlm,
    create_bedrock_runtime_client,
)
from procurement.agent.recommendation_schema import (
    RECOMMENDATION_JSON_SCHEMA,
    load_procurement_system_prompt,
    validate_recommendation_payload,
)
from procurement.ports.llm import (
    LlmOutputInvalidError,
    LlmUnavailableError,
    RecommendationRequest,
)


class FakeBedrockRuntimeClient:
    """Record mocked Converse calls and return configured provider envelopes."""

    def __init__(self, *responses: object) -> None:
        self._responses = iter(responses)
        self.requests: list[dict[str, Any]] = []

    def converse(self, **request: Any) -> dict[str, Any]:
        self.requests.append(request)
        response = next(self._responses)
        if isinstance(response, Exception):
            raise response
        assert isinstance(response, dict)
        return response


class SlowBedrockRuntimeClient:
    """Take longer than the configured attempt timeout on every call."""

    def __init__(self) -> None:
        self.calls = 0

    def converse(self, **_request: Any) -> dict[str, Any]:
        self.calls += 1
        blocking_sleep(0.03)
        return _response("{}")


def _request() -> RecommendationRequest:
    return t27_request()


def _valid_text(request: RecommendationRequest | None = None) -> str:
    return json.dumps(t27_payload(request or _request()))


def _response(text: str) -> dict[str, Any]:
    return {
        "output": {"message": {"content": [{"text": text}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": 48, "outputTokens": 19, "totalTokens": 67},
    }


def _adapter(
    client: FakeBedrockRuntimeClient,
    *,
    model_id: str = APPROVED_MODEL_ID,
    **overrides: Any,
) -> BedrockStructuredLlm:
    return BedrockStructuredLlm(
        client=client,
        system_prompt=load_procurement_system_prompt(),
        output_schema=RECOMMENDATION_JSON_SCHEMA,
        validator=validate_recommendation_payload,
        model_id=model_id,
        **overrides,
    )


def test_boto_client_disables_sdk_retries_and_uses_the_approved_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    client = FakeBedrockRuntimeClient()

    def create_client(service_name: str, **kwargs: Any) -> object:
        captured["service_name"] = service_name
        captured.update(kwargs)
        return client

    monkeypatch.setattr(
        "procurement.adapters.aws.bedrock.boto3.client",
        create_client,
    )

    created = create_bedrock_runtime_client(region_name="us-east-1")

    assert created is client
    assert captured["service_name"] == "bedrock-runtime"
    assert captured["region_name"] == "us-east-1"
    config = captured["config"]
    assert config.connect_timeout == 30
    assert config.read_timeout == 30
    assert config.retries["total_max_attempts"] == 1

    with pytest.raises(ValueError, match="us-east-1"):
        create_bedrock_runtime_client(region_name="eu-west-1")


@pytest.mark.anyio
async def test_only_the_approved_gpt_oss_model_can_be_invoked() -> None:
    client = FakeBedrockRuntimeClient(_response(_valid_text()))
    adapter = _adapter(client)

    recommendation = await adapter.recommend(_request())

    assert [request["modelId"] for request in client.requests] == [
        "openai.gpt-oss-20b-1:0"
    ]
    assert recommendation.input_tokens == 48
    assert recommendation.output_tokens == 19
    schema = client.requests[0]["outputConfig"]["textFormat"]["structure"][
        "jsonSchema"
    ]["schema"]
    assert '"additionalProperties":false' in schema
    assert client.requests[0]["inferenceConfig"]["maxTokens"] == 2_048
    user_text = client.requests[0]["messages"][0]["content"][0]["text"]
    assert '"required_risk_flags":["LIMITED_VENDOR_HISTORY"]' in user_text
    assert '"preference_profile_id":"preference-1"' in user_text
    assert '"priority_order":["reliability","delivery","price"]' in user_text
    assert '"recommendation_fields":{' in user_text
    assert '"top_level_decision_field":"decision"' in user_text
    assert '"forbidden_wrapper_fields":["recommend","manual_review"]' in user_text

    with pytest.raises(ValueError, match="approved Bedrock model"):
        _adapter(
            FakeBedrockRuntimeClient(),
            model_id="openai.gpt-oss-120b-1:0",
        )


@pytest.mark.anyio
async def test_transient_failures_retry_twice_with_bounded_backoff() -> None:
    throttled = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "private detail"}},
        "Converse",
    )
    client = FakeBedrockRuntimeClient(
        throttled,
        throttled,
        _response(_valid_text()),
    )
    delays: list[float] = []

    async def record_delay(delay: float) -> None:
        delays.append(delay)

    adapter = _adapter(
        client,
        retry_delay_seconds=0.01,
        sleep=record_delay,
        jitter=lambda _lower, upper: upper,
    )

    recommendation = await adapter.recommend(_request())

    assert recommendation.product_id == "product-101"
    assert len(client.requests) == 3
    assert delays == [0.01, 0.02]


@pytest.mark.anyio
async def test_transient_connection_failures_use_the_same_retry_bound() -> None:
    disconnected = EndpointConnectionError(
        endpoint_url="https://private-bedrock-endpoint.invalid"
    )
    client = FakeBedrockRuntimeClient(
        disconnected,
        disconnected,
        _response(json.dumps(t27_manual_review_payload())),
    )

    recommendation = await _adapter(client, retry_delay_seconds=0).recommend(_request())

    assert recommendation.decision.value == "manual_review"
    assert len(client.requests) == 3


@pytest.mark.anyio
async def test_each_timeout_is_bounded_and_retried_at_most_twice() -> None:
    client = SlowBedrockRuntimeClient()
    adapter = BedrockStructuredLlm(
        client=client,
        system_prompt=load_procurement_system_prompt(),
        output_schema=RECOMMENDATION_JSON_SCHEMA,
        validator=validate_recommendation_payload,
        attempt_timeout_seconds=0.001,
        retry_delay_seconds=0,
    )

    with pytest.raises(LlmUnavailableError) as raised:
        await adapter.recommend(_request())

    assert str(raised.value) == "The recommendation model is unavailable."
    assert client.calls == 3


@pytest.mark.anyio
async def test_permanent_bedrock_error_is_not_retried_or_exposed() -> None:
    unsafe_detail = "private provider access detail"
    denied = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": unsafe_detail}},
        "Converse",
    )
    client = FakeBedrockRuntimeClient(denied)

    with pytest.raises(LlmUnavailableError) as raised:
        await _adapter(client).recommend(_request())

    assert len(client.requests) == 1
    assert str(raised.value) == "The recommendation model is unavailable."
    assert unsafe_detail not in str(raised.value)


@pytest.mark.anyio
async def test_officer_note_is_included_in_the_bedrock_message() -> None:
    request = replace(_request(), officer_note="Avoid Vendor X, temporary issue.")
    client = FakeBedrockRuntimeClient(_response(_valid_text(request)))
    adapter = _adapter(client)

    await adapter.recommend(request)

    sent_message = client.requests[0]["messages"][0]["content"][0]["text"]
    assert "Avoid Vendor X, temporary issue." in sent_message


@pytest.mark.anyio
async def test_missing_officer_note_omits_the_field_from_the_bedrock_message() -> None:
    request = _request()
    client = FakeBedrockRuntimeClient(_response(_valid_text(request)))
    adapter = _adapter(client)

    await adapter.recommend(request)

    sent_message = client.requests[0]["messages"][0]["content"][0]["text"]
    assert '"officer_note"' not in sent_message


@pytest.mark.anyio
async def test_one_schema_repair_attempt_then_safe_invalid_output_fallback() -> None:
    private_invalid_output = "private malformed model output"
    valid_output = _valid_text()
    repaired_client = FakeBedrockRuntimeClient(
        _response(private_invalid_output),
        _response(valid_output),
    )

    recommendation = await _adapter(repaired_client).recommend(_request())

    assert recommendation.product_id == "product-101"
    assert len(repaired_client.requests) == 2
    repair_message = repaired_client.requests[1]["messages"][-1]["content"][0]["text"]
    assert "previous response was invalid" in repair_message.lower()
    assert "top-level `decision` field" in repair_message
    assert "never create a `recommend` wrapper" in repair_message
    assert "required_risk_flags" in repair_message
    assert private_invalid_output not in repair_message

    invalid_client = FakeBedrockRuntimeClient(
        _response(private_invalid_output),
        _response(private_invalid_output),
    )

    with pytest.raises(LlmOutputInvalidError) as raised:
        await _adapter(invalid_client).recommend(_request())

    assert len(invalid_client.requests) == 2
    assert str(raised.value) == "The recommendation model returned an invalid result."
    assert private_invalid_output not in str(raised.value)


@pytest.mark.anyio
async def test_one_gpt_oss_leading_quote_is_normalized_before_validation() -> None:
    live_text = '"' + _valid_text() + "\n"
    response = _response(live_text)
    response["output"]["message"]["content"].insert(
        0,
        {"reasoningContent": {"reasoningText": {"text": "not retained"}}},
    )
    client = FakeBedrockRuntimeClient(response, response)

    recommendation = await _adapter(client).recommend(_request())

    assert recommendation.product_id == "product-101"
    assert len(client.requests) == 1


@pytest.mark.anyio
async def test_gpt_oss_extra_object_prefix_is_normalized_before_validation() -> None:
    live_text = '{"' + _valid_text() + "\n"
    client = FakeBedrockRuntimeClient(_response(live_text), _response(live_text))

    recommendation = await _adapter(client).recommend(_request())

    assert recommendation.product_id == "product-101"
    assert len(client.requests) == 1


@pytest.mark.parametrize(
    "invalid_text",
    (
        """```json
        {"decision":"recommend"}
        ```""",
        '"{"decision":"recommend"} trailing',
        '"{\\"decision\\":\\"recommend\\"}"',
        '"{"decision" "recommend"}',
        '"{"decision":"recommend"}',
        (
            '"{"decision":"recommend","product_id":"not-eligible",'
            '"rationale":"Invalid selection.","risk_flags":[],'
            '"budget_acknowledgement":"not_evaluated"}'
        ),
    ),
)
@pytest.mark.anyio
async def test_leading_quote_normalization_rejects_every_broader_case(
    invalid_text: str,
) -> None:
    client = FakeBedrockRuntimeClient(
        _response(invalid_text),
        _response("still invalid"),
    )

    with pytest.raises(LlmOutputInvalidError):
        await _adapter(client).recommend(_request())

    assert len(client.requests) == 2


@pytest.mark.anyio
async def test_reasoning_content_is_ignored_and_never_returned() -> None:
    hidden_reasoning = "private hidden reasoning content"
    payload = t27_payload()
    payload["rationale"] = "Use bounded supplied evidence."
    client = FakeBedrockRuntimeClient(
        {
            "output": {
                "message": {
                    "content": [
                        {
                            "reasoningContent": {
                                "reasoningText": {"text": hidden_reasoning}
                            }
                        },
                        {"text": json.dumps(payload)},
                    ]
                }
            },
            "stopReason": "end_turn",
            "usage": {"inputTokens": 71, "outputTokens": 23, "totalTokens": 94},
        }
    )

    recommendation = await _adapter(client).recommend(_request())

    assert recommendation.rationale == "Use bounded supplied evidence."
    assert hidden_reasoning not in repr(recommendation)
