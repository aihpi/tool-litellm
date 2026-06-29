from typing import Any, Dict, List, Optional, Union

import httpx

from litellm.llms.base_llm import BaseEmbeddingConfig
from litellm.llms.base_llm.chat.transformation import BaseLLMException
from litellm.litellm_core_utils.litellm_logging import Logging as LiteLLMLoggingObj
from litellm.types.llms.openai import AllEmbeddingInputValues, AllMessageValues
from litellm.types.utils import Embedding, EmbeddingResponse, Usage


class AihpiProviderEmbeddingError(BaseLLMException):
    pass


class AihpiProviderEmbeddingConfig(BaseEmbeddingConfig):
    def get_supported_openai_params(self, model: str) -> List[str]:
        return []

    def map_openai_params(
        self,
        non_default_params: dict,
        optional_params: dict,
        model: str,
        drop_params: bool,
    ) -> dict:
        return optional_params

    def get_complete_url(
        self,
        api_base: Optional[str],
        api_key: Optional[str],
        model: str,
        optional_params: dict,
        litellm_params: dict,
        stream: Optional[bool] = None,
    ) -> str:
        if api_base is None:
            raise ValueError("api_base is required for aihpi-provider embeddings")
        api_base = api_base.rstrip("/")
        return f"{api_base}/v1/embeddings"

    def transform_embedding_request(
        self,
        model: str,
        input: AllEmbeddingInputValues,
        optional_params: dict,
        headers: dict,
    ) -> dict:
        images: List[Dict[str, str]] = []

        def _append_image(entry: Dict[str, Any]) -> None:
            if "image_url" in entry and entry["image_url"]:
                images.append({"image_url": entry["image_url"]})
                return
            if "image_base64" in entry and entry["image_base64"]:
                images.append({"image_base64": entry["image_base64"]})
                return
            raise ValueError("Each image entry must include image_url or image_base64")

        def _handle_string(value: str) -> None:
            if value.startswith("http://") or value.startswith("https://"):
                images.append({"image_url": value})
                return
            if value.startswith("data:"):
                parts = value.split(",", 1)
                image_base64 = parts[1] if len(parts) == 2 else ""
                if not image_base64:
                    raise ValueError("Invalid data URI for image_base64 input")
                images.append({"image_base64": image_base64})
                return
            raise ValueError("Text-only input is not supported. Provide image URLs or image objects.")

        # Accept list of objects or strings; reject text-only values
        if isinstance(input, list):
            for item in input:
                if isinstance(item, dict):
                    _append_image(item)
                elif isinstance(item, str):
                    _handle_string(item)
                else:
                    raise ValueError("Invalid input type. Provide image URLs or objects with image_url/image_base64.")
        elif isinstance(input, dict):  # type: ignore[unreachable]
            _append_image(input)
        elif isinstance(input, str):
            _handle_string(input)
        else:
            raise ValueError("Invalid input type. Provide image URLs or objects with image_url/image_base64.")

        if not images:
            raise ValueError("At least one image must be provided for embeddings.")

        data: Dict[str, Any] = {
            "model": model,
            "images": images,
            **optional_params,
        }
        return data

    def transform_embedding_response(
        self,
        model: str,
        raw_response: httpx.Response,
        model_response: EmbeddingResponse,
        logging_obj: LiteLLMLoggingObj,
        api_key: Optional[str],
        request_data: dict,
        optional_params: dict,
        litellm_params: dict,
    ) -> EmbeddingResponse:
        response_json = raw_response.json()
        embeddings = response_json.get("embeddings")
        if not isinstance(embeddings, list):
            raise AihpiProviderEmbeddingError(
                status_code=raw_response.status_code,
                message="Invalid embeddings response format",
            )

        data: List[Embedding] = []
        for idx, emb in enumerate(embeddings):
            data.append(Embedding(embedding=emb, index=idx, object="embedding"))

        logging_obj.post_call(
            input=request_data.get("input"),
            api_key=api_key,
            additional_args={"complete_input_dict": request_data},
            original_response=response_json,
        )

        return EmbeddingResponse(
            model=model,
            data=data,
            object="list",
            usage=Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
        )

    def validate_environment(
        self,
        headers: dict,
        model: str,
        messages: List[AllMessageValues],
        optional_params: dict,
        litellm_params: dict,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ) -> dict:
        default_headers = {"Content-Type": "application/json"}
        if api_key:
            default_headers["Authorization"] = f"Bearer {api_key}"
        return {**default_headers, **headers}

    def get_error_class(
        self, error_message: str, status_code: int, headers: Union[dict, httpx.Headers]
    ) -> BaseLLMException:
        return AihpiProviderEmbeddingError(
            status_code=status_code,
            message=error_message,
        )
