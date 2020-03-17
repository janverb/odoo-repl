from datetime import datetime
from typing import (
    Any,
    Callable,
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
    name: str
    model_name: str
    comodel_name: str
    type: str
    string: Text
    relational: bool
    compute: object
    store: bool
    column: Any
    default: object
    help: Optional[Text]
    related: Optional[Sequence[Text]]
    inverse_fields: Sequence[Field[Any, Any]]  # Only in older versions
    inverse_name: str  # Not always there
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

class Selection(Field[Text, Required]):
    # Note: this can be integer instead of text
    selection: Union[List[Tuple[Text, Text]], Text, Callable[..., Any]]
