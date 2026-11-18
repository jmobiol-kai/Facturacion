from odoo import Command, fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import UserError
from odoo.tests import tagged

DEBT_TYPES = ("asset_receivable", "liability_payable")


@tagged("post_install", "-at_install")
class TestMultiBranchPayment(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(su=True)
        cls.company = cls.env.company
        cls.company.use_payment_pro = True
        cls.company.with_context(no_vat_validation=True).vat = "30712345679"
        cls.bank_journal = cls.env["account.journal"].browse(cls.company_data["default_journal_bank"].id)
        cls.yesterday = cls._branch("Yesterday")
        cls.fisa = cls._branch("Fisa")
        cls.env.flush_all()
        cls.cr.precommit.run()
        cls._ensure_general_journal(cls.company)
        cls._ensure_general_journal(cls.yesterday)
        cls._ensure_general_journal(cls.fisa)
        cls.cc_branches = cls.env["account.account"].create({
            "name": "Cuenta corriente con sucursales",
            "code": "91190001",
            "account_type": "asset_current",
            "reconcile": False,
            "company_ids": [Command.set(cls.company.ids)],
        })
        cls.cc_central = cls.env["account.account"].create({
            "name": "Cuenta corriente con casa central",
            "code": "92190001",
            "account_type": "liability_current",
            "reconcile": False,
            "company_ids": [Command.set(cls.company.ids)],
        })
        cls.company.kai_branch_current_account_id = cls.cc_branches
        cls.yesterday.kai_central_current_account_id = cls.cc_central
        cls.fisa.kai_central_current_account_id = cls.cc_central
        cls.vendor = cls.env["res.partner"].create({"name": "Proveedor multi sucursal"})
        cls.customer = cls.env["res.partner"].create({"name": "Cliente multi sucursal"})

    @classmethod
    def _branch(cls, name):
        return cls.env["res.company"].with_context(no_vat_validation=True).create({
            "name": name,
            "parent_id": cls.company.id,
            "vat": cls.company.vat,
            "country_id": cls.company.country_id.id,
        })

    @classmethod
    def _ensure_general_journal(cls, company):
        journal = cls.env["account.journal"].search([
            ("company_id", "=", company.id),
            ("type", "=", "general"),
        ], limit=1)
        if not journal:
            cls.env["account.journal"].create({
                "name": "Operaciones varias",
                "code": "OPV",
                "type": "general",
                "company_id": company.id,
            })

    def _invoice(self, company, move_type, partner, amount):
        account = (
            self.company_data["default_account_expense"]
            if move_type in ("in_invoice", "in_refund")
            else self.company_data["default_account_revenue"]
        )
        move = self.env["account.move"].with_company(company).create({
            "move_type": move_type,
            "company_id": company.id,
            "partner_id": partner.id,
            "invoice_date": fields.Date.today(),
            "invoice_line_ids": [Command.create({
                "name": "Servicio",
                "quantity": 1,
                "price_unit": amount,
                "account_id": account.id,
                "tax_ids": [Command.clear()],
            })],
        })
        move.action_post()
        return move

    def _debt_lines(self, moves):
        return moves.line_ids.filtered(lambda line: line.account_id.account_type in DEBT_TYPES)

    def _payment(self, partner_type, partner, lines):
        amount = abs(sum(lines.mapped("amount_residual")))
        payment_type = "outbound" if partner_type == "supplier" else "inbound"
        payment = self.env["account.payment"].with_company(self.company).create({
            "payment_type": payment_type,
            "partner_type": partner_type,
            "partner_id": partner.id,
            "amount": amount,
            "currency_id": self.company.currency_id.id,
            "journal_id": self.bank_journal.id,
            "company_id": self.company.id,
        })
        payment.to_pay_move_line_ids = [Command.set(lines.ids)]
        return payment

    def _partner_balance(self, company, partner, account_type):
        lines = self.env["account.move.line"].search([
            ("company_id", "=", company.id),
            ("partner_id", "=", partner.commercial_partner_id.id),
            ("account_id.account_type", "=", account_type),
            ("parent_state", "=", "posted"),
        ])
        return sum(lines.mapped("balance"))

    def _account_balance(self, account, company):
        lines = self.env["account.move.line"].search([
            ("account_id", "=", account.id),
            ("company_id", "=", company.id),
            ("parent_state", "=", "posted"),
        ])
        return sum(lines.mapped("balance"))

    def _liquidity_balance(self, payment):
        accounts = payment.outstanding_account_id | payment.journal_id.default_account_id
        lines = payment.move_id.line_ids.filtered(lambda line: line.account_id in accounts)
        return sum(lines.mapped("balance"))

    def _assert_reconciled(self, lines):
        lines.invalidate_recordset()
        self.assertTrue(lines)
        self.assertTrue(all(lines.mapped("reconciled")))
        self.assertTrue(self.company.currency_id.is_zero(sum(lines.mapped("amount_residual"))))

    def test_supplier_payment_clears_central_and_two_branches(self):
        central_bill = self._invoice(self.company, "in_invoice", self.vendor, 80)
        yesterday_bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 100)
        fisa_bill = self._invoice(self.fisa, "in_invoice", self.vendor, 50)
        lines = self._debt_lines(central_bill | yesterday_bill | fisa_bill)
        payment = self._payment("supplier", self.vendor, lines)
        payment.action_post()

        self.assertEqual(payment.move_id.state, "posted")
        self.assertEqual(payment.move_id.company_id, self.company)
        self._assert_reconciled(lines)
        self.assertEqual(set(payment.kai_bridge_move_ids.company_id.ids), {self.yesterday.id, self.fisa.id})
        cc_lines = payment.move_id.line_ids.filtered(lambda line: line.account_id == self.cc_branches)
        self.assertEqual(set(cc_lines.mapped("kai_settled_company_id").ids), {self.yesterday.id, self.fisa.id})
        self.assertAlmostEqual(sum(cc_lines.mapped("balance")), 150.0, places=2)
        payable_lines = payment.move_id.line_ids.filtered(
            lambda line: line.account_id.account_type == "liability_payable"
        )
        self.assertEqual(len(payable_lines), 1)
        self.assertAlmostEqual(payable_lines.balance, 80.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.company, self.vendor, "liability_payable"), 0.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.yesterday, self.vendor, "liability_payable"), 0.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.fisa, self.vendor, "liability_payable"), 0.0, places=2)
        central_cc = self._account_balance(self.cc_branches, self.company)
        branch_cc = (
            self._account_balance(self.cc_central, self.yesterday)
            + self._account_balance(self.cc_central, self.fisa)
        )
        self.assertAlmostEqual(central_cc, 150.0, places=2)
        self.assertAlmostEqual(branch_cc, -150.0, places=2)
        self.assertAlmostEqual(central_cc + branch_cc, 0.0, places=2)
        self.assertAlmostEqual(self._liquidity_balance(payment), -230.0, places=2)

        payment.action_draft()
        lines.invalidate_recordset()
        self.assertFalse(payment.kai_bridge_move_ids)
        self.assertFalse(any(lines.mapped("reconciled")))
        self.assertAlmostEqual(self._partner_balance(self.yesterday, self.vendor, "liability_payable"), -100.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_branches, self.company), 0.0, places=2)

        payment.action_post()
        self._assert_reconciled(lines)
        self.assertEqual(len(payment.kai_bridge_move_ids), 2)
        self.assertAlmostEqual(self._partner_balance(self.fisa, self.vendor, "liability_payable"), 0.0, places=2)

    def test_customer_receipt_and_cancel(self):
        yesterday_invoice = self._invoice(self.yesterday, "out_invoice", self.customer, 120)
        fisa_invoice = self._invoice(self.fisa, "out_invoice", self.customer, 80)
        lines = self._debt_lines(yesterday_invoice | fisa_invoice)
        payment = self._payment("customer", self.customer, lines)
        payment.action_post()

        self._assert_reconciled(lines)
        self.assertEqual(len(payment.kai_bridge_move_ids), 2)
        self.assertFalse(payment.move_id.line_ids.filtered(
            lambda line: line.account_id.account_type == "asset_receivable"
        ))
        cc_lines = payment.move_id.line_ids.filtered(lambda line: line.account_id == self.cc_branches)
        self.assertAlmostEqual(sum(cc_lines.mapped("balance")), -200.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.yesterday, self.customer, "asset_receivable"), 0.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.fisa, self.customer, "asset_receivable"), 0.0, places=2)
        central_cc = self._account_balance(self.cc_branches, self.company)
        branch_cc = (
            self._account_balance(self.cc_central, self.yesterday)
            + self._account_balance(self.cc_central, self.fisa)
        )
        self.assertAlmostEqual(central_cc, -200.0, places=2)
        self.assertAlmostEqual(central_cc + branch_cc, 0.0, places=2)
        self.assertAlmostEqual(self._liquidity_balance(payment), 200.0, places=2)

        payment.action_cancel()
        lines.invalidate_recordset()
        self.assertEqual(payment.state, "canceled")
        self.assertFalse(payment.kai_bridge_move_ids)
        self.assertFalse(any(lines.mapped("reconciled")))
        self.assertAlmostEqual(self._partner_balance(self.yesterday, self.customer, "asset_receivable"), 120.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.fisa, self.customer, "asset_receivable"), 80.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_central, self.yesterday), 0.0, places=2)

    def test_supplier_credit_note_nets_inside_the_branch(self):
        bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 100)
        refund = self._invoice(self.yesterday, "in_refund", self.vendor, 30)
        fisa_bill = self._invoice(self.fisa, "in_invoice", self.vendor, 50)
        lines = self._debt_lines(bill | refund | fisa_bill)
        payment = self._payment("supplier", self.vendor, lines)
        self.assertAlmostEqual(payment.amount, 120.0, places=2)
        payment.action_post()

        self._assert_reconciled(lines)
        self.assertAlmostEqual(self._partner_balance(self.yesterday, self.vendor, "liability_payable"), 0.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.fisa, self.vendor, "liability_payable"), 0.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_branches, self.company), 120.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_central, self.yesterday), -70.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_central, self.fisa), -50.0, places=2)
        yesterday_bridge = payment.kai_bridge_move_ids.filtered(lambda move: move.company_id == self.yesterday)
        self.assertEqual(len(yesterday_bridge), 1)
        self.assertTrue(yesterday_bridge.line_ids.filtered(lambda line: line.kai_debt_move_line_id == self._debt_lines(bill)))
        self.assertTrue(yesterday_bridge.line_ids.filtered(lambda line: line.kai_debt_move_line_id == self._debt_lines(refund)))

    def test_single_company_payment_has_no_bridge(self):
        bill = self._invoice(self.company, "in_invoice", self.vendor, 90)
        lines = self._debt_lines(bill)
        payment = self._payment("supplier", self.vendor, lines)
        payment.action_post()

        self._assert_reconciled(lines)
        self.assertFalse(payment.kai_bridge_move_ids)
        self.assertFalse(payment.move_id.line_ids.filtered(lambda line: line.account_id == self.cc_branches))
        payable = payment.move_id.line_ids.filtered(lambda line: line.account_id.account_type == "liability_payable")
        self.assertEqual(len(payable), 1)
        self.assertAlmostEqual(payable.balance, 90.0, places=2)
        self.assertEqual(payable.partner_id, self.vendor.commercial_partner_id)
        self.assertAlmostEqual(self._partner_balance(self.company, self.vendor, "liability_payable"), 0.0, places=2)

    def test_other_legal_entity_is_rejected(self):
        other = self.env["res.company"].with_context(no_vat_validation=True).create({
            "name": "Otra SA",
            "parent_id": self.company.id,
            "vat": "30999999990",
            "country_id": self.company.country_id.id,
        })
        self.cr.precommit.run()
        self._ensure_general_journal(other)
        own_bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 40)
        foreign_bill = self._invoice(other, "in_invoice", self.vendor, 25)
        lines = self._debt_lines(own_bill | foreign_bill)
        payment = self._payment("supplier", self.vendor, self._debt_lines(own_bill))
        self.assertFalse(payment._kai_shares_legal_entity(lines.company_id))
        with self.assertRaises(UserError):
            payment.to_pay_move_line_ids = [Command.set(lines.ids)]
        self.assertNotIn(other, payment.to_pay_move_line_ids.company_id)

    def test_missing_branch_account_blocks_confirmation(self):
        bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 40)
        other = self._invoice(self.fisa, "in_invoice", self.vendor, 10)
        payment = self._payment("supplier", self.vendor, self._debt_lines(bill | other))
        self.company.kai_branch_current_account_id = False
        with self.assertRaises(UserError) as error:
            payment.action_post()
        self.assertIn("sucursales", str(error.exception))
        self.assertFalse(payment.kai_bridge_move_ids)

    def test_partial_payment_is_blocked(self):
        bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 100)
        other = self._invoice(self.fisa, "in_invoice", self.vendor, 50)
        payment = self._payment("supplier", self.vendor, self._debt_lines(bill | other))
        payment.amount = 120
        with self.assertRaises(UserError) as error:
            payment.action_post()
        self.assertIn("exactamente", str(error.exception))

    def test_foreign_currency_is_blocked(self):
        bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 40)
        other = self._invoice(self.fisa, "in_invoice", self.vendor, 10)
        payment = self._payment("supplier", self.vendor, self._debt_lines(bill | other))
        euro = self.env.ref("base.EUR")
        euro.active = True
        payment.currency_id = euro
        with self.assertRaises(UserError) as error:
            payment.action_post()
        self.assertIn("moneda", str(error.exception))
