# -*- coding: utf-8 -*-

from __future__ import print_function
from __future__ import unicode_literals

import importlib
import os
import random
import sys

try:
    import __builtin__ as builtins
except ImportError:
    import builtins

try:
    import urlparse
except ImportError:
    import urllib.parse as urlparse

from datetime import datetime
from functools import partial

PY3 = sys.version_info >= (3, 0)

if PY3:
    unicode = str


FIELD_BLACKLIST = {
    '__last_update',
    'create_date',
    'create_uid',
    'write_date',
    'write_uid',
    'id',
}


def enable(env, module_name='__main__', color=True):
    """Enable all the bells and whistles."""
    try:
        import openerp as odoo
    except ImportError:
        import odoo

    __main__ = importlib.import_module(module_name)

    if sys.version_info < (3, 0):
        readline_init(os.path.expanduser('~/.python2_history'))

    sys.displayhook = displayhook
    odoo.models.BaseModel._repr_pretty_ = _BaseModel_repr_pretty_

    __main__.self = env.user
    __main__.odoo = odoo
    __main__.openerp = odoo

    __main__.browse = partial(browse, env)
    __main__.sql = partial(sql, env)

    __main__.env = EnvProxy(env)
    __main__.u = UserBrowser(env)
    __main__.cfg = ConfigBrowser(env)
    __main__.ref = DataBrowser(env)

    for part in __main__.env._base_parts():
        if not hasattr(__main__, part) and not hasattr(builtins, part):
            setattr(__main__, part, ModelProxy(env, part))

    if not color:
        disable_color()


def disable_color():
    """Disable colored output for model and record summaries."""
    global red, green, yellow, blue, purple, cyan
    red = green = yellow = blue = purple = cyan = lambda s: s


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


def color_repr(owner, field_name):
    """Return a color-coded representation of an object."""
    try:
        obj = getattr(owner, field_name)
    except Exception as err:
        return red(repr(err))
    field_type = owner._fields[field_name].type
    if obj is False and field_type != 'boolean' or obj is None:
        return red(repr(obj))
    elif isinstance(obj, bool):
        # False shows up as green if it's a Boolean, and red if it's a
        # default value, so red values always mean "missing"
        return green(repr(obj))
    elif _is_record(obj):
        if len(obj._ids) == 0:
            return red("{}[]".format(obj._name))
        if len(obj._ids) > 10:
            return cyan(
                "{} \N{multiplication sign} {}".format(
                    obj._name, len(obj._ids)
                )
            )
        if obj._name == 'res.users':
            return ', '.join(cyan('u.' + user.login) for user in obj)
        return cyan("{}{!r}".format(obj._name, list(obj._ids)))
    elif isinstance(obj, (bytes, unicode)):
        if len(obj) > 120:
            return blue(repr(obj)[:120] + '...')
        return blue(repr(obj))
    elif isinstance(obj, datetime):
        # Blue for consistency with versions where they're strings
        return blue(str(obj))
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
    'binary': blue,
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
    if isinstance(obj, ModelProxy):
        obj = obj._real
    if not _is_record(obj):
        raise TypeError
    return obj


def odoo_model_summary(obj):
    """Summarize a model's fields."""
    obj = _unwrap(obj)

    fields = sorted(obj._fields)
    max_len = max(len(f) for f in fields)
    parts = []

    parts.append(yellow(obj._name))
    for field in fields:
        if field in FIELD_BLACKLIST:
            continue
        parts.append(
            "{}: ".format(green(field))
            # Like str.ljust, but not confused about colors
            + (max_len - len(field)) * ' '
            + field_color(obj._fields[field])
            + " ({})".format(obj._fields[field].string)
        )
    return '\n'.join(parts)


def odoo_repr(obj):
    """Display all of a record's fields."""
    obj = _unwrap(obj)

    if len(obj) > 3:
        return "{}[{}]".format(obj._name, ', '.join(map(str, obj._ids)))
    elif len(obj) > 1:
        return '\n\n'.join(odoo_repr(sub) for sub in obj)
    elif len(obj) == 0:
        return "{}[]".format(obj._name)

    fields = sorted(obj._fields)
    max_len = max(len(f) for f in fields)
    parts = []

    header = yellow("{}[{!r}]".format(obj._name, obj.id))
    data = find_data(obj.env, obj)
    for data_record in data:
        header += " (ref.{}.{})".format(data_record.module, data_record.name)
    if obj.env.uid != 1:
        header += " (as u.{})".format(obj.env.user.login)
    parts.append(header)

    if not obj.exists():
        parts.append(red("Missing"))
        return '\n'.join(parts)

    for field in fields:
        if field in FIELD_BLACKLIST:
            continue
        parts.append(
            "{}: ".format(green(field))
            + (max_len - len(field)) * ' '
            + color_repr(obj, field)
        )
    return '\n'.join(parts)


def _BaseModel_repr_pretty_(self, printer, cycle):
    if printer.indentation == 0 and hasattr(self, '_ids'):
        printer.text(odoo_repr(self))
    else:
        printer.text(repr(self))


