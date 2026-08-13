from .provider import handler

__all__ = ["handler"]


def register() -> None:
    """Startup hook for litellm proxy. Set LITELLM_WORKER_STARTUP_HOOKS=litellm.aihpi:register"""
    import litellm
    from litellm.utils import custom_llm_setup

    from .routes import register_routes

    if not any(item["provider"] == "aihpi-provider" for item in litellm.custom_provider_map):
        litellm.custom_provider_map.append({"provider": "aihpi-provider", "custom_handler": handler})
    custom_llm_setup()

    register_routes()
