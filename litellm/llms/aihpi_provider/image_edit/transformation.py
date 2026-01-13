from typing import Any, Dict, Optional, Tuple

from httpx._types import RequestFiles
from litellm.llms.openai.image_edit.transformation import OpenAIImageEditConfig
from litellm.images.utils import ImageEditRequestUtils
from litellm.types.llms.openai import FileTypes
from litellm.types.router import GenericLiteLLMParams


class AihpiProviderImageEditConfig(OpenAIImageEditConfig):
    def transform_image_edit_request(
        self,
        model: str,
        prompt: str,
        image: FileTypes,
        image_edit_optional_request_params: Dict,
        litellm_params: GenericLiteLLMParams,
        headers: dict,
    ) -> Tuple[Dict, RequestFiles]:
        data: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            **image_edit_optional_request_params,
        }
        files_list: RequestFiles = []

        # Use only the first image if a list is provided.
        img = image[0] if isinstance(image, list) else image
        if img is not None:
            content_type = ImageEditRequestUtils.get_image_content_type(img)
            if hasattr(img, "name"):
                files_list.append(("image", (img.name, img, content_type)))
            else:
                files_list.append(("image", ("image.png", img, content_type)))

        mask = image_edit_optional_request_params.get("mask")
        if mask is not None:
            content_type = ImageEditRequestUtils.get_image_content_type(mask)
            if hasattr(mask, "name"):
                files_list.append(("mask", (mask.name, mask, content_type)))
            else:
                files_list.append(("mask", ("mask.png", mask, content_type)))

        return data, files_list

    def validate_environment(
        self, headers: dict, model: str, api_key: Optional[str] = None
    ) -> dict:
        if api_key:
            headers.update({"Authorization": f"Bearer {api_key}"})
        return headers

    def get_supported_openai_params(self, model: str) -> list:
        base = super().get_supported_openai_params(model)
        return base + [
            "num_inference_steps",
            "true_cfg_scale",
            "seed",
            "negative_prompt",
        ]

    def map_openai_params(
        self, image_edit_optional_params, model: str, drop_params: bool
    ):
        return dict(image_edit_optional_params)

    def get_complete_url(
        self, model: str, api_base: Optional[str], litellm_params: dict
    ) -> str:
        if api_base is None:
            raise ValueError("api_base is required for aihpi-provider image edits")
        api_base = api_base.rstrip("/")
        return f"{api_base}/images/edits"
