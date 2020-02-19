"""Various helpers for accessing records with shorthand notation."""

from odoo_repl import util
from odoo_repl.imports import odoo, t, PY3

__all__ = (
    "RecordBrowser",
    "UserBrowser",
    "EmployeeBrowser",
    "DataBrowser",
    "DataModuleBrowser",
)


class RecordBrowser(object):
    _model = NotImplemented  # type: str
    _field = NotImplemented  # type: str
    _listing = NotImplemented  # type: str
    _abbrev = NotImplemented  # type: str

    def __init__(self, env):
        # type: (odoo.api.Environment) -> None
        self._env = env

    def __getattr__(self, attr):
        # type: (t.Text) -> odoo.models.BaseModel
        try:
            thing = self._env[self._model].search([(self._field, "=", attr)])
        except AttributeError as err:
            if err.args == ("environments",) and not attr.startswith("_"):
                # This happens when IPython runs completions in a separate thread
                # Returning an empty record means it can complete without making
                # queries, even across relations
                # When the line is actually executed __getattr__ will run again
                # We check for an underscore at the start to exclude both
                # dunder attributes and _ipython special methods
                # Even if a username does start with an underscore this is
                # acceptable because it only breaks completion
                return self._env[self._model]
            raise
        if not thing:
            raise AttributeError("Record '{}' not found".format(attr))
        return thing

    def __dir__(self):
        # type: () -> t.List[t.Text]
        if self._model not in self._env.registry:
            raise TypeError("Model '{}' is not installed".format(self._model))
        return [u"_model", u"_field", u"_listing", u"_abbrev"] + util.sql(
            self._env, self._listing
        )

    __getitem__ = __getattr__
    _ipython_key_completions_ = __dir__

    @classmethod
    def _repr_for_value(cls, ident):
        # type: (t.Text) -> t.Text
        if util.is_name(ident):
            return u"{}.{}".format(cls._abbrev, ident)
        if not PY3 and not isinstance(ident, str):
            try:
                ident = str(ident)
            except UnicodeEncodeError:
                pass
        return u"{}[{!r}]".format(cls._abbrev, ident)


class UserBrowser(RecordBrowser):
    """Easy access to records of user accounts.

    Usage:
    >>> u.admin
    res.users[1]
    >>> u[1]
    res.users[1]

    >>> u.adm<TAB> completes to u.admin

    >>> record.sudo(u.testemployee1)  # View a record as testemployee1
    """

    _model = "res.users"
    _field = "login"
    _listing = "SELECT login FROM res_users WHERE active"
    _abbrev = "u"


class EmployeeBrowser(RecordBrowser):
    """Like UserBrowser, but for employees. Based on user logins."""

    _model = "hr.employee"
    _field = "user_id.login"
    _listing = """
    SELECT u.login
    FROM hr_employee e
    INNER JOIN resource_resource r
        ON e.resource_id = r.id
    INNER JOIN res_users u
        ON r.user_id = u.id
    WHERE r.active
    """
    _abbrev = "emp"


class DataBrowser(object):
    """Easy access to data records by their XML IDs.

    Usage:
    >>> ref.base.user_root
    res.users[1]
    >>> ref('base.user_root')
    res.users[1]

    The attribute access has tab completion.
    """

    def __init__(self, env):
        # type: (odoo.api.Environment) -> None
        self._env = env

    def __getattr__(self, attr):
        # type: (t.Text) -> DataModuleBrowser
        if not util.sql(
            self._env, "SELECT id FROM ir_model_data WHERE module = %s LIMIT 1", attr
        ):
            raise AttributeError("No module '{}'".format(attr))
        browser = DataModuleBrowser(self._env, attr)
        setattr(self, attr, browser)
        return browser

    def __dir__(self):
        # type: () -> t.List[t.Text]
        return util.sql(self._env, "SELECT DISTINCT module FROM ir_model_data")

    def __call__(self, query):
        # type: (t.Text) -> odoo.models.BaseModel
        return self._env.ref(query)


class DataModuleBrowser(object):
    """Access data records within a module. Created by DataBrowser."""

    def __init__(self, env, module):
        # type: (odoo.api.Environment, t.Text) -> None
        self._env = env
        self._module = module

    def __getitem__(self, key):
        # type: (t.Text) -> odoo.models.BaseModel
        try:
            return self._env.ref("{}.{}".format(self._module, key))
        except ValueError as err:
            raise KeyError(err)
        except AttributeError as err:
            if err.args == ("environments",) and not key.startswith("_"):
                # Threading issue, try to keep autocomplete working
                # See RecordBrowser.__getattr__
                model = util.sql(
                    self._env,
                    "SELECT model FROM ir_model_data WHERE module = %s AND name = %s",
                    self._module,
                    key,
                )  # type: t.List[str]
                return self._env[model[0]]
            raise

    def __getattr__(self, attr):
        # type: (t.Text) -> odoo.models.BaseModel
        try:
            return self[attr]
        except KeyError:
            raise AttributeError

    def __dir__(self):
        # type: () -> t.List[t.Text]
        return util.sql(
            self._env, "SELECT name FROM ir_model_data WHERE module = %s", self._module
        )

    _ipython_key_completions_ = __dir__
