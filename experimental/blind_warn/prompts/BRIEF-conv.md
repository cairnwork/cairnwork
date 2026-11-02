You read a repository to find its existing conventions for one bug ticket. Your working directory is {D}. It holds:
- ISSUE.md: the bug ticket;
- src/: the repository source at the buggy base (read-only; no later history).
Read only files in {D}. You cannot run code.

Find, in src/, the existing tests, docstrings and documentation (README, docs/, CHANGES, comments) that are related to the
failure in ISSUE.md: the functions, classes or options it touches and their neighbours. From them, write a "repo convention
summary": for each of these aspects, what this repository already does in comparable code, with the source (file path and
test or function name, plus a short quote):
- empty / None values; input types (str, bytes, other types); letter case; ranges and boundaries;
- error handling (raise which exception type, drop silently, return None or a default, warn); output order and wording;
- any other edge behaviour the ticket's expected behaviour depends on.
For an aspect where the repository gives no basis, write "no basis". Do not guess how the bug should be fixed, do not
propose code, and do not decide behaviour the repository does not show.

Write work/conventions.md (Markdown, under 80 lines): one bullet per aspect: `- <aspect>: <what the repo does> (source: <path>::<name>, "<short quote>")` or `- <aspect>: no basis`.
Then reply with one line: OK.
