# -*- coding: utf-8 -*-

from __future__ import print_function
from __future__ import unicode_literals

import os
import sys

try:
    import __builtin__ as builtins
except ImportError:
    import builtins

try:
    import urlparse
except ImportError:
    import urllib.parse as urlparse

from functools import partial
from pprint import pprint

if sys.version_info >= (3, 0):
    unicode = str


def enable():
    """Enable all the bells and whistles."""
    import __main__

    if sys.version_info < (3, 0):
        readline_init(os.path.expanduser('~/.python2_history'))
    sys.displayhook = displayhook

    try:
        from openerp.models import BaseModel
    except ImportError:
        from odoo.models import BaseModel
    BaseModel._repr_pretty_ = lambda s, p, c: p.text(odoo_repr(s))

    __main__.env = EnvAccess(__main__.session)
    try:
        __main__.res = __main__.env.res
        __main__.hr = __main__.env.hr
        __main__.ir = __main__.env.ir
    except (NameError, AttributeError):
        pass

    __main__.u = UserBrowser(__main__.session)
    __main__.data = DataBrowser(__main__.session)

    __main__.browse = partial(browse, __main__.session)
    __main__.sql = partial(sql, __main__.session)
    __main__.find_data = partial(find_data, __main__.session)
    __main__.resolve_data = partial(resolve_data, __main__.session)


def readline_init(history=None):
    """Set up readline history and completion. Unnecessary in Python 3."""
    import atexit
    import readline
    import rlcompleter  # noqa: F401

    readline.parse_and_bind('tab: complete')
    if readline.get_current_history_length() == 0 and history is not None:
        try:
            readline.read_history_file(history)
        except IOError:
            pass
        atexit.register(lambda: readline.write_history_file(history))


# Terminal escape codes for coloring text
red = '\x1b[1m\x1b[31m{}\x1b[30m\x1b(B\x1b[m'.format
green = '\x1b[1m\x1b[32m{}\x1b[30m\x1b(B\x1b[m'.format
yellow = '\x1b[1m\x1b[33m{}\x1b[30m\x1b(B\x1b[m'.format
blue = '\x1b[1m\x1b[34m{}\x1b[30m\x1b(B\x1b[m'.format
purple = '\x1b[1m\x1b[35m{}\x1b[30m\x1b(B\x1b[m'.format
cyan = '\x1b[1m\x1b[36m{}\x1b[30m\x1b(B\x1b[m'.format


def color_repr(obj, field_type):
    """Return a color-coded representation of an object."""
    if obj is False and field_type != 'Boolean' or obj is None:
        return red(repr(obj))
    elif isinstance(obj, bool):
        return green(repr(obj))
    elif _is_record(obj):
        if len(obj._ids) == 0:
            return red("{}[]".format(obj._name))
        if len(obj._ids) > 10:
            return cyan("{} \N{multiplication sign} {}".format(
                obj._name, len(obj._ids)
            ))
        if obj._name == 'res.users':
            return ', '.join(cyan(user.login) for user in obj)
        return cyan("{}{!r}".format(obj._name, list(obj._ids)))
    elif isinstance(obj, (bytes, unicode)):
        if len(obj) > 120:
            return blue(repr(obj)[:120] + '...')
        return blue(repr(obj))
    elif isinstance(obj, (int, float)):
        return purple(repr(obj))
    else:
        return repr(obj)


field_colors = {
    'one2many': cyan,
    'many2one': cyan,
    'many2many': cyan,
    'char': blue,
    'text': blue,
    'datetime': blue,
    'date': blue,
    'integer': purple,
    'float': purple,
    'id': purple,
    'boolean': green,
}


def field_color(field):
    """Color a field type, if appropriate."""
    if field.relational:
        return "{}: {}".format(green(field.type), cyan(field.comodel_name))
    if field.type in field_colors:
        return field_colors[field.type](field.type)
    return green(field.type)


def _unwrap(obj):
    if isinstance(obj, EnvAccess):
        obj = obj._real
    if not _is_record(obj):
        raise TypeError
    return obj


def odoo_repr(obj):
    obj = _unwrap(obj)

    if len(obj) > 1:
        return '\n\n'.join(odoo_repr(sub) for sub in obj)

    fields = sorted(obj._fields)
    max_len = max(len(f) for f in fields)
    parts = []

    if len(obj) == 0:
        parts.append(yellow(obj._name))
        for field in fields:
            parts.append("{}: ".format(green(field))
                         # Like str.ljust, but not confused about colors
                         + (max_len - len(field)) * ' '
                         + field_color(obj._fields[field])
                         + " ({})".format(obj._fields[field].string))
        return '\n'.join(parts)

    header = "{}[{!r}]".format(obj._name, obj.id)
    if obj.env.uid != 1:
        header += " ({})".format(obj.env.user.login)
    parts.append(yellow(header))

    for field in fields:
        parts.append("{}: ".format(green(field))
                     + (max_len - len(field)) * ' '
                     + color_repr(getattr(obj, field),
                                  obj._fields[field].__class__.__name__))
    return '\n'.join(parts)


