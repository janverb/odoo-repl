# mypy: allow-untyped-defs, check-untyped-defs
"""Runtime automated tests.

These need a real database to work, which is inconvenient. Run odoo-repl with
the --run-tests flag in a buildout directory to run them.

The tests currently expect a database with only `base` installed, with demo
data.

A lot of tests are brittle. They should work with an aforementioned database on
Odoo 8, 10, and 12, but maybe not with other versions or many addons installed.

So if the tests fail for a certain Odoo version that doesn't necessarily mean
the package is actually broken. The tests definitely should be fixed in that
case though.
"""

from unittest import TestCase, defaultTestLoader, TextTestRunner, TestResult

from psycopg2.errors import SyntaxError as PGSyntaxError

import odoo_repl

from odoo_repl.imports import t, PY3


class TestOdooRepl(TestCase):
    db = None  # type: t.Optional[str]

    def setUp(self):
        self.real_env, self.ns = odoo_repl.create_namespace(self.db)
        self.env = self.ns["env"]  # type: odoo_repl.EnvProxy
        self.ref = self.ns["ref"]  # type: odoo_repl.shorthand.DataBrowser
        self.u = self.ns["u"]  # type: odoo_repl.shorthand.UserBrowser
        self.sql = self.ns["sql"]
        self.addons = self.ns["addons"]  # type: odoo_repl.addons.AddonBrowser
        odoo_repl.color.enabled = False

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
        rep = odoo_repl.record_repr(self.u.demo)
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
        rep = odoo_repl.model_repr(self.env["res.users"])
        self.assertRegex(rep, r"^res.users\nUsers\n")
        self.assertRegex(rep, r"\nInherits from res.partner through partner_id\n")
        self.assertRegex(rep, r"\nrsd  company_id:\s*many2one: res.company \(Company\)")
        self.assertRegex(rep, r"\nrs   login:\s*char \(Login\)\n")
        self.assertRegex(rep, r"\nDelegated to partner_id: \w+")
        self.assertRegex(rep, r"\nbase: /[^\n]*/res_users.py:\d+")

    def test_field_repr(self):
        self.assertRegex(
            odoo_repl.field_repr(self.real_env, self.env["res.users"].login),
            r"""^char login on res.users \(required, store(, related_sudo)?\)
Login: Used to log into the system
base: /[^\n]*/res_users.py:\d+$""",
        )
        self.assertRegex(
            odoo_repl.field_repr(self.real_env, self.env["res.users"].company_id),
            r"""^many2one company_id on res.users to res.company"""
            r""" \(required, store(, related_sudo)?\)
Company: The [^\n]*\.(
Constrained by _check_company)?
Default value: (_get_company|lambda self: self\.env\.company\.id)
base: /[^\n]*/res_users.py:\d+$""",
        )
        self.assertRegex(
            odoo_repl.field_repr(self.real_env, self.env["res.currency"].date),
            r"""^date date on res.currency \(readonly(, related_sudo)?\)
Date
Computed by _?compute_date
base: /[^\n]*/res_currency.py:\d+$""",
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
        self.assertIsInstance(self.addons.auth_ldap.manifest.version, str)
        self.assertTrue(self.addons.auth_ldap.path.endswith("addons/auth_ldap"))
        self.assertTrue(self.addons.auth_ldap.record.name, "auth_ldap")

        self.assertRegex(
            str(self.addons.auth_ldap),
            r"""^auth_ldap [\d\.]* by O[^\n]*
[^\n]*/addons/auth_ldap
(Uni|I)nstalled
Authentication via LDAP

Depends: base(, base_setup)?(
Dependents: users_ldap_[a-zA-Z0-9_, ]*)?

Adds support for authentication by LDAP server.
===============================================""",
        )

        self.assertRegex(
            str(self.addons.base),
            r"""^base [\d\.]* by O[^\n]*
[^\n]*/addons/base
Installed
Base

Dependents: [^\n]*
Indirect dependents: [^\n]*
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
        self.assertEqual(repr(res_users), "res.users[]")
        self.assertEqual(res_users.mod_().model, "res.users")
        self.assertEqual(len(res_users.shuf_(2)), 2)

    def test_create_write_info(self):
        demo = self.env["res.users"].search([("login", "=", "demo")])
        self.assertRegex(
            odoo_repl.odoo_repr(demo),
            r"""
Created on 20..-..-.. ..:..:..
""",
        )
        demo.partner_id.sudo(demo).write({"website": "blargh"})
        self.assertRegex(
            odoo_repl.odoo_repr(demo.partner_id),
            r"""
Created on 20..-..-.. ..:..:..
Written on 20..-..-.. ..:..:.. by u.demo
""",
        )

    if not PY3:
        assertRegex = TestCase.assertRegexpMatches
        assertNotRegex = TestCase.assertNotRegexpMatches


def run(db=None):
    # type: (t.Optional[str]) -> TestResult
    TestOdooRepl.db = db
    return TextTestRunner().run(defaultTestLoader.loadTestsFromTestCase(TestOdooRepl))
