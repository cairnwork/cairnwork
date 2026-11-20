"""tamper_scan: the sys.modules stub patterns (literal keys that name no sensitive module)."""

from __future__ import annotations

# sys.modules entries a forgery would swap; a literal key naming any other module is a test stub.
_SENSITIVE_MODULE = (
    r"(?:_?pytest\w*|pluggy|py|_?cairnwork\w*|sys|os|builtins|importlib|gc|conftest|__main__)"
)
# Test code by module name: a test root package, a conftest or a test module, at any depth.
_TEST_MODULE = r"(?:\w+\.)*(?:tests|test|testing|conftest|test_\w*|\w+_test)"
_SENSITIVE_NAME = rf"(?:{_SENSITIVE_MODULE}|{_TEST_MODULE})(?:\.[\w.]*)?"
_SENSITIVE_KEY = rf"(?:'{_SENSITIVE_NAME}'|\"{_SENSITIVE_NAME}\")"
_STUB_NAME = (
    rf"(?:'(?!{_SENSITIVE_MODULE}(?:\.|')|{_TEST_MODULE}(?:\.|'))[\w.]+'"
    rf"|\"(?!{_SENSITIVE_MODULE}(?:\.|\")|{_TEST_MODULE}(?:\.|\"))[\w.]+\")"
)
_STUB_KEY = rf"\[\s*{_STUB_NAME}\s*\]"
_STUB_RHS = r"(?:(?!\bmodules\b)[^;])*"  # no second sys.modules access chained on the line
# One statement on the line: assign, delete, pop or setdefault one literal stub key.
_SYS_MODULES_STUB = (
    rf"^\s*(?:sys\s*\.\s*modules\s*{_STUB_KEY}\s*=(?!=){_STUB_RHS}"
    rf"|del\s+sys\s*\.\s*modules\s*{_STUB_KEY}"
    rf"|sys\s*\.\s*modules\s*\.\s*pop\s*\(\s*{_STUB_NAME}\s*(?:,\s*None\s*)?\)"
    rf"|sys\s*\.\s*modules\s*\.\s*setdefault\s*\(\s*{_STUB_NAME}\s*,{_STUB_RHS}\))\s*(?:#.*)?$"
)
