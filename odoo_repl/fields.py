from __future__ import print_function

import inspect
import pprint
import re
import types

from odoo_repl import color
from odoo_repl import methods
from odoo_repl import sources
from odoo_repl import util
from odoo_repl.imports import t, odoo, Field, PY3, Text, MYPY


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
            listing = {"source_"}
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
        return self._make_method_proxy_(_find_computer(self._env, self._real))

    @property
    def default(self):
        # type: () -> object
        if not self._real.default:
            raise AttributeError
        return self._make_method_proxy_(
            _find_field_default(self._env[self._real.model_name], self._real)
        )


def field_repr(field, env=None):
    # type: (t.Union[FieldProxy, Field], t.Optional[odoo.api.Environment]) -> t.Text
    """List detailed information about a field."""
    # TODO:
    # - .groups, .copy, .states, .inverse, .column[12]
    if env is None:
        env = util.env
    field = util.unwrap(field)
    model = env[field.model_name]
    record = env["ir.model.fields"].search(
        [("model", "=", field.model_name), ("name", "=", field.name)]
    )
    if len(record) > 1:
        # This is rare, but apparently valid
        # TODO: pick intelligently based on MRO
        record = record[-1]
    elif not record:
        return color.missing("No ir.model.fields record found for field")
    parts = []  # type: t.List[t.Text]
    parts.append(
        "{} {} on {}".format(
            color.blue.bold(record.ttype),
            color.field(record.name),
            color.model(record.model),
        )
    )
    if record.relation:
        parts[-1] += " to {}".format(color.model(record.relation))

    properties = [
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
    if properties:
        parts[-1] += " ({})".format(", ".join(properties))

    parts.append(record.field_description)
    if field.help:
        if "\n" in field.help:
            parts.append(field.help)
        else:
            parts[-1] += ": " + field.help

    if field.related:
        parts.append("Delegated to {}".format(color.field(".".join(field.related))))
    elif getattr(field, "column", False) and type(field.column).__name__ == "related":
        parts.append("Delegated to {}".format(color.field(".".join(field.column.arg))))
    else:
        func = _find_computer(env, field)
        if getattr(func, "__name__", None) == "<lambda>":
            assert isinstance(func, types.FunctionType)
            try:
                func = _decipher_lambda(func)
            except Exception:
                pass
        if callable(func):
            func = getattr(func, "__name__", func)
        if func:
            parts.append("Computed by {}".format(color.method(str(func))))

    if getattr(model, "_constraint_methods", False):
        for constrainer in model._constraint_methods:
            if field.name in constrainer._constrains:
                parts.append(
                    "Constrained by {}".format(
                        color.method(getattr(constrainer, "__name__", constrainer))
                    )
                )

    if getattr(field, "inverse_fields", False):
        parts.append(
            "Inverted by {}".format(
                ", ".join(color.field(inv.name) for inv in field.inverse_fields)
            )
        )

    if field.default:
        default = _find_field_default(model, field)

        show_literal = False

        if getattr(default, "__module__", None) in {"odoo.fields", "openerp.fields"}:
            default = color.purple.bold("(Unknown)")
            show_literal = True

        try:
            if getattr(default, "__name__", None) == "<lambda>":
                assert isinstance(default, types.FunctionType)
                source = _decipher_lambda(default)
                default = color.purple.bold(source)
                show_literal = True
        except Exception:
            pass

        if callable(default):
            default = color.method(getattr(default, "__name__", str(default)))
            show_literal = True

        if show_literal:
            parts.append("Default value: {}".format(default))
        else:
            parts.append("Default value: {!r}".format(default))

    if record.ttype == "selection":
        if MYPY:
            assert isinstance(field, odoo.fields.Selection)
        if isinstance(field.selection, Text):
            sel = u"Values computed by {}".format(color.method(field.selection))
        elif callable(field.selection):
            sel = u"Values computed by {}".format(color.method(repr(field.selection)))
        else:
            # Most likely a list of 2-tuples
            sel = color.highlight(pprint.pformat(field.selection))
        parts.append(sel)

    src = sources.find_source(field)
    parts.extend(sources.format_sources(src))

    if not src and record.modules:
        parts.append(
            "Defined in module {}".format(
                ", ".join(color.module(module) for module in record.modules.split(", "))
            )
        )

    return "\n".join(parts)


def _find_computer(env, field):
    # type: (odoo.api.Environment, Field) -> object
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
    return source


def _find_field_default(model, field):
    # type: (odoo.models.BaseModel, Field) -> object
    # TODO: was the commented out code useful?
    if hasattr(model, "_defaults"):  # and not callable(model._defaults[field.name]):
        default = model._defaults[field.name]
    elif field.default:
        default = field.default
    else:
        return None

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