def oprint(obj):
    """Display all records in a set, even if there are a lot."""
    print('\n\n'.join(odoo_repr(record) for record in obj))


def displayhook(obj):
    """A sys.displayhook replacement that pretty-prints models and records."""
    if isinstance(obj, ModelProxy) and obj._real is not None:
        print(odoo_model_summary(obj._real))
        builtins._ = obj
    elif _is_record(obj):
        print(odoo_repr(obj))
        builtins._ = obj
    else:
        sys.__displayhook__(obj)


class EnvProxy(object):
    """A wrapper around an odoo.api.Environment object.

    Models returned by indexing will be wrapped in a ModelProxy for nicer
    behavior. Models can also be accessed as attributes, with tab completion.
    """

    def __init__(self, env):
        self._env = env

    def __getattr__(self, attr):
        if attr.startswith('__'):
            raise AttributeError
        if hasattr(self._env, attr):
            return getattr(self._env, attr)
        if attr in self._base_parts():
            return ModelProxy(self._env, attr)
        raise AttributeError

    def __dir__(self):
        listing = set(super().__dir__()) if PY3 else {'_base_parts'}
        listing.update(self._base_parts())
        listing.update(
            attr for attr in dir(self._env) if not attr.startswith('__')
        )
        return sorted(listing)

    def _base_parts(self):
        return list({mod.split('.', 1)[0] for mod in self._env.registry})

    def __repr__(self):
        return "{}({!r})".format(self.__class__.__name__, self._env)

    def __getitem__(self, ind):
        if ind not in self._env.registry:
            raise IndexError("Model '{}' does not exist".format(ind))
        return ModelProxy(self._env, ind)

    def _ipython_key_completions_(self):
        return self._env.registry.keys()


class ModelProxy(object):
    """A wrapper around an Odoo model.

    Records can be browsed with indexing syntax, other models can be used
    with tab-completed attribute access, there are added convenience methods,
    and instead of an ordinary repr a summary of the fields is shown.
    """

    def __init__(self, env, path):
        self._env = env
        self._path = path
        self._real = env[path] if path in env.registry else None

    def __getattr__(self, attr):
        if attr.startswith('__'):
            raise AttributeError
        new = self._path + '.' + attr
        if new in self._env.registry:
            return self.__class__(self._env, new)
        if any(m.startswith(new + '.') for m in self._env.registry):
            return self.__class__(self._env, new)
        if self._real is None:
            raise AttributeError("Model '{}' does not exist".format(new))
        return getattr(self._real, attr)

    def __dir__(self):
        listing = set(super().__dir__()) if PY3 else set()
        if self._real is not None:
            listing.update(
                attr for attr in dir(self._real) if not attr.startswith('__')
            )
        listing.update(
            mod[len(self._path) + 1 :].split('.', 1)[0]
            for mod in self._env.registry
            if mod.startswith(self._path + '.')
        )
        return sorted(listing)

    def __repr__(self):
        return "<{}({})>".format(self.__class__.__name__, self._path)

    def _repr_pretty_(self, printer, cycle):
        if self._real is not None and printer.indentation == 0:
            printer.text(odoo_model_summary(self._real))
        else:
            printer.text(repr(self))

    def __getitem__(self, ind):
        if self._real is None:
            return IndexError("Model '{}' does not exist".format(self._path))
        if not ind:
            return self._real
        if isinstance(ind, (list, set)):
            ind = tuple(ind)
        if not isinstance(ind, tuple):
            ind = (ind,)
        # Browsing a non-existent record can cause weird caching problems, so
        # check first
        real_ind = set(
            sql(
                self._env,
                'SELECT id FROM "{}" WHERE id IN %s'.format(self._real._table),
                ind,
            )
        )
        missing = set(ind) - real_ind
        if missing:
            raise IndexError(
                "Records {} do not exist".format(', '.join(map(str, missing)))
            )
        return self._real.browse(ind)

    def _ensure_real(self):
        if self._real is None:
            raise TypeError("Model '{}' does not exist".format(self._path))

    def search(
        self, args=(), offset=0, limit=None, order='id', count=False, **kwargs
    ):
        """Perform a quick and dirty search.

        .search(x='test', y=<some record>) is roughly equivalent to
        .search([('x', '=', 'test'), ('y', '=', <some record>.id)]).
        .search() gets all records.
        """
        self._ensure_real()
        args = list(args)
        # TODO: inspect fields
        args.extend((k, '=', getattr(v, 'id', v)) for k, v in kwargs.items())
        return self._real.search(
            args, offset=offset, limit=limit, order=order, count=count
        )

    def create(self, vals=(), **kwargs):
        """Create a new record, optionally with keyword arguments."""
        self._ensure_real()
        kwargs.update(vals)
        for key, value in kwargs.items():
            if key not in self._real._fields:
                raise TypeError("Field '{}' does not exist".format(key))
            if _is_record(value) or (
                isinstance(value, (list, tuple))
                and value
                and _is_record(value[0])
            ):
                # TODO: typecheck model
                field_type = self._real._fields[key].type
                if field_type.endswith('2many'):
                    kwargs[key] = [(4, record.id) for record in value]
                elif field_type.endswith('2one'):
                    if len(value) > 1:
                        raise TypeError(
                            "Can't link multiple records for '{}'".format(key)
                        )
                    kwargs[key] = value.id
        return self._real.create(kwargs)

    def _all_ids_(self):
        """Get all record IDs in the database."""
        self._ensure_real()
        return sql(
            self._env, 'SELECT id FROM {}'.format(self._env[self._path]._table)
        )

    def _mod_(self):
        """Get the ir.model record of the model."""
        self._ensure_real()
        return self._env['ir.model'].search([('model', '=', self._path)])

    def _shuf_(self, num=1):
        """Return a random record, or multiple."""
        self._ensure_real()
        return self._real.browse(random.sample(self._all_ids_(), num))


