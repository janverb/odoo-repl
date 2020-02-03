# TODO:
# - typecheck .search()
# - typecheck multi/single records?
# - generate model info from Odoo runtime
import typing as t

from collections import OrderedDict

from mypy.nodes import StrExpr, UnicodeExpr, ARG_POS
from mypy.plugin import (
    MethodContext,
    MethodSigContext,
    Plugin,
    CheckerPluginInterface,
)
from mypy.types import (
    CallableType,
    Type,
    Instance,
    TypedDictType,
    AnyType,
    TypeOfAny,
    ProperType,
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
        if fullname.startswith("odoo.api.Environment"):
            if fullname.endswith(".__getitem__"):
                return envget_hook
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
        if not isinstance(cur_type, Instance):
            ctx.api.fail("Can't get {!r} from {!r}".format(part, cur_type), ctx.context)
            return AnyType(TypeOfAny.from_error)
        field_value = cur_type.type.get(part)
        if (
            not field_value
            or not field_value.type
            or not isinstance(field_value.type, Instance)
        ):
            ctx.api.fail(
                "Unknown field {!r} on type {!r}".format(part, cur_type.type.fullname),
                ctx.context,
            )
            return AnyType(TypeOfAny.from_error)
        if field_value.type.type.fullname.startswith("odoo.fields."):
            get = field_value.type.type.get("__get__")
            if (
                not get
                or not isinstance(get.type, CallableType)
                or not isinstance(get.type.ret_type, ProperType)
            ):
                ctx.api.fail(
                    "Unexpected type while analyzing {!r}".format(
                        field_value.type.type.fullname
                    ),
                    ctx.context,
                )
                return AnyType(TypeOfAny.from_error)
            cur_type = get.type.ret_type
        else:
            cur_type = field_value.type
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
        if not isinstance(cur_type, Instance):
            ctx.api.fail("Can't get {!r} from {!r}".format(part, cur_type), ctx.context)
            return AnyType(TypeOfAny.from_error)
        field_value = cur_type.type.get(part)
        if (
            not field_value
            or not field_value.type
            or not isinstance(field_value.type, Instance)
        ):
            ctx.api.fail(
                "Unknown field {!r} on type {!r}".format(part, cur_type.type.fullname),
                ctx.context,
            )
            return AnyType(TypeOfAny.from_error)
        if field_value.type.type.fullname.startswith("odoo.fields."):
            get = field_value.type.type.get("__get__")
            if (
                not get
                or not isinstance(get.type, CallableType)
                or not isinstance(get.type.ret_type, ProperType)
            ):
                ctx.api.fail(
                    "Unexpected type while analyzing {!r}".format(
                        field_value.type.type.fullname
                    ),
                    ctx.context,
                )
                return AnyType(TypeOfAny.from_error)
            cur_type = get.type.ret_type
        else:
            cur_type = field_value.type
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
    f_obj = ctx.type.type.get(field)
    if not f_obj or not isinstance(f_obj.type, Instance):
        ctx.api.fail(
            "Didn't find field {!r} on {!r}".format(field, ctx.type.type.fullname),
            ctx.context,
        )
        return AnyType(TypeOfAny.from_error)
    get = f_obj.type.type.get("__get__")
    if not get or not isinstance(get.type, CallableType):
        ctx.api.fail(
            "Didn't find descriptor for {!r} on {!r}".format(
                field, ctx.type.type.fullname
            ),
            ctx.context,
        )
        return AnyType(TypeOfAny.from_error)
    return get.type.ret_type


def envget_hook(ctx: MethodContext) -> Type:
    if not len(ctx.args) == 1 and len(ctx.args[0]) == 1:
        return ctx.default_return_type
    arg = ctx.args[0][0]
    if not isinstance(arg, (StrExpr, UnicodeExpr)):
        return ctx.default_return_type
    model = arg.value
    clsname = "".join(part.capitalize() for part in model.split("."))
    try:
        return ctx.api.named_type("odoo.models." + clsname)  # type: ignore
    except KeyError:
        ctx.api.fail("Unknown model {!r}".format(model), ctx.context)
        return AnyType(TypeOfAny.from_error)


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


def plugin(_version: str) -> t.Type[OdooPlugin]:
    return OdooPlugin
