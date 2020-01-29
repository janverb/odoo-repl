from typing import Any, List, Optional, Sequence, Text, Tuple

class Field:
    name: Text
    model_name: Text
    comodel_name: Text
    type: Text
    relational: bool
    compute: object
    column: Any
    default: object
    help: Optional[Text]
    related: Optional[Sequence[Text]]
    inverse_fields: Sequence[Field]  # Only in older versions
    selection: List[Tuple[Text, Text]]  # Only on selection fields
