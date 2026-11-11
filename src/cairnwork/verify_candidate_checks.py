"""Small pre-burn checks for the external candidate verification entry point."""

from cairnwork.import_path_tree import ImportPathError, require_plain_dirs
from cairnwork.issue_task import check_verify_import_paths
from cairnwork.pipeline import TaskRejected, check_task
from cairnwork_protocol.ids import ID_RULE, core_ids


def require_core_ids(verifiers):
    """Require verifier ids accepted by Core before anything runs (#490)."""
    if not core_ids([vid for vid, _ in verifiers]):
        raise TaskRejected("BAD_VERIFIER", f"verifier {ID_RULE}")


def check_task_for_verify(task, python):
    """Translate verifier setup failures to explicit refusals before any tests are burned."""
    try:
        return check_task(task, verifier_python=python)
    except OSError as exc:
        raise TaskRejected("VERIFIER_ENVIRONMENT_ERROR", str(exc)) from exc


def validate_import_paths(repo, base, import_paths, test_roots):
    """Validate caller-provided import paths before task construction."""
    try:
        check_verify_import_paths(list(import_paths), test_roots)
        if import_paths:
            require_plain_dirs(repo, base, import_paths)
    except (ValueError, ImportPathError) as exc:
        raise TaskRejected("BAD_IMPORT_PATH", str(exc)) from exc
