# -*- coding: utf-8 -*-
# Instead of Union[Literal[False], ...] we use Optional[...]
# This is less correct, but mypy handles it better

# Maybe it's possible and nice to make a distinction between uni- and multi-records

import types
from typing import (
    overload,
    Any,
    Callable,
    Dict,
    Generic,
    Iterable,
    Iterator,
    List,
    Optional,
    Sequence,
    Text,
    Tuple,
    TypeVar,
    Union,
)

from typing_extensions import Literal, TypedDict

from odoo import fields
from odoo.api import Environment
from odoo.fields import Field

T = TypeVar("T")
AnyModel = TypeVar("AnyModel", bound=BaseModel)

class BaseModel:
    _fields: Dict[Text, Field[Any, Any]]
    _table: Text
    _name: Text
    _description: Text
    _module: Text
    _auto: bool
    _register: bool
    _abstract: bool  # Not all versions
    _transient: bool
    _defaults: Dict[Text, object]
    _constraint_methods: List[_Constrainer]
    # Actually a defaultdict but let's not rely on that
    _onchange_methods: Dict[str, List[types.FunctionType]]
    _inherit: Union[str, Sequence[str]]
    _inherits: Dict[Text, Text]
    _rec_name: Optional[Text]
    env: Environment
    # _ids is normally a tuple, but base_suspend_security turns it into a list
    # on res.users
    _ids: Sequence[Union[int, NewId]]
    ids: List[int]
    @property
    def id(self: AnyModel) -> Union[NewId, _RecordId[AnyModel]]: ...
    display_name = fields.Char(required=True)
    create_date = fields.Datetime()
    create_uid = fields.Many2one("res.users")
    write_date = fields.Datetime()
    write_uid = fields.Many2one("res.users")
    # .browse(<NewId>) returns an empty record, .browse([<NewId>]) works
    def browse(self: AnyModel, ids: Union[int, Iterable[_Id]] = ...) -> AnyModel: ...
    def exists(self: AnyModel) -> AnyModel: ...
    def sudo(self: AnyModel, user: Union[int, ResUsers] = ...) -> AnyModel: ...
    with_user = sudo  # Odoo 13
    def with_context(
        self: AnyModel, ctx: Dict[Text, Any] = ..., **kwargs: Any
    ) -> AnyModel: ...
    @overload
    def search(
        self,
        args: Sequence[Union[Text, Tuple[Text, Text, object]]],
        count: Literal[True],  # count can't have a default value here so it's moved up
        offset: int = ...,
        limit: Optional[int] = ...,
        order: Optional[Text] = ...,
    ) -> int: ...
    @overload
    def search(
        self: AnyModel,
        args: Sequence[Union[Text, Tuple[Text, Text, object]]],
        offset: int = ...,
        limit: Optional[int] = ...,
        order: Optional[Text] = ...,
        count: bool = ...,
    ) -> AnyModel: ...
    def create(self: AnyModel, vals: Dict[str, object]) -> AnyModel: ...
    def write(self, vals: Dict[str, object]) -> bool: ...
    # todo: handle .read() in mypy-odoo
    def read(self, fields: Sequence[str] = ...) -> List[Dict[str, Any]]: ...
    @overload
    def mapped(self, func: Text) -> Union[List[object], BaseModel]: ...
    @overload
    def mapped(self: AnyModel, func: Callable[[AnyModel], T]) -> List[T]: ...
    def filtered(
        self: AnyModel, func: Union[Text, Callable[[AnyModel], Any]]
    ) -> AnyModel: ...
    def get_xml_id(self) -> Dict[int, Text]: ...
    def fields_view_get(
        self, view_id: Optional[int] = ..., view_type: Text = ...
    ) -> _FieldView: ...
    @overload
    def __getitem__(self: AnyModel, key: int) -> AnyModel: ...
    @overload
    def __getitem__(self: AnyModel, key: slice) -> AnyModel: ...
    @overload
    def __getitem__(self: AnyModel, key: Text) -> object: ...
    def __iter__(self: AnyModel) -> Iterator[AnyModel]: ...
    def __or__(self: AnyModel, other: AnyModel) -> AnyModel: ...
    def __sub__(self: AnyModel, other: AnyModel) -> AnyModel: ...
    def __len__(self) -> int: ...

class _RecordId(int, Generic[AnyModel]): ...
class NewId: ...

_Id = Union[int, NewId]

class _Constrainer(types.FunctionType):
    _constrains: Tuple[Text]

class _FieldView(TypedDict):
    # Very incomplete
    arch: Text

