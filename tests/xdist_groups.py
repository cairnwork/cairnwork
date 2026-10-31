"""Test files that share state with other files at run time (#406).

Under pytest-xdist ``--dist loadgroup`` the files of one group run on one worker, one after
another; every other file runs in parallel (conftest.pytest_itemcollected adds the marks).
Step 3 of #406 gives a group's files their own state and deletes them here.
"""

XDIST_GROUPS = {
    # Every make_runs() run root is named local_worker_fakes.TAG, and the wrapper's sweep kills
    # each process of this uid whose environment carries that tag: one test's sweep kills the
    # wrapper processes of a test running at the same time on another worker.
    "local_worker_run_tag": (
        "test_local_worker_gitquery.py",
        "test_local_worker_ignore_rules.py",
        "test_local_worker_paging.py",
        "test_local_worker_patch_count.py",
        "test_local_worker_paths.py",
        "test_local_worker_query_dir.py",
        "test_local_worker_readonly.py",
        "test_local_worker_search.py",
        "test_local_worker_sweep_scope.py",
        "test_local_worker_unseen.py",
        "test_local_worker_untracked_allow_cli.py",
        "test_local_worker_wrapper.py",
        "test_local_worker_write_scope.py",
        "test_local_worker_write_scope_links.py",
        "test_pause_guard.py",
    ),
}
XDIST_GROUP_OF = {name: group for group, names in XDIST_GROUPS.items() for name in names}
