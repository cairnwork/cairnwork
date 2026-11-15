"""Tamper scan pattern for candidate-defined exception attribute hooks.

These hooks can run inside the verifier when trusted observers inspect exceptions. This is a
hurdle; aliases, generated names and other runtimes remain possible. Dunder hooks match anywhere;
the plain attribute names only as a property, so an ordinary ``def path(...)`` is not flagged.
"""

EXCEPTION_ATTRIBUTE_HOOK = (
    r"\bdef\s+(?:__getattribute__|__class__|__cause__|__context__|__traceback__)\s*\("
    r"|@property\s*\n\s*def\s+(?:args|errno|filename|name|path)\s*\("
)
