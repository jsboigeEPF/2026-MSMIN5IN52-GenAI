"""Terminal-safe text. LLM output and repository content are untrusted: a raw ESC character in a diff could
erase or hide a line on the reviewer's screen (ANSI escape sequences), so it must never reach a terminal."""

import re

# C0 controls except tab and newline, DEL, and C1 controls.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
# Same set, but carriage return is legitimate in source files (CRLF line endings).
_CONTROL_IN_CODE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def printable(text: str) -> str:
    """Show control characters as visible escapes (ESC becomes `\\x1b`) instead of sending them."""
    return _CONTROL.sub(lambda m: f"\\x{ord(m.group()):02x}", text.replace("\r\n", "\n"))


def control_chars_error(text: str) -> str | None:
    """Why `text` is refused as generated code, or None if it holds no control character."""
    found = _CONTROL_IN_CODE.search(text)
    if found is None:
        return None
    return f"contains the control character \\x{ord(found.group()):02x}, which could hide code on screen"
