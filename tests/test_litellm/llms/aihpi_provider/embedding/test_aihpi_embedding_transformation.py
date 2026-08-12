import pytest

from aihpi.provider import _parse_embedding_response, _parse_image_input
from litellm.llms.custom_llm import CustomLLMError


def test_should_parse_image_urls_and_data_uri():
    result = _parse_image_input([
        "https://example.com/image.png",
        "data:image/png;base64,AAABBB",
    ])

    assert result == [
        {"image_url": "https://example.com/image.png"},
        {"image_base64": "AAABBB"},
    ]


def test_should_parse_image_objects():
    result = _parse_image_input([{"image_url": "https://example.com/image.png"}])

    assert result == [{"image_url": "https://example.com/image.png"}]


def test_should_reject_text_only_input():
    with pytest.raises(ValueError, match="Text-only input is not supported"):
        _parse_image_input(["hello"])


def test_should_reject_empty_input():
    with pytest.raises(ValueError, match="At least one image must be provided"):
        _parse_image_input([])


def test_should_parse_embedding_response():
    response = _parse_embedding_response(
        model="aihpi-provider/dinov3-vitl",
        raw={"embeddings": [[0.1, 0.2], [0.3, 0.4]]},
    )

    assert response.model == "aihpi-provider/dinov3-vitl"
    assert len(response.data) == 2
    assert response.data[0]["embedding"] == [0.1, 0.2]
    assert response.data[1]["embedding"] == [0.3, 0.4]


def test_should_reject_invalid_embedding_response():
    with pytest.raises(CustomLLMError, match="Invalid embeddings response format"):
        _parse_embedding_response(
            model="aihpi-provider/dinov3-vitl",
            raw={"not_embeddings": "bad"},
        )
