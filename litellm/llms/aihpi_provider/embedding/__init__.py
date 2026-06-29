from litellm.llms.base_llm.embedding.transformation import BaseEmbeddingConfig

from .transformation import AihpiProviderEmbeddingConfig

__all__ = [
    "AihpiProviderEmbeddingConfig",
    "get_aihpi_provider_embedding_config",
]


def get_aihpi_provider_embedding_config(model: str) -> BaseEmbeddingConfig:
    return AihpiProviderEmbeddingConfig()
