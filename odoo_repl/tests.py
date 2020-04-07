# mypy: allow-untyped-defs, check-untyped-defs
"""Runtime automated tests.

These need a real database to work, which is inconvenient. Run odoo-repl with
the --run-tests flag in a buildout directory to run them.

The tests currently expect a database with only `base` installed, with demo
data.

A lot of tests are brittle. They should work with an aforementioned database on
Odoo 8, 10, 12, and 13, but maybe not with other versions or many addons
installed.

So if the tests fail for a certain Odoo version that doesn't necessarily mean
the package is actually broken. The tests definitely should be fixed in that
case though.
"""

from __future__ import print_function

import io
import sys

from contextlib import contextmanager
from unittest import TestCase, defaultTestLoader, TextTestRunner, TestResult

from psycopg2.errors import SyntaxError as PGSyntaxError

import odoo_repl

from odoo_repl import config
from odoo_repl import odoo_repr
from odoo_repl import util
from odoo_repl.imports import t, PY3, cast, odoo, Text  # noqa: F401


def slow(test):
    # type: (t.Callable[[TestOdooRepl], None]) -> t.Callable[[TestOdooRepl], None]
    def newtest(self):
        # type: (TestOdooRepl) -> None
        if not config.slow_tests:
            self.skipTest("Slow tests disabled")
        test(self)

    return newtest


