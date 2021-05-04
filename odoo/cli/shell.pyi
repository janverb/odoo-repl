from typing import Dict, Optional, Sequence, Text

class Shell:
    def console(self, local_vars: Dict[str, object]) -> None: ...
    def run(self, args: Sequence[Text]) -> Optional[int]: ...