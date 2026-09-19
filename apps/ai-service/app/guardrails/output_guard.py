def validate_grounded_output(answer: str, *, has_context: bool) -> str:
    """Reject an answer that cannot be shown to anyone.

    An empty answer is a failure whether or not retrieval returned context: the caller
    streams this string to the user, and "" reads as the agent having silently given up.
    The check used to apply only when context existed, so the ungrounded path -- the one
    where something did go wrong -- was the one that skipped it.
    """
    validated = answer.strip()
    if not validated:
        raise ValueError(
            "Grounded answer cannot be empty" if has_context else "Answer cannot be empty"
        )
    return validated
