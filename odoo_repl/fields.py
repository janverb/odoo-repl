from __future__ import print_function

import inspect
import pprint
import re
import string
import types

from odoo_repl import color
from odoo_repl import fzf
from odoo_repl import gitsources
from odoo_repl import methods
from odoo_repl import sources
from odoo_repl import util
from odoo_repl.imports import t, odoo, Field, BaseModel, PY3, Text, cast


class FieldProxy(object):
    def __init__(self, env, field):
        # type: (odoo.api.Environment, Field) -> None
        self._env = env
        self._real = field

    def __getattr__(self, attr):
        # type: (str) -> object
        if attr.startswith("__"):
            raise AttributeError
        return getattr(self._real, attr)

    def __dir__(self):
        # type: () -> t.List[str]
        if PY3:
            listing = set(super().__dir__())
        else:
            listing = {"source_", "gitsource_", "fzf_"}
        listing.update(dir(self._real))
        return sorted(listing)

    def __repr__(self):
        # type: () -> str
        if not hasattr(self._real, "model_name"):
            return "<Undisplayable field>"  # Work around Odoo bug
        return "<{}({!r})>".format(self.__class__.__name__, self._real)

    def _repr_pretty_(self, printer, _cycle):
        # type: (t.Any, t.Any) -> None
        if printer.indentation == 0 and hasattr(self._real, "model_name"):
            printer.text(field_repr(self._real, env=self._env))
        else:
            printer.text(repr(self))

    def source_(self, location=None):
        # type: (t.Optional[t.Text]) -> None
        first = True
        for source in sources.find_source(self._real):
            if location is not None and location != source.module:
                continue
            if source.lnum is None:
                print(
                    "This field is defined somewhere in {!r} "
                    "but we don't know where".format(source.fname)
                )
                continue
            if not first:
                print()
            else:
                first = False
            print(sources.format_source(source))
            print(
                color.highlight(sources.extract_field_source(source.fname, source.lnum))
            )

    def gitsource_(self):
        # type: () -> None
        gitsources.gitsource(self._real)

    def fzf_(self):
        # type: () -> t.Optional[BaseModel]
        return fzf.fzf_field(self._env[self._real.model_name], self._real.name)

    def _make_method_proxy_(self, func):
        # type: (object) ->  object
        if not callable(func):
            return func
        name = getattr(func, "__name__", False)
        if not name:
            return func
        model = self._env[self._real.model_name]
        if hasattr(model, name):
            get = getattr(func, "__get__", False)
            if get:
                func = get(model)
            if not callable(func):
                return func
            return methods.MethodProxy(func, model, name)
        return func

    @property
    def compute(self):
        # type: () -> object
        return self._make_method_proxy_(_find_computer(self._real, self._env))

    @property
    def default(self):
        # type: () -> object
        return self._make_method_proxy_(
            _find_field_default(self._real, self._env[self._real.model_name])
        )


def _name_func(function):
    # type: (object) -> t.Text
    return str(getattr(function, "__name__", function))


def _format_func(function):
    # type: (object) -> t.Text
    if getattr(function, "__name__", None) == "<lambda>":
        assert isinstance(function, types.FunctionType)
        try:
            return color.purple.bold(_decipher_lambda(function))
        except Exception:
            pass
    return color.method(_name_func(function))


def _format_maybe_func(obj):
    # type: (object) -> t.Text
    if callable(obj):
        return _format_func(obj)
    return repr(obj)


def field_repr(field, env=None):
    # type: (t.Union[FieldProxy, Field], t.Optional[odoo.api.Environment]) -> t.Text
    """List detailed information about a field."""
    # TODO:
    # - .groups, .copy, .states, .inverse, .column[12]
    if env is None:
        env = util.env
    field = util.unwrap(field)
    model = env[field.model_name]

    parts = []  # type: t.List[t.Text]

    # We mainly look this up just to warn when it doesn't exist
    record = env["ir.model.fields"].search(
        [("model", "=", field.model_name), ("name", "=", field.name)]
    )
    if len(record) > 1:
        # This is rare, but apparently valid
        # TODO: pick intelligently based on MRO
        record = record[-1]
    elif not record:
        parts.append(color.missing("No ir.model.fields record found for field"))

    parts.append(
        "{} {} on {}".format(
            color.blue.bold(field.type),
            color.field(field.name),
            color.model(field.model_name),
        )
    )
    if field.comodel_name:
        parts[-1] += " to {}".format(color.model(field.comodel_name))

    properties = _find_field_attrs(field)
    if properties:
        parts[-1] += " ({})".format(", ".join(properties))

    has_auto = has_auto_string(field)
    if not has_auto:
        parts.append(field.string)

    if field.help:
        if has_auto or "\n" in field.help:
            parts.append(field.help)
        else:
            parts[-1] += ": " + field.help

    related = _find_field_delegated(field)
    if related:
        parts.append("Delegated to {}".format(color.field(related)))
    else:
        func = _find_computer(field, env)
        if func:
            parts.append("Computed by {}".format(_format_func(func)))

    for inverse in _find_inverse_names(field, env):
        parts.append("Inverts {}".format(color.field(inverse)))

    if field.default:
        parts.append(
            "Default value: {}".format(
                _format_maybe_func(_find_field_default(field, model))
            )
        )

    if isinstance(field, odoo.fields.Selection):
        parts.append(_format_selection_values(field, model))

    for constrainer in _find_constraint_methods(field, model):
        parts.append("Constrained by {}".format(_format_func(constrainer)))

    for onchange in _find_onchange_methods(field, model):
        parts.append("On change: {}".format(_format_func(onchange)))

    src = sources.find_source(field)
    parts.extend(sources.format_sources(src))

    if not src and record and record.modules:
        # In newer Odoo versions we could check field._modules instead
        parts.append(
            "Defined in module {}".format(
                ", ".join(color.module(module) for module in record.modules.split(", "))
            )
        )

    return "\n".join(parts)


