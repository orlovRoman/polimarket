"""
core/constants.py
Shared constants and enums used across the engine and agents.
"""


class Outcome:
    """String constants for market outcome sides."""
    YES = "YES"
    NO = "NO"

    @classmethod
    def values(cls) -> tuple[str, str]:
        return (cls.YES, cls.NO)

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return str(value).upper().strip() in cls.values()
