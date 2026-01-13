from typing import Optional

from litellm.llms.openai.image_edit.transformation import OpenAIImageEditConfig


class AihpiProviderImageEditConfig(OpenAIImageEditConfig):
    def validate_environment(
        self, headers: dict, model: str, api_key: Optional[str] = None
    ) -> dict:
        if api_key:
            headers.update({"Authorization": f"Bearer {api_key}"})
        return headers

    def get_complete_url(
        self, model: str, api_base: Optional[str], litellm_params: dict
    ) -> str:
        if api_base is None:
            raise ValueError("api_base is required for aihpi-provider image edits")
        api_base = api_base.rstrip("/")
        return f"{api_base}/images/edits"
