from typing import (
    Any,
    List,
    Optional,
    Sequence,
    Text,
    Tuple,
    Type,
    Generic,
    TypeVar,
    overload,
)

from typing_extensions import Literal

from odoo.models import AnyModel, BaseModel

class Field:
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
    inverse_fields: Sequence[Field]  # Only in older versions
    selection: List[Tuple[Text, Text]]  # Only on selection fields

class Char(Field):
    @overload
    def __new__(cls, required: Literal[True]) -> _RChar: ...
    @overload
    def __new__(cls) -> Char: ...
    def __get__(self, record: Any, owner: Any) -> Optional[Text]: ...

class _RChar(Char):
    def __get__(self, record: Any, owner: Any) -> Text: ...

class Integer(Field):
    @overload
    def __new__(cls, required: Literal[True]) -> _RInteger: ...
    @overload
    def __new__(cls) -> Integer: ...
    def __get__(self, record: Any, owner: Any) -> Optional[int]: ...

class _RInteger(Integer):
    def __get__(self, record: Any, owner: Any) -> int: ...

class Boolean(Field):
    @overload
    def __new__(cls, required: Literal[True]) -> _RBoolean: ...
    @overload
    def __new__(cls) -> Boolean: ...
    def __get__(self, record: Any, owner: Any) -> Optional[bool]: ...

class _RBoolean(Boolean):
    def __get__(self, record: Any, owner: Any) -> bool: ...
