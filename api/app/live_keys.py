"""Redis names shared by the API and the live worker (no heavy imports)."""


def live_channel(session_id) -> str:
    return f"live:{session_id}"


def command_key(session_id) -> str:
    return f"live:cmd:{session_id}"
