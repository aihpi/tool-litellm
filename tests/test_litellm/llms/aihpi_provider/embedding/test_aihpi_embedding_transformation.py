from unittest.mock import MagicMock

import pytest

from litellm.llms.aihpi_provider.embedding.transformation import (
    AihpiProviderEmbeddingConfig,
)
from litellm.types.utils import EmbeddingResponse


def test_should_transform_embedding_request_for_image_urls_and_data_uri():
    config = AihpiProviderEmbeddingConfig()

    result = config.transform_embedding_request(
        model="aihpi-provider/dinov3-vitl",
        input=[
            "https://example.com/image.png",
            "data:image/png;base64,AAABBB",
        ],
        optional_params={"foo": "bar"},
        headers={},
    )

    assert result["model"] == "aihpi-provider/dinov3-vitl"
    assert result["foo"] == "bar"
    assert result["images"] == [
        {"image_url": "https://example.com/image.png"},
        {"image_base64": "AAABBB"},
    ]


def test_should_transform_embedding_request_for_image_objects():
    config = AihpiProviderEmbeddingConfig()

    result = config.transform_embedding_request(
        model="aihpi-provider/dinov3-vitl",
        input=[{"image_url": "https://example.com/image.png"}],
        optional_params={},
        headers={},
    )

    assert result["images"] == [{"image_url": "https://example.com/image.png"}]


def test_should_reject_text_only_input():
    config = AihpiProviderEmbeddingConfig()

    with pytest.raises(ValueError, match="Text-only input is not supported"):
        config.transform_embedding_request(
            model="aihpi-provider/dinov3-vitl",
            input="hello",
            optional_params={},
            headers={},
        )


def test_should_transform_embedding_response():
    config = AihpiProviderEmbeddingConfig()

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "embeddings": [[0.1, 0.2], [0.3, 0.4]],
        "dim": 2,
        "model_id": "dinov3-vitl",
    }
    mock_response.status_code = 200

    model_response = EmbeddingResponse()
    logging_obj = MagicMock()

    response = config.transform_embedding_response(
        model="aihpi-provider/dinov3-vitl",
        raw_response=mock_response,
        model_response=model_response,
        logging_obj=logging_obj,
        api_key="test-key",
        request_data={"input": ["https://example.com/image.png"]},
        optional_params={},
        litellm_params={},
    )

    logging_obj.post_call.assert_called_once()
    assert response.model == "aihpi-provider/dinov3-vitl"
    assert len(response.data) == 2
    assert response.data[0]["embedding"] == [0.1, 0.2]
