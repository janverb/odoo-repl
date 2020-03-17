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
    _defaults: Dict[Text, object]
    _constraint_methods: List[_Constrainer]
    # Actually a defaultdict but let's not rely on that
    _onchange_methods: Dict[str, List[types.FunctionType]]
    _inherit: Union[str, Sequence[str]]
    _inherits: Dict[Text, Text]
    env: Environment
    # _ids is normally a tuple, but base_suspend_security turns it into a list
    # on res.users
    _ids: Sequence[int]
    ids: List[int]
    @property
    def id(self: AnyModel) -> _RecordId[AnyModel]: ...
    display_name: Text
    create_date = fields.Datetime()
    create_uid: ResUsers
    write_date = fields.Datetime()
    write_uid: ResUsers
    def browse(self: AnyModel, ids: Union[int, Sequence[int]]) -> AnyModel: ...
    def exists(self: AnyModel) -> AnyModel: ...
    def sudo(self: AnyModel, user: Union[int, ResUsers] = ...) -> AnyModel: ...
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

class _RecordId(int, Generic[AnyModel]):
    pass

class _Constrainer(types.FunctionType):
    _constrains: Tuple[Text]

class _FieldView(TypedDict):
    # Very incomplete
    arch: Text

# These don't actually live in odoo.models
class IrModelAccess(BaseModel):
    active = fields.Boolean()
    group_id: ResGroups
    perm_read = fields.Boolean()
    perm_write = fields.Boolean()
    perm_create = fields.Boolean()
    perm_unlink = fields.Boolean()

class IrRule(BaseModel):
    active = fields.Boolean()
    groups: ResGroups
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
    module_id: IrModuleModule
    name = fields.Char()
    depend_id: IrModuleModule

class IrTranslation(BaseModel):
    src = fields.Char()
    value = fields.Char()

class IrUiView(BaseModel):
    def default_view(self, model: Text, view_type: Text) -> int: ...
    @overload  # < 10
    def read_combined(self, view_id: int) -> _FieldView: ...
    @overload  # >= 10
    def read_combined(self) -> _FieldView: ...

class ResGroups(BaseModel):
    name = fields.Char(required=True)

class ResUsers(BaseModel):
    login = fields.Char(required=True)
    active = fields.Boolean()
    employee_ids: HrEmployee
    partner_id: ResPartner
    def has_group(self, group_ext_id: Text) -> bool: ...

class ResPartner(BaseModel):
    pass

class HrEmployee(BaseModel):
    user_id: ResUsers
    active = fields.Boolean()
