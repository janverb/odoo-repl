"""A mypy plugin to typecheck Odoo code.

It's tightly coupled to the stubs, and it's not suitable for typechecking Odoo
addons yet.

The main thing that would have to change to make it suitable for that is
understanding Odoo's modules and inheritance. That might not be possible to do
with a plugin, in which case mypy itself would have to be patched.

But it serves odoo-repl's needs, and it could be reused in other projects that
interface with Odoo but run outside it.
"""

import typing as t

from collections import OrderedDict

from mypy import checker
from mypy import checkmember
from mypy import nodes
from mypy import types

from mypy.nodes import StrExpr, UnicodeExpr, ARG_POS
from mypy.plugin import (
    ClassDefContext,
    FunctionContext,
    MethodContext,
    MethodSigContext,
    Plugin,
    CheckerPluginInterface,
)
from mypy.types import (
    CallableType,
    Type,
    Instance,
    LiteralType,
    TypedDictType,
    AnyType,
    TypeOfAny,
)


class OdooPlugin(Plugin):
    def get_method_hook(
        self, fullname: str
    ) -> t.Optional[t.Callable[[MethodContext], Type]]:
        if fullname.startswith("odoo.models."):
            if fullname.endswith(".mapped"):
                return mapped_hook
            if fullname.endswith(".filtered"):
                return filtered_hook
            if fullname.endswith(".__getitem__"):
                return fieldget_hook
        if fullname.startswith("odoo.api.Environment."):
            if fullname.endswith(".__getitem__"):
                return envget_hook
        if fullname.startswith("odoo.fields."):
            if fullname.endswith(".__get__"):
                return fieldvalget_hook
        return None

    def get_method_signature_hook(
        self, fullname: str
    ) -> t.Optional[t.Callable[[MethodSigContext], CallableType]]:
        if fullname.startswith("odoo.models."):
            if fullname.endswith(".create"):
                return create_hook
            if fullname.endswith(".write"):
                return write_hook
        return None

    def get_function_hook(
        self, fullname: str
    ) -> t.Optional[t.Callable[[FunctionContext], Type]]:
        if fullname in {
            "odoo.fields.Many2one",
            "odoo.fields.One2many",
            "odoo.fields.Many2many",
        }:
            return newrelationalfield_hook
        if fullname.startswith("odoo.fields."):
            return newfield_hook
        return None

    def get_base_class_hook(
        self, fullname: str
    ) -> t.Optional[t.Callable[[ClassDefContext], None]]:
        if fullname == "odoo.models.BaseModel":
            return newmodel_hook
        return None


def mapped_hook(ctx: MethodContext) -> Type:
    if (
        not isinstance(ctx.type, Instance)
        or len(ctx.args) != 1
        or len(ctx.args[0]) != 1
        or not isinstance(ctx.args[0][0], (StrExpr, UnicodeExpr))
    ):
        return ctx.default_return_type
    field = ctx.args[0][0].value
    if not field:
        return ctx.default_return_type
    cur_type = ctx.type  # type: Type
    for part in field.split("."):
        cur_type = _access_member(cur_type, part, ctx)
    if isinstance(cur_type, Instance):
        if cur_type.type.fullname.startswith("odoo.models."):
            return cur_type
    return ctx.api.named_generic_type("builtins.list", [cur_type])


def filtered_hook(ctx: MethodContext) -> Type:
    if (
        not isinstance(ctx.type, Instance)
        or len(ctx.args) != 1
        or len(ctx.args[0]) != 1
        or not isinstance(ctx.args[0][0], (StrExpr, UnicodeExpr))
    ):
        return ctx.default_return_type
    field = ctx.args[0][0].value
    if not field:
        return ctx.default_return_type
    cur_type = ctx.type  # type: Type
    for part in field.split("."):
        cur_type = _access_member(cur_type, part, ctx)
    return ctx.default_return_type


def fieldget_hook(ctx: MethodContext) -> Type:
    if (
        not isinstance(ctx.type, Instance)
        or len(ctx.args) != 1
        or len(ctx.args[0]) != 1
        or not isinstance(ctx.args[0][0], (StrExpr, UnicodeExpr))
        or not ctx.args[0][0].value
    ):
        return ctx.default_return_type
    field = ctx.args[0][0].value
    return _access_member(ctx.type, field, ctx)


def envget_hook(ctx: MethodContext) -> Type:
    if not len(ctx.args) == 1 and len(ctx.args[0]) == 1:
        return ctx.default_return_type
    arg = ctx.args[0][0]
    if not isinstance(arg, (StrExpr, UnicodeExpr)):
        return ctx.default_return_type
    return get_model_by_name(arg.value, ctx)


def get_model_by_name(name: str, ctx: t.Union[MethodContext, FunctionContext]) -> Type:
    clsname = "".join(part.capitalize() for part in name.split("."))
    try:
        return ctx.api.named_type("odoo.models." + clsname)  # type: ignore
    except KeyError:
        ctx.api.fail("Unknown model {!r}".format(name), ctx.context)
        return AnyType(TypeOfAny.from_error)


