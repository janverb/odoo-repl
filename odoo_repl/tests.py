"""Runtime automated tests.

These need a real database to work, which is inconvenient. Run odoo-repl with
the --run-tests flag in a buildout directory to run them.

The tests currently expect a database with only `base` installed, with demo
data.
"""

from unittest import TestCase, defaultTestLoader, TextTestRunner, TestResult

import odoo_repl

from odoo_repl.imports import t, PY3


class TestOdooRepl(TestCase):
    db = None  # type: t.Optional[str]

    def setUp(self):
        # type: () -> None
        self.real_env, self.ns = odoo_repl.create_namespace(self.db)
        self.env = self.ns["env"]  # type: odoo_repl.EnvProxy
        self.ref = self.ns["ref"]  # type: odoo_repl.DataBrowser
        self.u = self.ns["u"]  # type: odoo_repl.UserBrowser
        odoo_repl.color.enabled = False

    def test_basic_record_access(self):
        # type: () -> None
        demo = self.real_env["res.users"].search([("login", "=", "demo")])
        self.assertEqual(self.ref.base.user_demo, demo)
        self.assertEqual(self.u.demo, demo)
        self.assertEqual(self.env.res.users._(login="demo"), demo)
        self.assertEqual(self.env["res.users"]._("login", "=", "demo"), demo)
        self.assertEqual(self.ns["res"].users[demo.id], demo)
        self.assertIn("demo", dir(self.u))

    def test_record_repr(self):
        # type: () -> None
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
        # type: () -> None
        rep = odoo_repl.model_repr(self.env["res.users"])
        self.assertRegex(rep, r"^res.users\nUsers\n")
        self.assertRegex(rep, r"\nInherits from res.partner through partner_id\n")
        self.assertRegex(rep, r"\nrsd  company_id:\s*many2one: res.company \(Company\)")
        self.assertRegex(rep, r"\nrs   login:\s*char \(Login\)\n")
        self.assertRegex(rep, r"\nDelegated to partner_id: \w+")
        self.assertRegex(rep, r"\nbase: /[^\n]*/res_users.py:\d+")

    def test_field_repr(self):
        # type: () -> None
        self.assertRegex(
            odoo_repl.field_repr(self.real_env, self.env["res.users"].login),
            r"""^char login on res.users \(required, store, related_sudo\)
Login: Used to log into the system
base: /[^\n]*/res_users.py:\d+$""",
        )
        self.assertRegex(
            odoo_repl.field_repr(self.real_env, self.env["res.users"].company_id),
            r"""^many2one company_id on res.users to res.company"""
            r""" \(required, store, related_sudo\)
Company: The company this user is currently working for.(
Constrained by _check_company)?
Default value: _get_company
base: /[^\n]*/res_users.py:\d+$""",
        )
        self.assertRegex(
            odoo_repl.field_repr(self.real_env, self.env["res.currency"].date),
            r"""^date date on res.currency \(readonly, related_sudo\)
Date
Computed by _?compute_date
base: /[^\n]*/res_currency.py:\d+$""",
        )

    if not PY3:
        assertRegex = TestCase.assertRegexpMatches
        assertNotRegex = TestCase.assertNotRegexpMatches


def run(db=None):
    # type: (t.Optional[str]) -> TestResult
    TestOdooRepl.db = db
    return TextTestRunner().run(defaultTestLoader.loadTestsFromTestCase(TestOdooRepl))