if sys.version_info < (3, 3):
    def _get_columns():
        return int(os.popen('stty size').read().split()[1])
else:
    def _get_columns():
        import shutil
        return shutil.get_terminal_size().columns


def displayhook(obj):
    if isinstance(obj, EnvAccess) and obj._real is not None:
        obj = obj._real
    if obj is None:
        return
    if _is_record(obj):
        print(odoo_repr(obj))
    else:
        pprint(obj, width=_get_columns())
    builtins._ = obj


class EnvAccess(object):
    def __init__(self, session, path='', real=None):
        self._session = session
        self._path = path
        self._real = real

    def __getattr__(self, attr):
        if attr.startswith('__'):
            raise AttributeError
        new = self._path + '.' + attr if self._path else attr
        if (hasattr(self._real, attr)
                and new not in self._session.env.registry
                and not any(m.startswith(new + '.')
                            for m in self._session.env.registry)):
            return getattr(self._real, attr)
        if new in self._session.env.registry:
            return EnvAccess(self._session, new, self._session.env[new])
        if any(m.startswith(new + '.') for m in self._session.env.registry):
            return EnvAccess(self._session, new)
        raise AttributeError

    def __dir__(self):
        if not self._path:
            return list({mod.split('.', 1)[0]
                         for mod in self._session.env.registry})
        return dir(self._real) + list({mod[len(self._path)+1:].split('.', 1)[0]
                                       for mod in self._session.env.registry
                                       if mod.startswith(self._path + '.')})

    def __repr__(self):
        if self._real is not None:
            return repr(self._real)
        return "EnvAccess({!r}, {!r})".format(self._session, self._path)

    def __getitem__(self, ind):
        if not self._path:
            return self._session.env[ind]
        return self._real.browse(ind)

    def _ipython_key_completions_(self):
        if not self._path:
            return self._session.env.registry.keys()
        if self._real is None:
            return []
        # IPython doesn't seem to want to display int keys, at least in the
        # versions I tested it, but this can't hurt
        return sql(
            self._session,
            'SELECT id FROM {}'.format(self._session.env[self._path]._table),
        )

    def _search_(self, **kwargs):
        return self._real.search([(k, '=', getattr(v, 'id', v))
                                  for k, v in kwargs.items()])

    @property
    def _mod_(self):
        assert self._real is not None
        return self._session.env['ir.model'].search(
            [('model', '=', self._path)]
        )

    def _repr_pretty_(self, printer, cycle):
        if self._real is not None:
            printer.text(odoo_repr(self._real))
        printer.text(repr(self))


def sql(session, query, *args):
    """Execute a SQL query and try to make the result nicer.

    Optimized for ease of use, at the cost of reliability.
    """
    session.cr.execute(query, *args)
    result = session.cr.fetchall()
    if result and len(result[0]) == 1:
        result = [row[0] for row in result]
    if len(result) == 1:
        result = result[0]
    return result


def browse(session, url):
    """Take a browser form URL and figure out its record."""
    query = urlparse.parse_qs(urlparse.urlparse(url).fragment)
    return session.env[query['model'][0]].browse(int(query['id'][0]))


class UserBrowser(object):
    """Easy access to records of user accounts.

    Usage:
    >>> u.admin
    res.users[1]
    >>> u[1]
    res.users[1]

    >>> u.adm<TAB> completes to u.admin

    >>> record.sudo(u.testemployee1)  # View a record as testemployee1
    """
    def __init__(self, session):
        self._session = session

    def __getattr__(self, attr):
        # IPython does completions in a separate thread.
        # Odoo doesn't like that very much. So completions on attributes of
        # u fail.
        # We can solve that some of the time by remembering things we've
        # completed before.
        user = self._session.env['res.users'].search([('login', '=', attr)])
        setattr(self, attr, user)
        return user

    def __getitem__(self, ind):
        return self._session.env['res.users'].browse(ind)

    def __dir__(self):
        return sql(self._session, 'SELECT login FROM res_users')


def find_data(session, obj):
    ir_model_data = session.env['ir.model.data']
    if isinstance(obj, str):
        if '.' in obj:
            module, name = obj.split('.', 1)
            return ir_model_data.search([('module', '=', module),
                                         ('name', '=', name)])
        return ir_model_data.search([('name', '=', obj)])
    elif _is_record(obj):
        return ir_model_data.search([('model', '=', obj._name),
                                     ('res_id', '=', obj.id)])
    raise TypeError


def resolve_data(session, id):
    entry = find_data(session, id)
    return session.env[entry.model].browse(entry.res_id)


class DataBrowser(object):
    def __init__(self, session):
        self._session = session
        self._cache = None

    def __getattr__(self, attr):
        data = resolve_data(self._session, attr)
        setattr(self, attr, data)
        return data

    def __dir__(self):
        if self._cache is None:
            self._cache = sql(self._session, 'SELECT name FROM ir_model_data')
        return self._cache

    __getitem__ = __getattr__
    _ipython_key_completions_ = __dir__


def _is_record(obj):
    return hasattr(obj, '_ids') and \
        type(obj).__module__ in {'openerp.api', 'odoo.api'}
