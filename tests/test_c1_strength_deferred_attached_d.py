"""Direct strength tests for deferred attached-test AST recognition."""

import ast

import pytest

from cairnwork.deferred_attached import attached


def _attached(source: str):
    return attached(ast.parse(source))


def _is_attaching(source: str) -> bool:
    digests, blind = _attached(source)
    return bool(digests) or blind


@pytest.mark.parametrize(
    "source",
    [
        "class Helper: pass\nHelper.test_dynamic = lambda: None\n",
        "class Helper: pass\nsetattr(Helper, 'test_dynamic', lambda: None)\n",
        "class Helper: pass\nHelper.__setattr__('test_dynamic', lambda: None)\n",
        "class Helper: pass\nobject.__setattr__(Helper, 'test_dynamic', lambda: None)\n",
        "class Helper: pass\nvars(Helper).update({'test_dynamic': lambda: None})\n",
        "class Helper: pass\nHelper.__dict__['test_dynamic'] = lambda: None\n",
        "def helper(): pass\nclass Holder: pass\nHolder.test_dynamic = helper\n",
    ],
    ids=[
        "attribute",
        "setattr",
        "bound-setattr",
        "object-setattr",
        "namespace-update",
        "dict-subscript",
        "same-file-name",
    ],
)
def test_dynamic_test_attachments_are_detected(source):
    assert _is_attaching(source)


def test_self_attribute_and_literal_test_data_are_not_attaching():
    assert not _is_attaching(
        "class Helper:\n"
        "    def setup(self): self.test_client = make_client()\n"
        "test_data = load_data()\n"
    )


def test_attached_digest_tracks_attached_name_and_value():
    plain = ast.parse("class Helper: pass\nHelper.test_alpha = lambda: None\n")
    changed = ast.parse("class Helper: pass\nHelper.test_beta = lambda: None\n")
    assert attached(plain) != attached(changed)
