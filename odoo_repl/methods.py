from __future__ import print_function

import inspect
import sys

import odoo_repl

from odoo_repl import color
from odoo_repl import gitsources
from odoo_repl import grep
from odoo_repl import sources
from odoo_repl import util

from odoo_repl.imports import t, PY3, BaseModel


class MethodProxy(object):
    def __init__(self, method, model, name):
        # type: (t.Callable[..., t.Any], BaseModel, t.Text) -> None
        self._real = method
        self.model = model
        self.name = str(name)

    def __call__(self, *args, **kwargs):
        # type: (t.Any, t.Any) -> t.Any
        return self._real(*args, **kwargs)

    def __getattr__(self, attr):
        # type: (t.Text) -> t.Any
        if attr.startswith("__"):
            raise AttributeError
        return getattr(self._real, attr)

    def __dir__(self):
        # type: () -> t.List[str]
        if PY3:
            listing = set(super().__dir__())
        else:
            listing = {"edit_", "source_", "gitsource_", "grep_"}
        listing.update(dir(self._real))
        return sorted(listing)

    def __repr__(self):
        # type: () -> str
        return "{}({!r}, {!r}, {!r})".format(
            self.__class__.__name__, self._real, self.model, self.name
        )

    def _repr_pretty_(self, printer, _cycle):
        # type: (t.Any, t.Any) -> None
        if printer.indentation == 0:
            printer.text(method_repr(self))
        else:
            printer.text(repr(self))

    def edit_(self, *args, **kwargs):
        # type: (t.Any, t.Any) -> None
        return odoo_repl.edit(self, *args, **kwargs)

    def source_(self, location=None):
        # type: (t.Optional[t.Text]) -> None
        first = True
        for cls in type(self.model).__mro__[1:]:
            module = util.module(cls)
            if location is not None and location != module:
                continue
            if self.name in vars(cls):
                func = util.unpack_function(vars(cls)[self.name])
                fname = sources.getsourcefile(func)
                lines, lnum = inspect.getsourcelines(func)
                if not first:
                    print()
                else:
                    first = False
                print(sources.format_source(sources.Source(module, fname, lnum)))
                print(color.highlight("".join(lines)))

    gitsource_ = gitsources.gitsource

    def grep_(self, *args, **kwargs):
        # type: (object, object) -> None
        """grep through all of the method's definitions, ignoring other file content.

        See ModelProxy.grep_ for options.

        The implementation is hacky. If you get weird results it's probably not
        your fault.
        """
        argv = grep.build_grep_argv(args, kwargs)
        for cls in type(self.model).__mro__[1:]:
            if self.name in vars(cls):
                func = util.unpack_function(vars(cls)[self.name])
                try:
                    grep.partial_grep(argv, func)
                except grep.BadCommandline as err:
                    print(err, file=sys.stderr)
                    return
                except grep.NoResults:
                    continue
                else:
                    print()


def _get_method_docs(model, name):
    # type: (BaseModel, str) -> t.Iterable[t.Tuple[str, t.Text]]
    return sources.find_docs(
        (util.module(cls), vars(cls)[name])
        for cls in type(model).__mro__
        if name in vars(cls)
    )


def method_repr(methodproxy):
    # type: (MethodProxy) -> t.Text
    model = methodproxy.model
    name = methodproxy.name

    method = methodproxy._real
    decorators = list(_find_decorators(method))
    method = util.unpack_function(method)
    try:
        src = sources.find_method_source(methodproxy)
        signature = _func_signature(method)
    except (TypeError, ValueError, IOError):
        return repr(methodproxy._real)

    docs = _get_method_docs(model, name)
    parts = []
    parts.extend(decorators)
    parts.append(
        "{model}.{name}{signature}".format(
            model=color.model(model._name), name=color.method(name), signature=signature
        )
    )
    parts.extend(sources.format_docs(docs))
    parts.append("")
    parts.extend(sources.format_sources(src))
    return "\n".join(parts)


def _find_decorators(method):
    # type: (t.Any) -> t.Iterator[t.Text]
    if hasattr(method, "_constrains"):
        yield color.decorator("@api.constrains") + "({})".format(
            ", ".join(map(repr, method._constrains))
        )
    if hasattr(method, "_depends"):
        if callable(method._depends):
            yield color.decorator("@api.depends") + "({!r})".format(method._depends)
        else:
            yield color.decorator("@api.depends") + "({})".format(
                ", ".join(map(repr, method._depends))
            )
    if hasattr(method, "_onchange"):
        yield color.decorator("@api.onchange") + "({})".format(
            ", ".join(map(repr, method._onchange))
        )
    if getattr(method, "_api", False):
        api = method._api
        yield color.decorator("@api.{}".format(api.__name__ if callable(api) else api))
    if not hasattr(method, "__self__"):
        yield color.decorator("@staticmethod")
    elif isinstance(method.__self__, type):
        yield color.decorator("@classmethod")


def _func_signature(func):
    # type: (t.Callable[..., t.Any]) -> t.Text
    # pylint: disable=deprecated-method
    if PY3:
        return str(inspect.signature(func))
    else:
        return inspect.formatargspec(*inspect.getargspec(func))
