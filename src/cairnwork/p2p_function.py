"""Helpers for interpreting pytest node ids in P2P collection."""


def _function(nodeid: str) -> str:
    path, separator, test_id = nodeid.partition("::")
    if not separator:
        return nodeid
    parts = []
    start = depth = 0
    index = 0
    while index < len(test_id):
        char = test_id[index]
        if char == "[":
            depth += 1
        elif char == "]":
            depth = max(0, depth - 1)
        elif test_id.startswith("::", index) and depth == 0:
            parts.append(test_id[start:index])
            start = index + 2
            index += 1
        index += 1
    parts.append(test_id[start:])
    return "::".join([path, *(part.split("[", 1)[0] for part in parts)])