def fieldvalget_hook(ctx: MethodContext) -> Type:
    arg1 = ctx.args[0][0]
    if isinstance(arg1, nodes.TempNode) and isinstance(arg1.type, types.NoneType):
        # TODO: Probably can't count on it always being a TempNode
        return ctx.default_return_type
    if not isinstance(ctx.type, types.Instance):
        return ctx.default_return_type
    req_arg = ctx.type.args[0]
    if not isinstance(req_arg, types.LiteralType):
        return ctx.default_return_type
    required = req_arg.value
    valtype = ctx.type.type.bases[0].args[0]
    if required:
        return valtype
    else:
        bool_type = ctx.api.named_type("bool")  # type: ignore
        lit_false = types.LiteralType(False, bool_type)
        return types.UnionType([valtype, lit_false])


def newfield_hook(ctx: FunctionContext) -> Type:
    if not isinstance(ctx.default_return_type, Instance):
        return ctx.default_return_type
    if ctx.arg_names and ["required"] in ctx.arg_names:
        req_type = ctx.arg_types[ctx.arg_names.index(["required"])][0]
        if (
            not isinstance(req_type, Instance)
            or not isinstance(req_type.last_known_value, LiteralType)
            or not isinstance(req_type.last_known_value.value, bool)
        ):
            ctx.api.fail("Can't decipher whether field is required or not", ctx.context)
            return ctx.default_return_type
        required = req_type.last_known_value
    else:
        bool_type = ctx.api.named_type("bool")  # type: ignore
        required = LiteralType(False, bool_type)
    return ctx.default_return_type.copy_modified(args=[required])


def newrelationalfield_hook(ctx: FunctionContext) -> Type:
    if not isinstance(ctx.default_return_type, Instance):
        return ctx.default_return_type
    if ctx.args and ctx.args[0] and isinstance(ctx.args[0][0], StrExpr):
        model = get_model_by_name(ctx.args[0][0].value, ctx)
    else:
        ctx.api.fail("Can't decipher comodel name", ctx.context)
        return ctx.default_return_type
    return ctx.default_return_type.copy_modified(args=[model])


def newmodel_hook(ctx: ClassDefContext) -> None:
    """Add a literal type for _name.

    This makes tagged unions possible. If we have Union[ResUsers, ResPartner]
    then mypy understands that `if x._name == "res.users":` means that x is
    ResUsers.

    Unfortunately it doesn't work for narrowing down BaseModel, so it's only
    rarely useful. A workaround could be to automatically define a union of all
    defined models.

    PEP 622 currently proposes @typing.sealed, which would mandate that all
    subclasses of a class must be mandated in the same module. This should
    resolve the problem for the current type stub setup, and may help with a
    version of the plugin that can analyze actual Odoo code.
    https://www.python.org/dev/peps/pep-0622/#sealed-classes-as-adts
    """
    dotted_name = []  # type: t.List[str]
    for char in ctx.cls.name:
        if dotted_name and char.isupper():
            dotted_name.append(".")
        dotted_name.append(char.lower())
    name_type = types.LiteralType("".join(dotted_name), ctx.api.named_type("str"))
    var = nodes.Var("_name", name_type)
    var.info = ctx.cls.info
    stn = nodes.SymbolTableNode(nodes.MDEF, var)
    ctx.cls.info.names["_name"] = stn


def _build_vals_dict(
    typ: Instance, api: CheckerPluginInterface
) -> "OrderedDict[str, Type]":
    return OrderedDict(
        {
            name: (
                api.named_generic_type("odoo.models._RecordId", [stn.type])
                if isinstance(stn.type, Instance)
                and stn.type.type.fullname.startswith("odoo.models.")
                else stn.type
            )
            for name, stn in typ.type.names.items()
            if stn.type
        }
    )


def create_hook(ctx: MethodSigContext) -> CallableType:
    if not isinstance(ctx.type, Instance):
        return ctx.default_signature
    if ctx.type.type.name == "BaseModel":
        return ctx.default_signature
    vals = _build_vals_dict(ctx.type, ctx.api)
    fallback = ctx.api.named_type("typing._TypedDict")  # type: ignore
    vals_type = TypedDictType(vals, set(), fallback)
    return CallableType(
        [vals_type],
        [ARG_POS],
        ["vals"],
        ctx.default_signature.ret_type,
        ctx.default_signature.fallback,
    )


def write_hook(ctx: MethodSigContext) -> CallableType:
    if not isinstance(ctx.type, Instance):
        return ctx.default_signature
    if ctx.type.type.name == "BaseModel":
        return ctx.default_signature
    vals = _build_vals_dict(ctx.type, ctx.api)
    fallback = ctx.api.named_type("typing._TypedDict")  # type: ignore
    vals_type = TypedDictType(vals, set(), fallback)
    return CallableType(
        [vals_type],
        [ARG_POS],
        ["vals"],
        ctx.default_signature.ret_type,
        ctx.default_signature.fallback,
    )


def _access_member(typ: Type, name: str, ctx: MethodContext) -> Type:
    assert isinstance(ctx.api, checker.TypeChecker)
    return checkmember.analyze_member_access(
        name=name,
        typ=typ,
        context=ctx.context,
        is_lvalue=False,
        is_super=False,
        is_operator=False,
        msg=ctx.api.msg,
        original_type=typ,
        chk=ctx.api,
    )


def plugin(_version: str) -> t.Type[OdooPlugin]:
    return OdooPlugin
