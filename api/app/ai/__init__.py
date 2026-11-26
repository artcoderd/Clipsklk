class AIError(Exception):
    """AI selection failed; the pipeline falls back to the non-AI selection."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
