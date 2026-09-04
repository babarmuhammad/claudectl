"""Where Claude Code keeps a project's state on disk.

`os.path.join(cfgdir, 'projects', enc)` was written out by hand in fourteen
places across ten modules, and about forty HTTP endpoints reached it through
`gui_api._folder`, which joined a request-supplied `enc` with no normalisation
at all. `enc` is produced by `paths.encode_component`, which maps every
non-alphanumeric character to '-', so a separator or a dot in it means the
value did not come from the encoder — it came from the wire.
"""

import os

from . import config as _c

__all__ = ['projects_root', 'project_folder', 'session_file', 'is_encoded',
           'claudectl_dir']

#: what claudectl writes into a project it does not own
_WORKDIR = '.claudectl'


def claudectl_dir(project_path):
    """`<project>/.claudectl`, created, and marked never-commit.

    Everything claudectl parks in someone else's repository goes here, and some
    of it is sensitive: `bash-log.txt` is every Bash command Claude Code ran in
    that project (`export TOKEN=…`, `curl -H "Authorization: …"`), and
    `injected-context.md` is an entire transcript. claudectl's own repo has the
    directory in `.gitignore`; nobody else's does, so the file landed in users'
    working trees looking like something to commit.

    A `.gitignore` holding `*` ignores the directory's contents including itself,
    which is the whole fix and costs one write the first time.
    """
    d = os.path.join(project_path, _WORKDIR)
    _c._ensure_dir(d)     # one implementation of the marker, see config
    return d


def is_encoded(enc):
    """True if *enc* looks like something `paths.encode_component` produced."""
    return bool(enc) and all(c.isascii() and (c.isalnum() or c == '-') for c in enc)


def projects_root(cfgdir=None):
    return os.path.join(cfgdir or _c.config_dir, 'projects')


def project_folder(cfgdir, enc):
    """<cfgdir>/projects/<enc>. Raises ValueError if *enc* could escape.

    Validating the shape is stricter than a containment check and needs no
    filesystem call: the encoder's whole output alphabet is [A-Za-z0-9-].
    """
    if not is_encoded(enc):
        raise ValueError('not a project folder name: %r' % (enc,))
    return os.path.join(projects_root(cfgdir), enc)


def session_file(cfgdir, enc, sid):
    """The transcript path for one session. *sid* is validated like *enc* —
    it reaches the filesystem from the wire on the same endpoints."""
    if not is_encoded(sid):
        raise ValueError('not a session id: %r' % (sid,))
    return os.path.join(project_folder(cfgdir, enc), sid + '.jsonl')
