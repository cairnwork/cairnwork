"""Part of the report_guard plugin source kept out of report_guard.py (at the 400-line cap, #962
step 2-0): _denied_inside, a pure move. Plugin text, not parent code: the plugin cannot import
cairnwork. report_guard concatenates it where it was, so the installed plugin is byte-identical."""

DENIED_SOURCE = '''\
def _denied_inside(exc, _codes=(errno.EACCES, errno.EPERM, errno.EROFS, errno.ENETUNREACH),
                   _lookup=socket.gaierror, _type=type, _sub=issubclass, _base=BaseException,
                   _cause=BaseException.__cause__.__get__,
                   _context=BaseException.__context__.__get__,
                   _quiet=BaseException.__suppress_context__.__get__,
                   _errno=OSError.errno.__get__, _members=BaseExceptionGroup.exceptions.__get__):
    """#854: a denial-shaped OSError wrapped in a failure (group member, cause, unsuppressed
    context), or #932 a failed host-name lookup (a closed network); past the bound, yes (fail
    closed). Other wrapped OSErrors stay plain failures."""
    todo, seen = [exc], set()
    while todo:
        if not _sub(_type(cur := todo.pop()), _base) or id(cur) in seen:
            continue
        if len(seen) >= 64:  # the bound: a 65th distinct exception, not an empty cause slot
            return True
        seen.add(id(cur))
        error_no = _errno(cur) if _sub(_type(cur), OSError) else None
        if cur is not exc and (_sub(_type(cur), _lookup)
                               or _type(error_no) is int and error_no in _codes):
            # #932: a lookup's errno is EAI_*
            return True
        members = _members(cur) if _sub(_type(cur), BaseExceptionGroup) else ()
        if _type(members) is tuple:
            if len(members) > 64:  # fail closed, as the bound above does
                return True
            todo.extend(members)
        todo.append(_cause(cur))
        if not _quiet(cur):
            todo.append(_context(cur))
    return False


'''
