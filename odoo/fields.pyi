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
    required: Required
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

class Char(Field[Text, Required]): ...
class Integer(Field[int, Required]): ...
class Boolean(Field[bool, Required]): ...
class Datetime(Field[Union[datetime, Text], Required]): ...

class Selection(Field[Text, Required]):
    # Note: this can be integer instead of text
    selection: Union[List[Tuple[Text, Text]], Text, Callable[..., Any]]

# While these have a required= argument, they always return a record object,
# so for our purposes they are always required (they can't return False)
# This may change in the future for Many2one if we start making a distinction
# between empty/uni-/multi-records
class Many2one(Field[AnyModel, Literal[True]]):
    def __init__(self, comodel_name: str, *, required: bool = ...) -> None: ...

class One2many(Field[AnyModel, Literal[True]]):
    def __init__(self, comodel_name: str, *, required: bool = ...) -> None: ...

class Many2many(Field[AnyModel, Literal[True]]):
    def __init__(self, comodel_name: str, *, required: bool = ...) -> None: ...