def _find_field_attrs(field):
    # type: (Field) -> t.List[str]
    # TODO: required can be a callable, others perhaps too
    # Maybe check for a callable/string and add a question mark if so?
    return [
        attr
        for attr in (
            "readonly",
            "required",
            "store",
            "index",
            "auto_join",
            "compute_sudo",
            "related_sudo",
            "translate",
        )
        if getattr(field, attr, False)
    ]


def _find_computer(field, env):
    # type: (Field, odoo.api.Environment) -> object
    if field.compute is not None:
        func = field.compute
        func = getattr(func, "__func__", func)
        if isinstance(func, Text):
            func = getattr(env[field.model_name], func)
        return func
    elif type(getattr(field, "column", None)).__name__ == "function":
        return field.column._fnct
    return None


def _decipher_lambda(func):
    # type: (types.FunctionType) -> t.Text
    """Try to retrieve a lambda's source code. Very nasty.

    Signals failure by throwing random exceptions.
    """
    source = inspect.getsource(func)
    source = re.sub(r" *\n *", " ", source).strip()
    match = re.search("lambda [^:]*:.*", source)
    if not match:
        raise RuntimeError
    source = match.group().strip()
    try:
        compile(source, "", "eval")
    except SyntaxError as err:
        assert err.offset is not None
        source = source[: err.offset - 1].strip()
        if re.search(r",[^)]*$", source):
            source = source.rsplit(",")[0].strip()
        compile(source, "", "eval")
    # A trailing comma is normally outside the lambda
    source = source.strip(string.whitespace + ",")
    return source


def _find_field_delegated(field):
    # type: (Field) -> t.Optional[t.Text]
    if field.related:
        return ".".join(field.related)
    elif getattr(field, "column", False) and type(field.column).__name__ == "related":
        return ".".join(field.column.arg)  # type: ignore
    else:
        return None


def _find_field_default(field, model):
    # type: (Field, BaseModel) -> object
    default = field.default
    if (
        getattr(default, "__module__", None)
        in {"odoo.api", "odoo.fields", "openerp.api", "openerp.fields"}
        and hasattr(model, "_defaults")
        and field.name in model._defaults
    ):
        default = model._defaults[field.name]

    try:
        # Very nasty but works some of the time
        # Hopefully something better exists
        if (
            isinstance(default, types.FunctionType)
            and default.__module__ in {"odoo.fields", "openerp.fields"}
            and default.__name__ == "<lambda>"
            and "value" in default.__code__.co_freevars
            and default.__closure__
        ):
            default = default.__closure__[
                default.__code__.co_freevars.index("value")
            ].cell_contents
    except Exception:
        pass

    return default


def _find_constraint_methods(field, model):
    # type: (Field, BaseModel) -> t.Iterable[object]
    for constrainer in getattr(model, "_constraint_methods", ()):
        if field.name in constrainer._constrains:
            yield constrainer


def _find_onchange_methods(field, model):
    # type: (Field, BaseModel) -> t.Iterable[object]
    return model._onchange_methods.get(field.name, ())


def _find_inverse_names(field, env):
    # type: (Field, odoo.api.Environment) -> t.Set[str]
    inverse_names = set()  # type: t.Set[str]
    if not field.relational:
        return inverse_names
    inverse_names.update(inv.name for inv in getattr(field, "inverse_fields", ()))
    inverse_name = getattr(field, "inverse_name", False)
    table = getattr(field, "relation", None)
    if inverse_name:
        inverse_names.add(inverse_name)
    if field.comodel_name:
        for other_field in env[field.comodel_name]._fields.values():
            if (
                other_field.comodel_name == field.model_name
                and getattr(other_field, "inverse_name", False) == field.name
                or table
                and getattr(other_field, "relation", None) == table
            ):
                inverse_names.add(other_field.name)
    return inverse_names


def _format_selection_values(field, model):
    # type: (odoo.fields.Selection[t.Any], BaseModel) -> t.Text
    if field.related:
        field = cast(
            "odoo.fields.Selection[t.Any]",
            model.env[model._fields[field.related[0]].comodel_name]._fields[
                field.related[1]
            ],
        )
    if isinstance(field.selection, Text):
        return u"Values computed by {}".format(color.method(field.selection))
    elif callable(field.selection):
        return u"Values computed by {}".format(color.method(repr(field.selection)))
    else:
        # Most likely a list of 2-tuples
        return color.highlight(pprint.pformat(field.selection))


def has_auto_string(field):
    # type: (Field) -> bool
    """Return whether the string of a field looks automatically generated."""
    if field.string == "unknown":
        # Legacy osv fields
        return True
    string_ = field.string.lower()
    name = field.name.replace("_", " ")
    if name == string_:
        return True
    if name.endswith(" ids") and name[:-4] == string_:
        return True
    if name.endswith(" id") and name[:-3] == string_:
        return True
    return False
