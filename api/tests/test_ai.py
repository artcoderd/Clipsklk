import pytest

from app.ai import AIError, providers
from app.ai.prompts import format_transcript
from app.ai.schema import Selection, json_schema
from app.ai.select import choose, heuristic_caption
from app.config import get_settings
from tests.ai_mocks import MockAI, ideas
from tests.media_fixtures import fake_transcript


@pytest.fixture
def keys(monkeypatch):
    s = get_settings()
    for k, v in (("anthropic_api_key", "sk-ant-test"), ("openai_api_key", "sk-test"), ("gemini_api_key", "g-test")):
        monkeypatch.setattr(s, k, v)
    yield s


def use(monkeypatch, mock: MockAI):
    monkeypatch.setitem(providers.HTTP_CLIENTS, mock.provider, mock.client())


TR = fake_transcript(180)                    # a sentence every 5 s (0-4.8, 5-9.8, ...)
AN = {"laugh": [0.0] * 180, "scream": [0.0] * 180, "volume": [0.0] * 180}


def test_schema_is_strict():
    s = json_schema(Selection)
    item = s["$defs"]["ClipIdea"]
    assert "title" in item["properties"] and item["additionalProperties"] is False
    assert set(item["required"]) == set(item["properties"])


def test_claude_request_shape(keys, monkeypatch):
    mock = MockAI(ideas((20, 49.8), (100, 129.8)))
    use(monkeypatch, mock)
    moments, result, failure = choose("anthropic", TR, AN, 2, 30, 180.0, [], "Nada de groserías", 4)
    assert failure is None and result.provider == "anthropic" and result.model == "claude-opus-5-5"
    req = mock.requests[0]
    body = req["body"]
    assert req["url"].split("?")[0].endswith("/v1/messages")
    assert body["model"] == "claude-opus-5-5"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["output_config"]["effort"] == "medium"
    assert body["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in req["headers"]["anthropic-beta"]
    transcript_block, request_block = body["messages"][0]["content"]
    assert transcript_block["cache_control"] == {"type": "ephemeral", "ttl": "1h"}   # reused for "más clips"
    assert "[20.0–24.8]" in transcript_block["text"]
    assert "Nada de groserías" in request_block["text"] and "cache_control" not in request_block
    assert "temperature" not in body and "thinking" not in body
    assert [(m.start, m.end) for m in moments] == [(20.0, 49.8), (100.0, 129.8)]
    assert moments[0].ai and moments[0].reasons[0] == "IA"
    assert moments[0].hashtags == ["podcast", "humor"][:4]       # cleaned and de-duplicated
    assert moments[0].cover_t == 3.0 and moments[0].title == "El momento 0"


def test_proposals_are_checked_against_the_transcript(keys, monkeypatch):
    # 1: times off by 1-2 s (snapped); 2: overlaps the first (dropped); 3: invented time (dropped);
    # 4: far too long (trimmed); 5: overlaps an existing clip (dropped).
    use(monkeypatch, MockAI(ideas((21.3, 48.9), (40, 70), (300, 330), (60, 160), (150, 175))))
    moments, _, _ = choose("anthropic", TR, AN, 3, 30, 180.0, [(145.0, 178.0)], "", 5)
    spans = [(m.start, m.end, m.ai) for m in moments]
    assert (20.0, 49.8, True) in spans
    trimmed = next(m for m in moments if m.ai and m.start == 60.0)
    assert 18 <= trimmed.end - trimmed.start <= 42
    # 2 valid AI clips; the only free gap left (100-145 s) is filled without AI.
    assert len(moments) == 3 and sum(not m.ai for m in moments) == 1
    for a, b, _ in spans:
        assert b <= 145.0 or a >= 178.0           # never on top of existing clips
    assert all(m.title and m.caption is not None for m in moments)


@pytest.mark.parametrize("provider,model", [("openai", "gpt-5"), ("gemini", "gemini-2.5-pro")])
def test_other_providers(keys, monkeypatch, provider, model):
    mock = MockAI(ideas((20, 49.8)), provider=provider)
    use(monkeypatch, mock)
    moments, result, failure = choose(provider, TR, AN, 1, 30, 180.0, [], "", 5)
    assert failure is None and result.provider == provider and result.output_tokens > 0
    body = mock.requests[0]["body"]
    if provider == "openai":
        assert body["model"] == model and body["text"]["format"]["type"] == "json_schema"
        assert body["text"]["format"]["strict"] is True
    else:
        assert model in mock.requests[0]["url"]
        assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert moments[0].ai and (moments[0].start, moments[0].end) == (20.0, 49.8)


@pytest.mark.parametrize("mock,code", [
    (MockAI(ideas(), status=401), "ai_auth"),
    (MockAI(ideas(), status=429), "ai_rate_limited"),
    (MockAI(ideas(), stop_reason="refusal"), "ai_refused"),
    (MockAI("esto no es json"), "ai_bad_output"),
])
def test_failures_fall_back_without_ai(keys, monkeypatch, mock, code):
    use(monkeypatch, mock)
    monkeypatch.setattr("anthropic._constants.INITIAL_RETRY_DELAY", 0.0, raising=False)
    monkeypatch.setattr("anthropic._constants.MAX_RETRY_DELAY", 0.0, raising=False)
    moments, result, failure = choose("anthropic", TR, AN, 2, 30, 180.0, [], "", 5)
    assert failure == code and result is None
    assert len(moments) == 2 and not any(m.ai for m in moments)


def test_not_configured(monkeypatch):
    monkeypatch.setattr(get_settings(), "anthropic_api_key", None)
    with pytest.raises(AIError) as e:
        providers.ask("anthropic", "s", "c", "r", Selection)
    assert e.value.code == "ai_not_configured"


def test_transcript_format_and_cues():
    an = {"laugh": [0.0] * 60, "scream": [0.0] * 60}
    an["laugh"][12] = 0.9
    text = format_transcript(fake_transcript(60), an)
    assert "[10.0–14.8] esto es una frase de prueba (risas)" in text


def test_heuristic_caption():
    title, caption, tags = heuristic_caption("¡No lo puedo creer! El podcast de hoy habla de dinero, dinero y dinero.")
    assert title == "¡No lo puedo creer!" and caption.startswith("¡No") and tags[0] == "dinero"
