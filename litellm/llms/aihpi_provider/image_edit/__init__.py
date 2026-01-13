from litellm.llms.base_llm.image_edit.transformation import BaseImageEditConfig

from .transformation import AihpiProviderImageEditConfig

__all__ = [
    "AihpiProviderImageEditConfig",
    "get_aihpi_provider_image_edit_config",
]


def get_aihpi_provider_image_edit_config(model: str) -> BaseImageEditConfig:
    return AihpiProviderImageEditConfig()
