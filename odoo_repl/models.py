# -*- coding: utf-8 -*-
from __future__ import print_function

import collections
import inspect
import subprocess

import odoo_repl

from odoo_repl import access
from odoo_repl import color
from odoo_repl import fields
from odoo_repl import grep
from odoo_repl import methods
from odoo_repl import sources
from odoo_repl import util
from odoo_repl.imports import abc, odoo, t, cast, Field, PY3, Text, BaseModel


FIELD_BLACKLIST = {
    # These are on all models
    "__last_update",
    "display_name",
    "create_date",
    "create_uid",
    "write_date",
    "write_uid",
    "id",
}


def model_repr(obj):
    # type: (t.Union[ModelProxy, BaseModel]) -> t.Text
    """Summarize a model's fields."""
    if isinstance(obj, ModelProxy) and obj._real is None:
        return repr(obj)
    obj = util.unwrap(obj)

    field_names = []
    delegated = []
    for field in sorted(obj._fields):
        if field in FIELD_BLACKLIST:
            continue
        if getattr(obj._fields[field], "related", False):
            delegated.append(obj._fields[field])
            continue
        field_names.append(field)
    max_len = max(len(f) for f in field_names) if field_names else 0
    parts = []

    parts.append(color.header(obj._name))
    if obj._transient:
        parts[-1] += " (transient)"
    if getattr(obj, "_abstract", False):
        parts[-1] += " (abstract)"
    elif not obj._auto:
        parts[-1] += " (no automatic table)"
    if getattr(obj, "_description", False) and obj._description != obj._name:
        parts.append(color.display_name(obj._description))
    if getattr(obj, "_inherits", False):
        for model_name, field_name in obj._inherits.items():
            parts.append(
                "Inherits from {} through {}".format(
                    color.model(model_name), color.field(field_name)
                )
            )
    inherits = _find_inheritance(obj)
    if inherits:
        # Giving this a very similar message to the one for _inherits feels dirty
        # But then _inherit is already very similar to _inherits so maybe it's ok
        parts.append(
            "Inherits from {}".format(
                ", ".join(color.model(inherit) for inherit in sorted(inherits))
            )
        )
    docs = list(
        sources.find_docs(
            (util.module(cls), cls)
            for cls in type(obj).__bases__
            if getattr(cls, "_name", obj._name) == obj._name
        )
    )
    parts.extend(sources.format_docs(docs))
    if docs:
        parts.append("")
    for field in field_names:
        f_obj = obj._fields[field]
        parts.append(
            color.blue.bold(_fmt_properties(f_obj))
            + " {}: ".format(color.field(field))
            # Like str.ljust, but not confused about colors
            + (max_len - len(field)) * " "
            + color.color_field(f_obj)
            + u" ({})".format(f_obj.string)
        )
    if delegated:
        buckets = collections.defaultdict(
            list
        )  # type: t.DefaultDict[t.Tuple[t.Text, ...], t.List[t.Text]]
        for f_obj in delegated:
            assert f_obj.related
            buckets[tuple(f_obj.related[:-1])].append(
                color.field(f_obj.name)
                if f_obj.related[-1] == f_obj.name
                else "{} (.{})".format(color.field(f_obj.name), f_obj.related[-1])
            )
        parts.append("")
        for related_field, field_names in buckets.items():
            # TODO: figure out name of model of real field
            parts.append(
                "Delegated to {}: {}".format(
                    color.yellow.bold(".".join(related_field)), ", ".join(field_names)
                )
            )
    parts.append("")
    parts.extend(sources.format_sources(sources.find_source(obj)))
    return "\n".join(parts)


def _fmt_properties(field):
    # type: (Field) -> t.Text
    parts = [" ", " ", " ", " "]
    if field.required:
        if field.default:
            parts[0] = "r"
        else:
            parts[0] = "R"
    if field.store:
        parts[1] = "s"
    if field.default:
        parts[2] = "d"
    if _has_computer(field):
        parts[3] = "c"
    return "".join(parts)


def _has_computer(field):
    # type: (Field) -> bool
    return (
        field.compute is not None
        or type(getattr(field, "column", None)).__name__ == "function"
    )


def _find_inheritance(model):
    # type: (BaseModel) -> t.Set[str]
    inherits = set()  # type: t.Set[str]
    for base in type(model).__bases__:
        cur_inherits = getattr(base, "_inherit", None)
        if not cur_inherits:
            continue
        if isinstance(cur_inherits, Text):
            inherits.add(cur_inherits)  # type: ignore
        else:
            inherits.update(cur_inherits)
    return inherits - {model._name, "base"}


