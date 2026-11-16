"""Tamper scan pattern for candidate-defined exception attribute hooks.

These hooks can run inside the verifier when trusted observers inspect exceptions. This is a
hurdle; aliases, generated names and other runtimes remain possible. The scan reads one added
line at a time, so it matches a dunder hook definition and a one-line ``name = property(...)``
assignment of an exception attribute. A multi-line ``@property`` over a plain name (``args``,
``errno``, ``filename``, ``name``, ``path``) is not scanned: ordinary classes define those too.
"""

EXCEPTION_ATTRIBUTE_HOOK = (
    r"\bdef\s+(?:__getattribute__|__class__|__cause__|__context__|__traceback__)\s*\("
    r"|\b(?:__class__|args|errno|filename|name|path)\s*=\s*property\s*\("
)
