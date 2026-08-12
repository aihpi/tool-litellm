from typing import Any, Callable, Dict, List, Optional, Union

import httpx

from litellm.images.utils import ImageEditRequestUtils
from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler, HTTPHandler
from litellm.llms.custom_llm import CustomLLM, CustomLLMError
from litellm.types.utils import Embedding, EmbeddingResponse, ImageResponse, Usage


def _build_embedding_headers(api_key: Optional[str]) -> dict:
    headers: dict = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _parse_image_input(input: list) -> List[Dict[str, str]]:
    images: List[Dict[str, str]] = []

    for item in input:
        if isinstance(item, dict):
            if "image_url" in item and item["image_url"]:
                images.append({"image_url": item["image_url"]})
            elif "image_base64" in item and item["image_base64"]:
                images.append({"image_base64": item["image_base64"]})
            else:
                raise ValueError("Each image entry must include image_url or image_base64")
        elif isinstance(item, str):
            if item.startswith(("http://", "https://")):
                images.append({"image_url": item})
            elif item.startswith("data:"):
                parts = item.split(",", 1)
                b64 = parts[1] if len(parts) == 2 else ""
                if not b64:
                    raise ValueError("Invalid data URI for image_base64 input")
                images.append({"image_base64": b64})
            else:
                raise ValueError("Text-only input is not supported. Provide image URLs or image objects.")
        else:
            raise ValueError("Invalid input type. Provide image URLs or objects with image_url/image_base64.")

    if not images:
        raise ValueError("At least one image must be provided for embeddings.")

    return images


def _parse_embedding_response(model: str, raw: dict) -> EmbeddingResponse:
    embeddings = raw.get("embeddings")
    if not isinstance(embeddings, list):
        raise CustomLLMError(status_code=500, message="Invalid embeddings response format")

    return EmbeddingResponse(
        model=model,
        data=[Embedding(embedding=emb, index=idx, object="embedding") for idx, emb in enumerate(embeddings)],
        object="list",
        usage=Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
    )


def _build_image_edit_form(
    model: str,
    image: Any,
    prompt: Optional[str],
    optional_params: dict,
) -> tuple:
    data: Dict[str, Any] = {"model": model}
    if prompt:
        data["prompt"] = prompt
    data.update(optional_params)

    files_list: list = []
    img = image[0] if isinstance(image, list) else image
    if img is not None:
        content_type = ImageEditRequestUtils.get_image_content_type(img)
        name = img.name if hasattr(img, "name") else "image.png"
        files_list.append(("image", (name, img, content_type)))

    mask = optional_params.get("mask")
    if mask is not None:
        content_type = ImageEditRequestUtils.get_image_content_type(mask)
        name = mask.name if hasattr(mask, "name") else "mask.png"
        files_list.append(("mask", (name, mask, content_type)))

    return data, files_list


class AihpiProviderHandler(CustomLLM):

    def embedding(
        self,
        model: str,
        input: list,
        model_response: EmbeddingResponse,
        print_verbose: Callable,
        logging_obj: Any,
        optional_params: dict,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        timeout: Optional[Union[float, httpx.Timeout]] = None,
        litellm_params=None,
    ) -> EmbeddingResponse:
        if not api_base:
            raise CustomLLMError(status_code=400, message="api_base is required for aihpi-provider embeddings")

        url = f"{api_base.rstrip('/')}/v1/embeddings"
        headers = _build_embedding_headers(api_key)
        body = {"model": model, "images": _parse_image_input(input), **optional_params}

        client = HTTPHandler(timeout=timeout or 600.0)
        resp = client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        return _parse_embedding_response(model, resp.json())

    async def aembedding(
        self,
        model: str,
        input: list,
        model_response: EmbeddingResponse,
        print_verbose: Callable,
        logging_obj: Any,
        optional_params: dict,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        timeout: Optional[Union[float, httpx.Timeout]] = None,
        litellm_params=None,
    ) -> EmbeddingResponse:
        if not api_base:
            raise CustomLLMError(status_code=400, message="api_base is required for aihpi-provider embeddings")

        url = f"{api_base.rstrip('/')}/v1/embeddings"
        headers = _build_embedding_headers(api_key)
        body = {"model": model, "images": _parse_image_input(input), **optional_params}

        client = AsyncHTTPHandler(timeout=timeout or 600.0)
        resp = await client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        return _parse_embedding_response(model, resp.json())

    def image_edit(
        self,
        model: str,
        image: Any,
        prompt: Optional[str],
        model_response: ImageResponse,
        api_key: Optional[str],
        api_base: Optional[str],
        optional_params: dict,
        logging_obj: Any,
        timeout: Optional[Union[float, httpx.Timeout]] = None,
        client: Optional[HTTPHandler] = None,
    ) -> ImageResponse:
        if not api_base:
            raise CustomLLMError(status_code=400, message="api_base is required for aihpi-provider image edits")

        url = f"{api_base.rstrip('/')}/images/edits"
        headers: dict = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        data, files = _build_image_edit_form(model, image, prompt, optional_params)
        http = client or HTTPHandler(timeout=timeout or 600.0)
        resp = http.post(url, data=data, files=files, headers=headers)
        resp.raise_for_status()
        return ImageResponse(**resp.json())

    async def aimage_edit(
        self,
        model: str,
        image: Any,
        prompt: Optional[str],
        model_response: ImageResponse,
        api_key: Optional[str],
        api_base: Optional[str],
        optional_params: dict,
        logging_obj: Any,
        timeout: Optional[Union[float, httpx.Timeout]] = None,
        client: Optional[AsyncHTTPHandler] = None,
    ) -> ImageResponse:
        if not api_base:
            raise CustomLLMError(status_code=400, message="api_base is required for aihpi-provider image edits")

        url = f"{api_base.rstrip('/')}/images/edits"
        headers: dict = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        data, files = _build_image_edit_form(model, image, prompt, optional_params)
        http = client or AsyncHTTPHandler(timeout=timeout or 600.0)
        resp = await http.post(url, data=data, files=files, headers=headers)
        resp.raise_for_status()
        return ImageResponse(**resp.json())


handler = AihpiProviderHandler()
