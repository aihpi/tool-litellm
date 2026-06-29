import json

import pytest


def test_should_send_aihpi_embedding_request():
    respx = pytest.importorskip("respx")
    import litellm

    litellm.disable_aiohttp_transport = True
    api_base = "https://api.example.ai"
    with respx.mock:
        route = respx.post(f"{api_base}/v1/embeddings").respond(
            json={"embeddings": [[0.1, 0.2]], "dim": 2, "model_id": "dinov3"}
        )

        response = litellm.embedding(
            model="aihpi-provider/dinov3-vitl",
            input="https://example.com/image.png",
            api_key="test-key",
            api_base=api_base,
        )

        assert response.data[0]["embedding"] == [0.1, 0.2]
        assert route.called

        request = route.calls[0].request
        assert request.headers["Authorization"] == "Bearer test-key"
        request_body = json.loads(request.content.decode("utf-8"))
        assert request_body["model"] == "dinov3-vitl"
        assert request_body["images"] == [
            {"image_url": "https://example.com/image.png"}
        ]
