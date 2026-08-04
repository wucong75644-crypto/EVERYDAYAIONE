"""Legacy process settings loader for Projection and Authorization."""


def load_application_process_settings():
    from core.config import get_settings
    return get_settings()
