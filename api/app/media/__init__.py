class PipelineError(Exception):
    """An expected failure with a stable code the web app translates for the user."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
