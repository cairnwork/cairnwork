"""Tamper scan pattern for candidate-defined exception attribute hooks.

These hooks can run inside the verifier when trusted observers inspect exceptions. This is a
hurdle; aliases, generated names and other runtimes remain possible.
"""

EXCEPTION_ATTRIBUTE_HOOK = (
    r"\bdef\s+(?:__getattribute__|__class__|args|errno|filename|name|path|"
    r"__cause__|__context__|__traceback__)\s*\("
)
