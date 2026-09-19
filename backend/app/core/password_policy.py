"""One place that decides what counts as an acceptable password.

The rule used to be `min_length=8` written separately on each schema, which meant the
registration form and the admin's create-employee form could drift apart, and did.

Length is doing most of the work here. The blocklist only catches passwords that *are*
one of a handful of notorious strings -- it is a floor, not a strength meter. A
deployment that wants real strength checking should wire in a proper corpus (the
SecLists top-10k, or zxcvbn scoring); that is a dependency decision, not a default.
"""

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128

# Matched whole, case-insensitively, after trimming surrounding whitespace.
_BLOCKED_PASSWORDS = frozenset(
    {
        "123456789012",
        "111111111111",
        "password1234",
        "passwordpassword",
        "qwertyuiop12",
        "administrator",
        "letmeinletmein",
        "iloveyou1234",
        "welcome12345",
        "changeme1234",
    }
)


def validate_password(password: str) -> str:
    """Return the password unchanged, or raise ValueError explaining what is wrong.

    Shaped for use as a pydantic field validator, so the message reaches the caller as a
    422 field error rather than a generic rejection.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters long"
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must be at most {MAX_PASSWORD_LENGTH} characters long"
        )
    if password.strip().lower() in _BLOCKED_PASSWORDS:
        raise ValueError("This password is too common; choose a different one")
    return password
