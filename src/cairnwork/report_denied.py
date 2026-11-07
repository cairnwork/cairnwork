"""Part of the report_guard plugin source kept out of report_guard.py (at the 400-line cap, #962
step 2-0): _denied_inside, a pure move. Plugin text, not parent code: the plugin cannot import
cairnwork. report_guard concatenates it where it was, so the installed plugin is byte-identical."""

DENIED_SOURCE = '''\
def _denied_inside(exc, _codes=(errno.EACCES, errno.EPERM, errno.EROFS, errno.ENETUNREACH),
                   _lookup=socket.gaierror):
    """#854: a denial-shaped OSError wrapped in a failure (group member, cause, unsuppressed
    context), or #932 a failed host-name lookup (a closed network); past the bound, yes (fail
    closed). Other wrapped OSErrors stay plain failures."""
    todo, seen = [exc], set()
    while todo:
        if not isinstance(cur := todo.pop(), BaseException) or id(cur) in seen:
            continue
        if len(seen) >= 64:  # the bound: a 65th distinct exception, not an empty cause slot
            return True
        seen.add(id(cur))
        if cur is not exc and (isinstance(cur, _lookup) or isinstance(cur, OSError)
                               and cur.errno in _codes):  # #932: a lookup's errno is EAI_*
            return True
        members = getattr(cur, "exceptions", None)  # BaseExceptionGroup, or the backport's
        todo.extend(members if isinstance(members, tuple) else ())
        todo.append(cur.__cause__)
        if not cur.__suppress_context__:
            todo.append(cur.__context__)
    return False


'''
