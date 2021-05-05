"""User configuration.

Currently loaded from environment variables, but feel free to assign at
runtime.
"""

import os

color = not (os.environ.get("NO_COLOR") or os.environ.get("ODOO_REPL_NO_COLOR"))

editor = tuple((os.environ.get("EDITOR") or "nano").split())
bg_editor = bool(os.environ.get("ODOO_REPL_BG_EDITOR"))

# Clickable filenames could interfere with linkification that includes line numbers,
# so they're turned off by default (I also didn't find myself using it)
clickable_filenames = bool(os.environ.get("ODOO_REPL_CLICKABLE_FILENAMES"))
clickable_records = not os.environ.get("ODOO_REPL_NO_CLICKABLE_RECORDS")

grep = os.environ.get("ODOO_REPL_GREP", "")

force_pdb = bool(os.environ.get("ODOO_REPL_FORCE_PDB"))

slow_tests = bool(os.environ.get("ODOO_REPL_SLOW_TESTS"))
