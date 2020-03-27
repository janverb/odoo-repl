from typing import overload, Any, Dict, Text, ClassVar

from odoo import models
from odoo.sql_db import Cursor

class Environment:
    cr: Cursor
    prefetch: Dict[Any, Any]  # Only some versions
    user: models.ResUsers
    uid: int
    context: Dict[str, Any]
    registry: Dict[str, Any]
    _local: ClassVar[Any]
    def __init__(
        self, cursor: Cursor, uid: int, context: Dict[Text, object]
    ) -> None: ...
    def __getitem__(self, key: Text) -> models.BaseModel: ...
    def ref(self, key: Text) -> models.BaseModel: ...

class Environments: ...
