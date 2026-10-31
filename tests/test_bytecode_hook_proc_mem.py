"""Linux: the bytecode hook refuses the process memory files (bytecode_hook.py, Issue #276).

Code in the verifier's pytest process could otherwise write the hook's own objects through
``/proc/<pid>/mem``. Each spelling below runs in a child Python with the hook, as in
test_bytecode_hook.py, and must be refused with the hook still live afterwards. Linux only; CI's
test-linux-sandbox job runs this file as well.
"""

import sys

import pytest
from test_bytecode_hook import run_case

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="/proc/<pid>/mem is Linux only")

SPELLINGS = {
    "self": "def step():\n    open('/proc/self/mem', 'rb+')\n",
    "thread_self": "def step():\n    open('/proc/thread-self/mem', 'rb')\n",
    "own_pid": "def step():\n    os.open(f'/proc/{os.getpid()}/mem', os.O_RDWR)\n",
    "task": (
        "import threading\n"
        "def step():\n"
        "    open(f'/proc/self/task/{threading.get_native_id()}/mem', 'rb+')\n"
    ),
    "through_a_directory_link": (
        "def step():\n"
        "    link = os.path.join(sys.argv[2], 'p')\n"
        "    os.symlink('/proc/self', link)\n"
        "    open(os.path.join(link, 'mem'), 'rb+')\n"
    ),
    "link_to_the_file": (
        "def step():\n    os.symlink('/proc/self/mem', os.path.join(sys.argv[2], 'm'))\n"
    ),
    "relative_to_a_descriptor": (
        "def step():\n"
        "    fd = os.open('/proc/self', os.O_RDONLY)\n"
        "    os.open('mem', os.O_RDWR, dir_fd=fd)\n"
    ),
    "bytes_path": "def step():\n    open(b'/proc/self/mem', 'rb+')\n",
    "path_object": "def step():\n    pathlib.Path('/proc/self/mem').open('rb+')\n",
}


@pytest.mark.parametrize("how", sorted(SPELLINGS))
def test_the_process_memory_file_is_refused(tmp_path, how):
    outcome, state = run_case(sys.executable, tmp_path, SPELLINGS[how])
    assert outcome == "refused:PermissionError" and state == "live", (how, outcome)