class ModelProxy(object):
    """A wrapper around an Odoo model.

    Records can be browsed with indexing syntax, other models can be used
    with tab-completed attribute access, there are added convenience methods,
    and instead of an ordinary repr a summary of the fields is shown.
    """

    def __init__(self, env, path, nocomplete=False):
        # type: (odoo.api.Environment, t.Text, bool) -> None
        self._env = env
        self._path = path
        self._real = env[path] if path in env.registry else None
        if nocomplete and self._real is None:
            raise ValueError("Model '{}' does not exist".format(self._path))
        self._nocomplete = nocomplete

    def __getattr__(self, attr):
        # type: (t.Text) -> t.Any
        if attr.startswith("__"):
            raise AttributeError
        new = self._path + "." + attr
        if not self._nocomplete:
            if new in self._env.registry:
                return self.__class__(self._env, new)
            if any(m.startswith(new + ".") for m in self._env.registry):
                return self.__class__(self._env, new)
        if self._real is None:
            raise AttributeError("Model '{}' does not exist".format(new))
        if attr in self._real._fields:
            return fields.FieldProxy(self._env, self._real._fields[attr])
        thing = getattr(self._real, attr)  # type: object
        if (
            callable(thing)
            and not isinstance(thing, type)
            and hasattr(type(self._real), attr)
        ):
            thing = methods.MethodProxy(thing, self._real, attr)
        return thing

    def __dir__(self):
        # type: () -> t.List[t.Text]
        # Attributes that should be excluded when we're not proxying a real model
        real_attrs = {
            "shuf_",
            "mod_",
            "source_",
            "rules_",
            "view_",
            "sql_",
            "grep_",
            "_",
            "methods_",
            "menus_",
            "mapped",
            "filtered",
            "get_xml_id",
            "filtered_",
            "_all_ids_",
            "fields_",
        }  # type: t.Set[t.Text]
        if PY3:
            listing = set(super().__dir__())
        else:
            listing = real_attrs.copy()
        if self._real is not None:
            listing.update(
                attr for attr in dir(self._real) if not attr.startswith("__")
            )
            # https://github.com/odoo/odoo/blob/5cdfd53d/odoo/models.py#L341 adds a
            # bogus attribute that's annoying for tab completion
            listing -= {"<lambda>"}
        else:
            listing -= real_attrs
        # This can include entries that contain periods.
        # Both the default completer and IPython handle that well.
        listing.update(
            mod[len(self._path) + 1 :]
            for mod in self._env.registry
            if mod.startswith(self._path + ".")
        )
        return sorted(listing)

    def __iter__(self):
        # type: () -> t.Iterator[BaseModel]
        assert self._real is not None
        return iter(self._real.search([]))

    @property
    def fields_(self):
        # type: () -> t.List[fields.FieldProxy]
        assert self._real is not None
        return [
            fields.FieldProxy(self._env, field)
            for field in sorted(self._real._fields.values(), key=lambda f: f.name)
        ]

    def __len__(self):
        # type: () -> int
        assert self._real is not None
        return self._real.search([], count=True)

    def mapped(self, *a, **k):
        # type: (t.Any, t.Any) -> t.Any
        assert self._real is not None
        return self._real.search([]).mapped(*a, **k)

    def filtered(self, *a, **k):
        # type: (t.Any, t.Any) -> BaseModel
        assert self._real is not None
        return self._real.search([]).filtered(*a, **k)

    def get_xml_id(self):
        # type: () -> t.Dict[int, t.Text]
        assert self._real is not None
        return {
            data.res_id: data.complete_name
            for data in self._env["ir.model.data"].search(
                [("model", "=", self._real._name)], order="res_id asc"
            )
        }

    def filtered_(self, *a, **k):
        # type: (t.Any, t.Any) -> BaseModel
        assert self._real is not None
        return self._real.search([]).filtered_(*a, **k)  # type: ignore

    def __repr__(self):
        # type: () -> str
        return "<{}({})>".format(self.__class__.__name__, self._path)

    def _repr_pretty_(self, printer, _cycle):
        # type: (t.Any, t.Any) -> None
        if self._real is not None and printer.indentation == 0:
            printer.text(model_repr(self._real))
        else:
            printer.text(repr(self))

    def __getitem__(self, ind):
        # type: (t.Union[t.Iterable[int], t.Text, int]) -> t.Any
        if self._real is None:
            raise KeyError("Model '{}' does not exist".format(self._path))
        if not ind:
            return self._real
        if isinstance(ind, Text):
            if ind in self._real._fields:
                return fields.FieldProxy(self._env, self._real._fields[ind])
            thing = getattr(self._real, ind)
            if callable(thing):
                return methods.MethodProxy(thing, self._real, ind)
            return thing
        if isinstance(ind, abc.Iterable):
            assert not isinstance(ind, Text)
            ind = tuple(ind)
        if not isinstance(ind, tuple):
            ind = (ind,)
        # Browsing a non-existent record can cause weird caching problems, so
        # check first
        real_ind = set(
            util.sql(
                self._env,
                'SELECT id FROM "{}" WHERE id IN %s'.format(self._real._table),
                ind,
            )
        )
        missing = set(ind) - real_ind
        if missing:
            raise KeyError(
                "Records {} do not exist".format(", ".join(map(str, missing)))
            )
        return self._real.browse(ind)

    def _ipython_key_completions_(self):
        # type: () -> t.List[t.Text]
        assert self._real is not None
        return list(self._real._fields) + dir(self._real)  # type: ignore

    def _ensure_real(self):
        # type: () -> None
        if self._real is None:
            raise TypeError("Model '{}' does not exist".format(self._path))

    def _all_ids_(self):
        # type: () -> t.List[int]
        """Get all record IDs in the database."""
        self._ensure_real()
        return util.sql(
            self._env, "SELECT id FROM {}".format(self._env[self._path]._table)
        )

    def mod_(self):
        # type: () -> odoo.models.IrModel
        """Get the ir.model record of the model."""
        self._ensure_real()
        return self._env["ir.model"].search([("model", "=", self._path)])

    def shuf_(self, num=1):
        # type: (int) -> BaseModel
        """Return a random record, or multiple."""
        assert self._real is not None
        return odoo_repl._BaseModel_search_(self._real, shuf=num)

    def source_(self, location=None):
        # type: (t.Optional[t.Text]) -> None
        assert self._real is not None
        first = True
        for cls in type(self._real).__bases__:
            name = getattr(cls, "_name", None) or getattr(cls, "_inherit", None)
            if location is not None and util.module(cls) != location:
                continue
            if location is None and name != self._real._name:
                continue
            if not first:
                print()
            else:
                first = False
            print(sources.format_source(sources.Source.from_cls(cls)))
            print(color.highlight(inspect.getsource(cls)))

    def rules_(self, user=None):
        # type: (t.Optional[odoo.models.ResUsers]) -> None
        # TODO: is it possible to collapse the rules into a single policy for a user?
        mod_id = self.mod_().id
        parts = []  # type: t.List[t.Text]
        parts.extend(
            access.access_repr(acc)
            for acc in access.access_for_model(self._env, mod_id, user)
        )
        parts.extend(
            access.rule_repr(rule)
            for rule in access.rules_for_model(self._env, mod_id, user)
        )
        print("\n\n".join(parts))

    def view_(
        self,
        view_type="form",  # type: t.Text
        user=None,  # type: t.Optional[t.Union[t.Text, int, odoo.models.ResUsers]]
        view_id=None,  # type: t.Optional[t.Union[t.Text, odoo.models.IrUiView, int]]
    ):
        # type: (...) -> None
        """Build up and print a view."""
        assert self._real is not None
        model = self._real
        if user is not None:
            # TODO: handle viewing as group
            model = model.sudo(_to_user(self._env, user))

        View = model.env["ir.ui.view"]

        if isinstance(view_id, Text):
            view_id = cast("odoo.models.IrUiView", self._env.ref(view_id))
        if isinstance(view_id, BaseModel):
            if view_id._name != "ir.ui.view":
                raise TypeError("view_id must be ir.ui.view")
            view_id = view_id.id
        if view_id is None:
            view_id = View.default_view(model._name, view_type)

        if not view_id:
            raise RuntimeError("No {} view found for {}".format(view_type, model._name))

        if odoo.release.version_info < (10, 0):
            form = View.read_combined(view_id)["arch"]
        else:
            form = View.browse(view_id).read_combined()["arch"]

        try:
            import lxml.etree
        except ImportError:
            pass
        else:
            form = lxml.etree.tostring(
                lxml.etree.fromstring(
                    form, lxml.etree.XMLParser(remove_blank_text=True)
                ),
                pretty_print=True,
                encoding="unicode",
            )
        print(color.highlight(form, "xml"))

    def sql_(self):
        # type: () -> None
        """Display basic PostgreSQL information about stored fields."""
        assert self._real is not None
        cr = self._env.cr._obj
        with util.savepoint(cr):
            cr.execute(
                """
                SELECT column_name, udt_name
                FROM information_schema.columns
                WHERE table_name = %s
                ORDER BY column_name
                """,
                (self._real._table,),
            )
            info = cr.fetchall()
        print(color.header(self._real._table))
        max_len = max(len(name) for name, _ in info)
        for name, datatype in info:
            print(
                "{}: ".format(color.field(name))
                + (max_len - len(name)) * " "
                + datatype
            )

    def grep_(self, *args, **kwargs):
        # type: (object, object) -> None
        """grep through the combined source code of the model.

        See help(odoo_repl.grep) for more information.
        """
        assert self._real is not None
        # TODO: handle multiple classes in single file properly
        argv = grep.build_grep_argv(args, kwargs)
        seen = set()  # type: t.Set[t.Text]
        for src in sources.find_source(self._real):
            if src.fname not in seen:
                seen.add(src.fname)
                argv.append(src.fname)
        subprocess.Popen(argv).wait()

    def methods_(self):
        # type: () -> None
        self._ensure_real()
        for cls in type(self._real).__bases__:
            meths = [
                (name, attr)
                for name, attr in sorted(vars(cls).items())
                if callable(attr)
            ]
            if meths:
                print()
                print(color.module(util.module(cls)))
                for name, meth in meths:
                    print(
                        color.method(name)
                        + methods._func_signature(util.unpack_function(meth))
                    )

    def menus_(self):
        # type: () -> None
        """List menus that point to the model."""
        assert self._real is not None

        # TODO: Is checking ir.actions.act_window records enough?

        menus = sorted(
            (tuple(menu.complete_name.split("/")), menu.action)
            for menu in self._env["ir.ui.menu"].search([])
            if menu.action
            if menu.action._name == "ir.actions.act_window"
            if self._real._name == menu.action.res_model
        )

        grouped = collections.defaultdict(list)
        for path, action in menus:
            if path:
                grouped[path[:-1]].append((path[-1], action))

        for lead in sorted(grouped):
            first = True
            lead_len = sum(map(len, lead)) + len(lead)
            for end, action in grouped[lead]:
                if first:
                    print(
                        color.menu_lead("/".join(lead) + "/") + color.menu(end), end="",
                    )
                    first = False
                else:
                    print(lead_len * " " + color.menu(end), end="")
                affix = color.make_affix(action)
                if affix is not None:
                    print(" ({})".format(affix))
                else:
                    print()
            print()

        def get_binding_model(action):
            # type: (odoo.models.IrActionsAct_window) -> t.Optional[t.Text]
            if odoo.release.version_info >= (13, 0):
                if action.binding_model_id:
                    return action.binding_model_id.model
                return None
            if action.src_model:
                # May be the empty string, we turn that into None too
                return action.src_model
            return None

        for action in self._env["ir.actions.act_window"].search(
            [("res_model", "=", self._real._name)]
        ):
            src_model = get_binding_model(action)
            if src_model:
                msg = u"{} → {}".format(color.model(src_model), color.menu(action.name))
                affix = color.make_affix(action)
                if affix is not None:
                    msg += " ({})".format(affix)
                print(msg, end="\n\n")

    def _(self, *args, **kwargs):
        # type: (t.Any, t.Any) -> t.Any
        assert self._real is not None
        return self._real.search_(*args, **kwargs)  # type: ignore


def _to_user(
    env,  # type: odoo.api.Environment
    user,  # type: t.Union[BaseModel, t.Text, int]
):
    # type: (...) -> odoo.models.ResUsers
    if isinstance(user, Text):
        login = user
        user = env["res.users"].search([("login", "=", login)])
        if len(user) != 1:
            raise ValueError("No user {!r}".format(login))
        return user
    elif isinstance(user, int):
        return env["res.users"].browse(user)
    if not isinstance(user, BaseModel):
        raise ValueError("Can't convert type of {!r} to user".format(user))
    if user._name == "res.users":
        return user  # type: ignore
    candidate = getattr(user, "user_id", user)
    if getattr(candidate, "_name", None) != "res.users":
        raise ValueError("{!r} is not a user".format(candidate))
    return candidate  # type: ignore