# These don't actually live in odoo.models
class IrModelAccess(BaseModel):
    active = fields.Boolean()
    group_id = fields.Many2one("res.groups")
    perm_read = fields.Boolean()
    perm_write = fields.Boolean()
    perm_create = fields.Boolean()
    perm_unlink = fields.Boolean()

class IrRule(BaseModel):
    active = fields.Boolean()
    groups = fields.Many2many("res.groups")
    domain_force = fields.Char()
    perm_read = fields.Boolean()
    perm_write = fields.Boolean()
    perm_create = fields.Boolean()
    perm_unlink = fields.Boolean()
    # "global" is a keyword, so it's not a valid identifier
    # but Python 3 has unicode normalization for identifiers
    # so just run the text through https://yaytext.com/bold-italic/ first
    𝐠𝐥𝐨𝐛𝐚𝐥 = fields.Boolean()
    def _eval_context(self) -> Dict[Any, Any]: ...

class IrModel(BaseModel):
    model = fields.Char(required=True)
    name = fields.Char(required=True)

class IrModelData(BaseModel):
    complete_name = fields.Char(required=True)
    module = fields.Char(required=True)
    name = fields.Char(required=True)
    # res_id is not actually required, but it seems like integers aren't nullable?
    res_id = fields.Integer(required=True)
    model = fields.Char(required=True)

class IrModelFields(BaseModel):
    ttype = fields.Char(required=True)
    name = fields.Char(required=True)
    model = fields.Char(required=True)
    relation = fields.Char()
    field_description = fields.Char(required=True)
    modules = fields.Char(required=True)  # actually computed

class IrModuleModule(BaseModel):
    name = fields.Char(required=True)
    state = fields.Char()
    installed_version = fields.Char(required=True)  # computed

class IrModuleModuleDependency(BaseModel):
    module_id = fields.Many2one("ir.module.module")
    name = fields.Char()
    depend_id = fields.Many2one("ir.module.module")

class IrTranslation(BaseModel):
    src = fields.Char()
    value = fields.Char()
    name = fields.Char(required=True)
    # actually selection
    # not technically required but always present in practice
    type = fields.Char(required=True)

class IrUiView(BaseModel):
    type = fields.Char(required=True)  # actually selection
    mode = fields.Char(required=True)  # actually selection
    inherit_id = fields.Many2one("ir.ui.view")
    inherit_children_ids = fields.One2many("ir.ui.view")
    def default_view(self, model: Text, view_type: Text) -> int: ...
    @overload  # < 10
    def read_combined(self, view_id: int) -> _FieldView: ...
    @overload  # >= 10
    def read_combined(self) -> _FieldView: ...

class IrUiMenu(BaseModel):
    # The exact union depends on the version
    # This is the sum of the unions in Odoo 8 and 12
    # Odoo 8 claims ir.actions.wizard is also possible but that model
    # doesn't actually exist in that version
    # TODO: use a real field here
    action: Union[
        Literal[False],
        IrActionsReport,
        IrActionsReportXml,
        IrActionsAct_window,
        IrActionsAct_url,
        IrActionsServer,
        IrActionsClient,
    ]
    complete_name = fields.Char(required=True)
    name = fields.Char(required=True)
    child_id = fields.One2many("ir.ui.menu")
    parent_id = fields.Many2one("ir.ui.menu")

class IrActionsReport(BaseModel): ...
class IrActionsReportXml(BaseModel): ...

class IrActionsAct_window(BaseModel):
    res_model = fields.Char(required=True)
    binding_model_id = fields.Many2one("ir.model")  # Odoo 13+
    src_model = fields.Char()  # Odoo <=12
    name = fields.Char(required=True)
    view_id = fields.Many2one("ir.ui.view")
    views: List[Tuple[int, Text]]
    def default_view(
        self, model: Text, view_type: Text
    ) -> Union[Literal[False], int]: ...

class IrActionsAct_url(BaseModel): ...
class IrActionsServer(BaseModel): ...
class IrActionsClient(BaseModel): ...

class IrConfig_parameter(BaseModel):
    def get_param(
        self, key: Text, default: Text = ...
    ) -> Union[Text, Literal[False]]: ...

class ResGroups(BaseModel):
    name = fields.Char(required=True)

class ResUsers(BaseModel):
    login = fields.Char(required=True)
    active = fields.Boolean()
    employee_ids = fields.One2many("hr.employee")
    partner_id = fields.Many2one("res.partner", required=True)
    def has_group(self, group_ext_id: Text) -> bool: ...

class ResPartner(BaseModel):
    pass

class HrEmployee(BaseModel):
    user_id = fields.Many2one("res.users")
    active = fields.Boolean()
