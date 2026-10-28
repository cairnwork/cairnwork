"""EXPERIMENTAL (default off, not imported by core): the cross-family gate (#374).

A queued Issue may declare, in a ```cairnwork-families block, which model wrote it and which
wrote its deferred tests. With the gate on, an Issue whose worker would be of the same model
family as either author is refused before the Issue runner takes it (``families`` holds the
fixed family table and the verdict, ``gate`` the pass and its sidecar record). The families are
what the Issue author declares, an experiment label and not a safety guarantee.
CLI: ``python -m experimental.family_gate``. See OWNER / EXPIRES.
"""