def sql(env, query, *args):
    """Execute a SQL query and try to make the result nicer.

    Optimized for ease of use, at the cost of performance and boringness.
    """
    env.cr.execute(query, args)
    result = env.cr.fetchall()
    if result and len(result[0]) == 1:
        result = [row[0] for row in result]
    return result


def browse(env, url):
    """Take a browser form URL and figure out its record."""
    # TODO: handle other views more intelligently
    #       perhaps based on the user?
    query = urlparse.parse_qs(urlparse.urlparse(url).fragment)
    return env[query['model'][0]].browse(int(query['id'][0]))


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

    def __init__(self, env):
        self._env = env

    def __getattr__(self, attr):
        # IPython does completions in a separate thread.
        # Odoo doesn't like that. So completions on attributes of `u` fail.
        # We can solve that sometimes by remembering things we've completed
        # before.
        user = self._env['res.users'].search([('login', '=', attr)])
        if not user:
            raise AttributeError("User '{}' not found".format(attr))
        setattr(self, attr, user)
        return user

    def __dir__(self):
        return sql(self._env, 'SELECT login FROM res_users')

    __getitem__ = __getattr__
    _ipython_key_completions_ = __dir__


class DataBrowser(object):
    """Easy access to data records by their XML IDs.

    Usage:
    >>> ref.base.user_root
    res.users[1]
    >>> ref('base.user_root')
    res.users[1]

    The attribute access has tab completion.
    """

    def __init__(self, env):
        self._env = env

    def __getattr__(self, attr):
        if not sql(
            self._env,
            'SELECT id FROM ir_model_data WHERE module = %s LIMIT 1',
            attr,
        ):
            raise AttributeError("No module '{}'".format(attr))
        browser = DataModuleBrowser(self._env, attr)
        setattr(self, attr, browser)
        return browser

    def __dir__(self):
        return sql(self._env, 'SELECT DISTINCT module FROM ir_model_data')

    def __call__(self, query):
        return self._env.ref(query)


class DataModuleBrowser(object):
    """Access data records within a module. Created by DataBrowser."""

    def __init__(self, env, module):
        self._env = env
        self._module = module

    def __getattr__(self, attr):
        try:
            record = self._env.ref("{}.{}".format(self._module, attr))
        except ValueError as err:
            raise AttributeError(err)
        setattr(self, attr, record)
        return record

    def __dir__(self):
        return sql(
            self._env,
            'SELECT name FROM ir_model_data WHERE module = %s',
            self._module,
        )


def find_data(env, obj):
    """Find the ir.model.data record for a record or an ID."""
    ir_model_data = env['ir.model.data']
    if isinstance(obj, str):
        if '.' in obj:
            return env.ref(obj)
        return ir_model_data.search([('name', '=', obj)])
    elif _is_record(obj):
        return ir_model_data.search(
            [('model', '=', obj._name), ('res_id', '=', obj.id)]
        )
    raise TypeError


def _is_record(obj):
    """Return whether an object is an Odoo record."""
    # This has to work without importing BaseModel
    return hasattr(obj, '_ids') and type(obj).__module__ in {
        'openerp.api',
        'odoo.api',
    }


class ConfigBrowser(object):
    """Access ir.config.parameter entries as attributes."""

    def __init__(self, env, path=''):
        self._env = env
        self._path = path

    def __repr__(self):
        real = self._env['ir.config_parameter'].get_param(self._path)
        if real is False:
            return "<{}({})>".format(self.__class__.__name__, self._path)
        return repr(real)

    def __str__(self):
        return self._env['ir.config_parameter'].get_param(self._path)

    def __getattr__(self, attr):
        new = self._path + '.' + attr if self._path else attr
        if self._env['ir.config_parameter'].search(
            [('key', '=like', new + '.%')], limit=1
        ):
            result = ConfigBrowser(self._env, new)
            setattr(self, attr, result)
            return result
        real = self._env['ir.config_parameter'].get_param(new)
        if real is not False:
            setattr(self, attr, real)
            return real
        raise AttributeError("No config parameter '{}'".format(attr))

    def __dir__(self):
        if not self._path:
            return self._env['ir.config_parameter'].search([]).mapped('key')
        return list(
            {
                result[len(self._path) + 1 :]
                for result in self._env['ir.config_parameter']
                .search([('key', '=like', self._path + '.%')])
                .mapped('key')
            }
        )
