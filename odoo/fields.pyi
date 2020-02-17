from datetime import datetime
from typing import (
    Any,
    List,
    Optional,
    Sequence,
    Text,
    Tuple,
    Type,
    Union,
    Generic,
    TypeVar,
    overload,
)

from typing_extensions import Literal

from odoo.models import AnyModel, BaseModel

DATETIME_FORMAT: str

T = TypeVar("T")
Required = TypeVar("Required", bound=bool)
AnyField = TypeVar("AnyField", bound=Field[object, bool])

class Field(Generic[T, Required]):
    name: Text
    model_name: Text
    comodel_name: Text
    type: Text
    string: Text
    relational: bool
    compute: object
    column: Any
    default: object
    help: Optional[Text]
    related: Optional[Sequence[Text]]
    inverse_fields: Sequence[Field[Any, Any]]  # Only in older versions
    selection: List[Tuple[Text, Text]]  # Only on selection fields
    def __init__(self, *, required: bool = ...) -> None: ...
    @overload
    def __get__(self: AnyField, record: None, owner: Type[BaseModel]) -> AnyField: ...
    # mypy does not understand this
    # currently it just assumes -> T for everything, this should be fixed
    # perhaps in the plugin, perhaps by avoiding literals
    @overload
    def __get__(
        self: Field[T, Literal[True]], record: BaseModel, owner: Type[BaseModel]
    ) -> T: ...
    @overload
    def __get__(
        self: Field[T, Literal[False]], record: BaseModel, owner: Type[BaseModel]
    ) -> Optional[T]: ...

class Char(Field[Text, Required]):
    pass

class Integer(Field[int, Required]):
    pass

class Boolean(Field[bool, Required]):
    pass

class Datetime(Field[Union[datetime, Text], Required]):
    pass
