def error_kinds(error):
    """Unwrap transport task groups without echoing provider text or credentials."""
    if isinstance(error, BaseExceptionGroup):
        return ', '.join(sorted({error_kinds(child) for child in error.exceptions}))
    return type(error).__name__
