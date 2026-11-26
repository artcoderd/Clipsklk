"""Fake HTTP backends for the three AI providers: they record the request the
SDK sends and answer like the real API would (shape per provider)."""
import json

import httpx
import httpx2


class MockAI:
    def __init__(self, answer: dict | str, provider: str = "anthropic", status: int = 200, stop_reason="end_turn"):
        self.answer, self.provider, self.status, self.stop_reason = answer, provider, status, stop_reason
        self.requests: list[dict] = []

    def handler(self, request):
        body = json.loads(request.content or b"{}")
        self.requests.append({"url": str(request.url), "headers": dict(request.headers), "body": body})
        text = self.answer if isinstance(self.answer, str) else json.dumps(self.answer)
        if self.status != 200:
            return httpx.Response(self.status, json={"type": "error", "error": {"type": "api_error", "message": "boom"},
                                                     "code": self.status, "message": "boom", "status": "ERR"})
        if self.provider == "anthropic":
            return httpx.Response(200, json={
                "id": "msg_1", "type": "message", "role": "assistant", "model": body.get("model"),
                "content": [{"type": "text", "text": text}] if self.stop_reason != "refusal" else [],
                "stop_reason": self.stop_reason, "stop_sequence": None,
                "usage": {"input_tokens": 120, "output_tokens": 80, "cache_creation_input_tokens": 3000,
                          "cache_read_input_tokens": 0}})
        if self.provider == "openai":
            return httpx.Response(200, json={
                "id": "resp_1", "object": "response", "created_at": 1, "status": "completed", "model": body.get("model"),
                "output": [{"type": "message", "id": "m1", "role": "assistant", "status": "completed",
                            "content": [{"type": "output_text", "text": text, "annotations": []}]}],
                "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
                "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
                          "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}})
        # gemini
        return httpx.Response(200, json={
            "candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 90, "candidatesTokenCount": 40, "totalTokenCount": 130}})

    def client(self):
        # All three SDKs are built on httpx2 (the same client type production uses).
        lib = mod = httpx2

        def handle(request):
            resp = self.handler(request)
            return mod.Response(resp.status_code, content=resp.content, headers={"content-type": "application/json"})
        return lib.Client(transport=lib.MockTransport(handle))


def ideas(*spans, title="El momento", score=90):
    return {"clips": [{"start": a, "end": b, "title": f"{title} {i}", "caption": "Mira esto.",
                       "hashtags": ["#podcast", "humor", "humor"], "cover_time": a + 3, "reason": "Gancho fuerte",
                       "score": score - i} for i, (a, b) in enumerate(spans)]}
