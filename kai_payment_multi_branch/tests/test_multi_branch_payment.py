from datetime import timedelta
from unittest.mock import patch

from odoo import Command, _, fields
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

    def _legacy_payment(self, partner_type, partner, lines):
        payment = self._payment(partner_type, partner, lines)
        payment.with_context(kai_skip_branch_bridge=True).action_post()
        return payment

    def _wizard(self, payments):
        return self.env["kai.payment.regularize.wizard"].with_context(
            active_model="account.payment",
            active_ids=payments.ids,
        ).create({})

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

    def test_single_branch_payment_clears_the_branch_not_the_central_payable(self):
        bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 100)
        lines = self._debt_lines(bill)
        payment = self._payment("supplier", self.vendor, lines)
        payment.action_post()

        self._assert_reconciled(lines)
        self.assertEqual(payment.kai_bridge_move_ids.company_id, self.yesterday)
        self.assertFalse(payment.move_id.line_ids.filtered(
            lambda line: line.account_id.account_type == "liability_payable"
        ))
        cc_lines = payment.move_id.line_ids.filtered(lambda line: line.account_id == self.cc_branches)
        self.assertEqual(cc_lines.kai_settled_company_id, self.yesterday)
        self.assertAlmostEqual(cc_lines.balance, 100.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.company, self.vendor, "liability_payable"), 0.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.yesterday, self.vendor, "liability_payable"), 0.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_branches, self.company), 100.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_central, self.yesterday), -100.0, places=2)
        self.assertAlmostEqual(self._liquidity_balance(payment), -100.0, places=2)

    def test_regularize_legacy_supplier_matches_a_new_payment(self):
        bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 100)
        lines = self._debt_lines(bill)
        payment = self._legacy_payment("supplier", self.vendor, lines)
        self.assertFalse(payment.kai_bridge_move_ids)
        self.assertTrue(lines.reconciled)
        self.assertAlmostEqual(self._partner_balance(self.company, self.vendor, "liability_payable"), 100.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.yesterday, self.vendor, "liability_payable"), -100.0, places=2)
        liquidity_before = self._liquidity_balance(payment)

        wizard = self._wizard(payment)
        self.assertTrue(wizard.line_ids.eligible)
        self.assertEqual(wizard.date, payment.date)
        entry_date = payment.date + timedelta(days=4)
        wizard.date = entry_date
        wizard.action_regularize()

        lines.invalidate_recordset()
        self._assert_reconciled(lines)
        self.assertAlmostEqual(self._partner_balance(self.company, self.vendor, "liability_payable"), 0.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.yesterday, self.vendor, "liability_payable"), 0.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_branches, self.company), 100.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_central, self.yesterday), -100.0, places=2)
        self.assertAlmostEqual(self._liquidity_balance(payment), liquidity_before, places=2)
        self.assertTrue(payment.kai_bridge_move_ids)
        self.assertEqual(set(payment.kai_bridge_move_ids.mapped("date")), {entry_date})
        self.assertEqual(set(payment.kai_bridge_move_ids.company_id), {self.company, self.yesterday})
        branch_match = (lines.matched_debit_ids | lines.matched_credit_ids)
        branch_counterparts = (branch_match.debit_move_id | branch_match.credit_move_id) - lines
        self.assertTrue(branch_counterparts)
        self.assertTrue(all(line.company_id == self.yesterday for line in branch_counterparts))
        self.assertTrue(all(line.move_id.kai_origin_payment_id == payment for line in branch_counterparts))
        payable = payment.move_id.line_ids.filtered(lambda line: line.account_id.account_type == "liability_payable")
        payable_match = (payable.matched_debit_ids | payable.matched_credit_ids)
        payable_counterparts = (payable_match.debit_move_id | payable_match.credit_move_id) - payable
        self.assertTrue(all(line.company_id == self.company for line in payable_counterparts))
        self.assertTrue(all(line.move_id.kai_origin_payment_id == payment for line in payable_counterparts))

        again = self._wizard(payment)
        self.assertFalse(again.line_ids.eligible)
        self.assertIn("regularizado", again.line_ids.reason)
        with self.assertRaises(UserError):
            again.action_regularize()
        self.assertEqual(len(payment.kai_bridge_move_ids), 2)

        payment.action_draft()
        lines.invalidate_recordset()
        self.assertFalse(payment.kai_bridge_move_ids)
        self.assertFalse(lines.reconciled)
        self.assertAlmostEqual(self._partner_balance(self.yesterday, self.vendor, "liability_payable"), -100.0, places=2)

        payment.action_post()
        self._assert_reconciled(lines)
        self.assertEqual(payment.kai_bridge_move_ids.company_id, self.yesterday)
        self.assertFalse(payment.move_id.line_ids.filtered(
            lambda line: line.account_id.account_type == "liability_payable"
        ))
        self.assertAlmostEqual(self._partner_balance(self.company, self.vendor, "liability_payable"), 0.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_branches, self.company), 100.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_central, self.yesterday), -100.0, places=2)

    def test_regularize_legacy_customer_receipt(self):
        invoice = self._invoice(self.yesterday, "out_invoice", self.customer, 80)
        lines = self._debt_lines(invoice)
        payment = self._legacy_payment("customer", self.customer, lines)
        self.assertAlmostEqual(self._partner_balance(self.company, self.customer, "asset_receivable"), -80.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.yesterday, self.customer, "asset_receivable"), 80.0, places=2)

        wizard = self._wizard(payment)
        self.assertEqual(wizard.date, payment.date)
        wizard.action_regularize()

        self._assert_reconciled(lines)
        self.assertAlmostEqual(self._partner_balance(self.company, self.customer, "asset_receivable"), 0.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.yesterday, self.customer, "asset_receivable"), 0.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_branches, self.company), -80.0, places=2)
        self.assertAlmostEqual(self._account_balance(self.cc_central, self.yesterday), 80.0, places=2)
        self.assertAlmostEqual(self._liquidity_balance(payment), 80.0, places=2)

    def test_regularize_wizard_previews_and_skips_rejected_payments(self):
        branch_bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 60)
        own_bill = self._invoice(self.company, "in_invoice", self.vendor, 25)
        legacy = self._legacy_payment("supplier", self.vendor, self._debt_lines(branch_bill))
        own_payment = self._payment("supplier", self.vendor, self._debt_lines(own_bill))
        own_payment.action_post()

        wizard = self._wizard(legacy | own_payment)
        self.assertEqual(len(wizard.line_ids), 2)
        by_payment = {line.payment_id: line for line in wizard.line_ids}
        self.assertTrue(by_payment[legacy].eligible)
        self.assertFalse(by_payment[own_payment].eligible)
        self.assertIn("otra sucursal", by_payment[own_payment].reason)
        other_vendor = self.env["res.partner"].create({"name": "Proveedor bloqueado"})
        blocked = self._legacy_payment(
            "supplier",
            other_vendor,
            self._debt_lines(self._invoice(self.fisa, "in_invoice", other_vendor, 15)),
        )
        euro = self.env.ref("base.EUR")
        euro.active = True
        blocked.currency_id = euro
        self.assertIn("moneda extranjera", blocked._kai_regularize_block_reason())
        blocked.currency_id = self.company.currency_id
        with patch.object(type(blocked), "_kai_has_withholdings", lambda self: True):
            self.assertIn("retenciones", blocked._kai_regularize_block_reason())
        method = self.env["account.payment.method"].search([("code", "=", "own_checks")], limit=1)
        self.assertTrue(method)
        blocked.payment_method_line_id = self.env["account.payment.method.line"].create({
            "name": "Cheque propio",
            "payment_method_id": method.id,
            "journal_id": blocked.journal_id.id,
        })
        self.assertIn("cheques", blocked._kai_regularize_block_reason())

        wizard.action_regularize()
        self.assertTrue(legacy.kai_bridge_move_ids)
        self.assertFalse(own_payment.kai_bridge_move_ids)
        self.assertAlmostEqual(self._partner_balance(self.yesterday, self.vendor, "liability_payable"), 0.0, places=2)
        self.assertAlmostEqual(self._partner_balance(self.company, self.vendor, "liability_payable"), 0.0, places=2)

    def test_bridge_journal_uses_miscellaneous_not_point_of_sale(self):
        company = self.yesterday
        self.env["account.journal"].create({
            "name": "Punto de venta",
            "code": "POSS",
            "type": "general",
            "company_id": company.id,
            "sequence": 1,
        })
        misce = self.env["account.journal"].create({
            "name": "Operaciones misceláneas",
            "code": "MISCE",
            "type": "general",
            "company_id": company.id,
            "sequence": 50,
        })
        chart = self.env["account.payment"]._kai_default_misc_journal(company)
        if chart and chart != misce:
            chart.active = False
        self.assertEqual(self.env["account.payment"]._kai_default_misc_journal(company), misce)

        bill = self._invoice(company, "in_invoice", self.vendor, 40)
        payment = self._payment("supplier", self.vendor, self._debt_lines(bill))
        payment.action_post()
        self.assertEqual(payment.kai_bridge_move_ids.journal_id, misce)

        custom = self.env["account.journal"].create({
            "name": "Puentes sucursal",
            "code": "PUEN",
            "type": "general",
            "company_id": company.id,
        })
        company.kai_bridge_journal_id = custom
        other = self._invoice(company, "in_invoice", self.vendor, 15)
        second = self._payment("supplier", self.vendor, self._debt_lines(other))
        second.action_post()
        self.assertEqual(second.kai_bridge_move_ids.journal_id, custom)

        self.fisa.kai_bridge_journal_id = False
        for journal in self.env["account.journal"].search([
            ("company_id", "=", self.fisa.id),
            ("type", "=", "general"),
        ]):
            if not self.env["account.payment"]._kai_journal_is_reserved(journal):
                journal.active = False
        self.env["account.journal"].create({
            "name": "Punto de venta",
            "code": "POSS",
            "type": "general",
            "company_id": self.fisa.id,
            "sequence": 1,
        })
        fisa_bill = self._invoice(self.fisa, "in_invoice", self.vendor, 10)
        blocked = self._payment("supplier", self.vendor, self._debt_lines(fisa_bill))
        with self.assertRaises(UserError) as error:
            blocked.action_post()
        self.assertIn("misceláneas", str(error.exception))

    def _assert_paid_tab(self, payment, lines):
        payment.invalidate_recordset(["matched_move_line_ids", "matched_amount", "unmatched_amount"])
        self.assertEqual(set(payment.matched_move_line_ids.ids), set(lines.ids))
        self.assertAlmostEqual(payment.matched_amount, payment.amount, places=2)
        self.assertTrue(payment.currency_id.is_zero(payment.unmatched_amount))

    def test_paid_tab_includes_branch_invoices(self):
        yesterday_bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 100)
        fisa_bill = self._invoice(self.fisa, "in_invoice", self.vendor, 50)
        lines = self._debt_lines(yesterday_bill | fisa_bill)
        payment = self._payment("supplier", self.vendor, lines)
        payment.action_post()
        self._assert_paid_tab(payment, lines)

        payment.action_draft()
        payment.invalidate_recordset(["matched_move_line_ids", "matched_amount", "unmatched_amount"])
        self.assertFalse(payment.matched_move_line_ids)
        self.assertTrue(payment.currency_id.is_zero(payment.matched_amount))
        self.assertTrue(payment.currency_id.is_zero(payment.unmatched_amount))
        self.assertFalse(lines.kai_settled_by_company_ids)

    def test_paid_tab_mixed_central_and_branch(self):
        central_bill = self._invoice(self.company, "in_invoice", self.vendor, 80)
        yesterday_bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 120)
        lines = self._debt_lines(central_bill | yesterday_bill)
        payment = self._payment("supplier", self.vendor, lines)
        payment.action_post()
        self._assert_paid_tab(payment, lines)
        self.assertFalse(payment.matched_move_line_ids.filtered(
            lambda line: line.move_id.kai_origin_payment_id == payment
        ))

    def test_payment_receipt_with_only_central_company_selected(self):
        yesterday_bill = self._invoice(self.yesterday, "in_invoice", self.vendor, 70)
        fisa_bill = self._invoice(self.fisa, "in_invoice", self.vendor, 30)
        lines = self._debt_lines(yesterday_bill | fisa_bill)
        payment = self._payment("supplier", self.vendor, lines)
        payment.action_post()
        user = self.env["res.users"].create({
            "name": "Contable central",
            "login": "contable_central_mb",
            "email": "contable_central_mb@example.com",
            "company_id": self.company.id,
            "company_ids": [Command.set(self.company.ids)],
            "group_ids": [Command.set([
                self.env.ref("base.group_user").id,
                self.env.ref("account.group_account_user").id,
            ])],
        })
        restricted = payment.with_user(user).with_context(allowed_company_ids=[self.company.id])
        self.assertEqual(set(restricted.matched_move_line_ids.ids), set(lines.ids))
        paid_tab = restricted.web_read({
            "matched_move_line_ids": {"fields": {"id": {}}},
        })
        shown_ids = {row["id"] for row in paid_tab[0]["matched_move_line_ids"]}
        self.assertEqual(shown_ids, set(lines.ids))
        self.assertAlmostEqual(restricted.matched_amount, 100.0, places=2)
        self.assertTrue(restricted.currency_id.is_zero(restricted.unmatched_amount))
        html = self.env["ir.actions.report"].with_user(user).with_context(
            allowed_company_ids=[self.company.id]
        )._render_template(
            "account_payment_pro.report_payment_receipt_document_pro",
            {"o": restricted, "_": _},
        )
        text = html.decode() if isinstance(html, bytes) else str(html)
        self.assertIn(yesterday_bill.name, text)
        self.assertIn(fisa_bill.name, text)