class TestOdooRepl(TestCase):
    db = None  # type: t.Optional[str]

    def setUp(self):
        self.real_env, self.ns = odoo_repl.create_namespace(self.db)
        self.env = self.ns["env"]  # type: odoo_repl.EnvProxy
        self.ref = self.ns["ref"]  # type: odoo_repl.shorthand.DataBrowser
        self.u = self.ns["u"]  # type: odoo_repl.shorthand.UserBrowser
        self.sql = self.ns["sql"]
        self.addons = self.ns["addons"]  # type: odoo_repl.addons.AddonBrowser
        self._captured_stream = None  # type: t.Optional[io.StringIO]
        config.clickable_filenames = False
        config.clickable_records = False
        config.color = False

    def test_basic_record_access(self):
        demo = self.real_env["res.users"].search([("login", "=", "demo")])
        self.assertEqual(self.ref.base.user_demo, demo)
        self.assertEqual(self.u.demo, demo)
        self.assertEqual(self.env.res.users._(login="demo"), demo)
        self.assertEqual(self.env["res.users"]._("login", "=", "demo"), demo)
        self.assertEqual(self.ns["res"].users[demo.id], demo)
        self.assertIn("demo", dir(self.u))

    def test_record_repr(self):
        if odoo_repl.xml_thread:
            odoo_repl.xml_thread.join()
        rep = odoo_repl.odoo_repr(self.u.demo)
        self.assertRegex(rep, r"^res.users\[\d*\] \(ref.base.user_demo\)\n")
        self.assertRegex(
            rep, r"\ncompany_id:\s*res.company\[\d*\] \(ref.base.main_company\)\n"
        )
        self.assertNotRegex(rep, r"\ndate_create:")
        self.assertRegex(rep, r"\nlogin:\s*u?'demo'\n")
        self.assertRegex(
            rep, r"\npartner_id:\s*res.partner\[\d*\] \(ref.base.partner_demo\)\n"
        )
        self.assertRegex(rep, r"\nbase: /[^\n]*demo\.xml:\d+")

    def test_model_repr(self):
        rep = odoo_repl.odoo_repr(self.env["res.users"])
        self.assertRegex(rep, r"^res.users\nUsers\n")
        self.assertRegex(rep, r"\nInherits from res.partner through partner_id\n")
        self.assertRegex(rep, r"\nrsd  company_id:\s*many2one: res.company\n")
        self.assertRegex(rep, r"\nRs   login:\s*char\n")
        self.assertRegex(
            rep, r"\nRs   partner_id:\s*many2one: res.partner \(Related Partner\)\n"
        )
        self.assertRegex(rep, r"\nDelegated to partner_id: \w+")
        self.assertRegex(rep, r"\nbase: /[^\n]*/res_users.py:\d+")
        self.assertRegex(rep, r"\nbase:\n")

    def test_field_repr(self):
        self.assertRegex(
            odoo_repr(self.env["res.users"].login),
            r"""^char login on res.users \(required, store(, related_sudo)?\)
Used to log into the system(
On change: on_change_login)?
base: /[^\n]*/res_users.py:\d+$""",
        )
        self.assertRegex(
            odoo_repr(self.env["res.users"].company_id),
            r"""^many2one company_id on res.users to res.company"""
            r""" \(required, store(, related_sudo)?\)
The [^\n]*\.
Default value: (_get_company|lambda self: self\.env\.company\.id)(
Constrained by _check_company)?
base: /[^\n]*/res_users.py:\d+$""",
        )
        self.assertRegex(
            odoo_repr(self.env["res.currency"].date),
            r"""^date date on res.currency \(readonly(, related_sudo)?\)
Computed by _?compute_date
base: /[^\n]*/res_currency.py:\d+$""",
        )
        self.assertRegex(
            odoo_repr(self.env["res.partner"].type),
            r"""^selection type on res.partner \(store(, related_sudo)?\)
Address Type: .*
Default value: 'contact'
\[(\('\w+', '[\w\s]+'\)[,\s]*)+\]
base: /[^\n]*/res_partner.py:\d+$""",
        )

    def test_sql(self):
        self.assertEqual(
            self.sql("SELECT active, login FROM res_users WHERE login = %s", "demo"),
            [(True, "demo")],
        )
        self.assertEqual(
            self.sql("SELECT login FROM res_users WHERE login = %s", "demo"), ["demo"],
        )
        with self.assertRaises(PGSyntaxError):
            self.sql("FOO")
        self.assertEqual(
            sorted(
                self.sql(
                    "SELECT login FROM res_users WHERE login in %s", ("admin", "demo")
                )
            ),
            ["admin", "demo"],
        )

    def test_addons(self):
        self.assertIn("auth_ldap", dir(self.addons))
        self.assertIn("base", dir(self.addons))

        self.assertEqual(self.addons.auth_ldap.manifest.installable, True)
        self.assertIsInstance(self.addons.auth_ldap.manifest.version, Text)
        self.assertTrue(self.addons.auth_ldap.path.endswith("addons/auth_ldap"))
        self.assertTrue(self.addons.auth_ldap.record.name, "auth_ldap")

        self.assertRegex(
            odoo_repr(self.addons.auth_ldap),
            r"""^auth_ldap [\d\.]* by O[^\n]*
https?://.*/web\?debug=1#model=ir\.module\.module&id=\d+
[^\n]*/addons/auth_ldap
(Uni|I)nstalled
Authentication via LDAP

Depends: base(, base_setup)?(
Dependents: .*)?(
Defines: .*)?

Adds support for authentication by LDAP server.
===============================================""",
        )

        self.assertRegex(
            odoo_repr(self.addons.base),
            r"""^base [\d\.]* by O[^\n]*
https?://.*/web\?debug=1#model=ir\.module\.module&id=\d+
[^\n]*/addons/base
Installed
Base

Dependents: [^\n]*
Defines: [^\n]*, res.users, """,
        )

        self.assertEqual(self.addons.base.ref.user_demo, self.u.demo)

    def test_ref(self):
        self.assertIn("base", dir(self.ref))
        self.assertIn("user_demo", dir(self.ref.base))
        self.assertEqual(self.ref.base.user_demo, self.u.demo)

    def test_namespace_misc(self):
        self.assertIs(self.ns["odoo"], self.ns["openerp"])
        self.assertIsInstance(self.ns["odoo"].release.version_info, tuple)
        self.assertEqual(self.ns["self"], self.ns["res"].users[1])

    def test_modelproxy(self):
        res_users = self.env["res.users"]
        self.assertGreater(len(res_users), 1)
        self.assertIn("demo", res_users.mapped("login"))
        self.assertEqual(res_users.filtered_(login="demo"), self.u.demo)
        self.assertEqual(res_users.filtered(lambda u: u.login == "demo"), self.u.demo)
        self.assertEqual(repr(res_users), "<ModelProxy(res.users)>")
        self.assertEqual(res_users.mod_().model, "res.users")
        self.assertEqual(len(res_users.shuf_(2)), 2)

    def test_create_write_info(self):
        demo = self.env["res.users"].search([("login", "=", "demo")])
        self.assertRegex(
            odoo_repr(demo), "Created on 20..-..-.. ..:..:..",
        )
        util.with_user(demo.partner_id, demo).write({"website": "blargh"})
        self.assertRegex(
            odoo_repr(demo.partner_id),
            r"""Created on 20..-..-.. ..:..:..
Written on 20..-..-.. ..:..:.. by u.demo""",
        )

    def test_record_repr_works_if_unprivileged(self):
        user = cast("odoo.models.ResUsers", self.ref.base.public_user)
        odoo_repr(util.with_user(self.u.admin, user))

    def test_source_printing(self):
        with self.capture_stdout():
            self.ns["env"]["res.users"].login.source_()
        self.assertCaptured(r"base: /.*:\d+")
        self.assertCaptured(r" fields\.[Cc]har\(")

        with self.capture_stdout():
            self.ns["env"]["res.users"].browse.source_()
        self.assertCaptured(r"BaseModel: /.*:\d+")
        self.assertCaptured(r"\ndef browse\(")
        self.assertCaptured(r"return self\._browse\(")

        with self.capture_stdout():
            self.ns["u"].demo.source_()
        self.assertCaptured(r"base: /.*:\d+")
        self.assertCaptured(r"<record id=.user_demo.")

        with self.capture_stdout():
            self.ns["env"]["res.users"].source_()
        self.assertCaptured(r"base: /.*:\d+")
        self.assertCaptured(r"class [\w_]+\(")
        self.assertCaptured(r"def has_group\(")
        self.assertNotCaptured(r"class BaseModel")

    @slow
    def test_repr_all_models(self):
        for model in self.ns["env"]:
            self.assertIsInstance(model, odoo_repl.models.ModelProxy)
            self.assertTrue(odoo_repl.odoo_repr(model))
            for field in model.fields_:
                self.assertIsInstance(field, odoo_repl.fields.FieldProxy)
                self.assertTrue(odoo_repl.odoo_repr(field))
            for attr_name in dir(model._real):
                if attr_name.startswith("__") or attr_name in {"_cache"}:
                    continue
                thing = getattr(model, attr_name)
                try:
                    self.assertTrue(odoo_repl.odoo_repr(thing))
                except Exception:
                    print("\n\nFailed on {}.{}\n".format(model._name, attr_name))
                    raise

    @slow
    def test_repr_all_addons(self):
        for addon in self.ns["addons"]:
            self.assertIsInstance(addon, odoo_repl.addons.Addon)
            if addon.record.state == "uninstallable":
                continue
            try:
                self.assertTrue(odoo_repl.odoo_repr(addon))
            except Exception:
                print("\n\nFailed on addons.{}\n".format(addon._module))
                raise

    @slow
    def test_repr_all_data(self):
        if odoo_repl.xml_thread:
            odoo_repl.xml_thread.join()
        for xml_id in odoo_repl.sources.xml_records.copy():
            try:
                record = self.real_env.ref(str(xml_id))
            except ValueError:
                if xml_id.module == "base" and xml_id.name.startswith("module_"):
                    # These are not always present in Odoo 12+.
                    # Maybe they're paid modules that are removed when the module
                    # list is updated?
                    continue
                print(
                    "\n\nHad trouble retrieving record {}. "
                    "Maybe you need to run an update?\n".format(xml_id)
                )
                raise
            try:
                self.assertTrue(odoo_repl.odoo_repr(record))
            except Exception:
                print("\n\nFailed on record {}\n".format(xml_id))
                raise

    @slow
    def test_repr_all_rules(self):
        with self.capture_stdout():
            for model in self.ns["env"]:
                model.rules_()

    @slow
    def test_print_all_menus(self):
        with self.capture_stdout():
            for model in self.ns["env"]:
                model.menus_()

    @contextmanager
    def capture_stdout(self):
        # type: () -> t.Iterator[io.StringIO]
        target = io.StringIO()
        if not PY3:

            def _target_write(text):
                if isinstance(text, str):
                    text = text.decode()
                return type(target).write(target, text)

            target.write = _target_write  # type: ignore
        sys.stdout = target  # type: ignore
        self._captured_stream = target
        try:
            yield target
        finally:
            sys.stdout = sys.__stdout__

    def assertCaptured(self, pattern):
        # type: (str) -> None
        assert self._captured_stream
        self.assertRegex(self._captured_stream.getvalue(), pattern)

    def assertNotCaptured(self, pattern):
        # type: (str) -> None
        assert self._captured_stream
        self.assertNotRegex(self._captured_stream.getvalue(), pattern)

    if not PY3:
        assertRegex = TestCase.assertRegexpMatches
        assertNotRegex = TestCase.assertNotRegexpMatches


def run(db=None):
    # type: (t.Optional[str]) -> TestResult
    TestOdooRepl.db = db
    return TextTestRunner().run(defaultTestLoader.loadTestsFromTestCase(TestOdooRepl))
