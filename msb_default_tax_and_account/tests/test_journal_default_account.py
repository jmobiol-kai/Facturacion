# -*- coding: utf-8 -*-

from odoo import fields
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
import logging

_logger = logging.getLogger(__name__)


@tagged('post_install', '-at_install')
class TestJournalDefaultAccount(AccountTestInvoicingCommon):
    """Test que verifica la funcionalidad de cuenta por defecto del diario"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # AccountTestInvoicingCommon runs as a restricted accounting user; grant
        # the sales group so the SO->invoice flows can create sale.order records.
        cls.env.user.sudo().group_ids |= cls.env.ref('sales_team.group_sale_salesman')

        # Controlled tax environment: clear company default taxes so fixture
        # products carry exactly the taxes we assign (core links defaults additively).
        cls.env['res.company'].sudo().search([]).write({
            'account_sale_tax_id': False,
            'account_purchase_tax_id': False,
        })

        # Crear datos de prueba
        cls.company = cls.env.company

        # Crear diario de ventas
        cls.sales_journal = cls.env['account.journal'].create({
            'name': 'Test Sales Journal',
            'type': 'sale',
            'code': 'TSJ',
            'company_id': cls.company.id,
        })

        # Crear cuentas contables (B9: company_ids M2m, not company_id).
        cls.default_account = cls.env['account.account'].create({
            'name': 'Test Default Sales Account',
            'code': 'TEST001',
            'account_type': 'income',
            'company_ids': [(6, 0, cls.company.ids)],
        })

        cls.product_account = cls.env['account.account'].create({
            'name': 'Test Product Sales Account',
            'code': 'TEST002',
            'account_type': 'income',
            'company_ids': [(6, 0, cls.company.ids)],
        })

        # Crear impuesto
        cls.tax = cls.env['account.tax'].create({
            'name': 'Test Tax 21%',
            'amount': 21,
            'type_tax_use': 'sale',
            'company_id': cls.company.id,
        })

        # Crear producto con cuenta propia. B8: 'product' -> 'consu'.
        cls.product = cls.env['product.product'].create({
            'name': 'Test Product',
            'type': 'consu',
            'list_price': 100.0,
        })

        # Configurar cuenta del producto
        cls.product.product_tmpl_id.property_account_income_id = cls.product_account

        # Crear cliente
        cls.partner = cls.env['res.partner'].create({
            'name': 'Test Customer',
            'is_company': True,
        })

    def _new_invoice_line(self, journal):
        """Create an out_invoice with one product line on ``journal`` and return the line."""
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.partner.id,
            'journal_id': journal.id,
        })
        line = self.env['account.move.line'].with_context(check_move_validity=False).create({
            'move_id': invoice.id,
            'name': 'Test Line',
            'quantity': 1,
            'price_unit': 100.0,
            'display_type': 'product',
            'product_id': self.product.id,
        })
        return invoice, line

    def test_journal_account_override_enabled(self):
        """Enabled toggle: the POSTED product line account is the journal default account."""
        self.sales_journal.default_account_id = self.default_account
        self.sales_journal.usar_cuenta_defecto_facturas = True

        # Full SO -> invoice flow so we assert the account on the POSTED move line.
        so = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'journal_id': self.sales_journal.id,
            'company_id': self.company.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'product_uom_qty': 1,
                'price_unit': 100.0,
            })],
        })
        so.action_confirm()
        invoice = so._create_invoices()
        invoice.action_post()

        inv_line = invoice.invoice_line_ids.filtered('product_id')
        self.assertEqual(inv_line.account_id, self.default_account,
                         "Posted product line must use the journal default account when enabled")
        self.assertNotEqual(inv_line.account_id, self.product_account,
                            "Posted product line must NOT use the product account when override is on")

    def test_journal_account_override_disabled(self):
        """Disabled toggle: the POSTED line account derives from the product/category."""
        self.sales_journal.default_account_id = self.default_account
        self.sales_journal.usar_cuenta_defecto_facturas = False

        so = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'journal_id': self.sales_journal.id,
            'company_id': self.company.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'product_uom_qty': 1,
                'price_unit': 100.0,
            })],
        })
        so.action_confirm()
        invoice = so._create_invoices()
        invoice.action_post()

        inv_line = invoice.invoice_line_ids.filtered('product_id')
        expected_account = self.product.product_tmpl_id.with_company(
            self.company).get_product_accounts()['income']
        # Positive assertion: the resolved product income account is exactly ours.
        self.assertEqual(expected_account, self.product_account,
                         "Sanity: the product resolves to its configured income account")
        self.assertEqual(inv_line.account_id, expected_account,
                         "Posted line must use the product income account when override is off")
        self.assertNotEqual(inv_line.account_id, self.default_account,
                            "Posted line must NOT use the journal default account when disabled")

    def test_no_journal_default_account(self):
        """No journal default account: the line falls back to the product account, not blank."""
        self.sales_journal.default_account_id = False
        self.sales_journal.usar_cuenta_defecto_facturas = True

        invoice, line = self._new_invoice_line(self.sales_journal)

        # Simular onchange del producto (no journal account => early return, no override)
        line._onchange_product_id_set_journal_account()

        # The line must carry the product/category account, NOT be blanked out.
        self.assertEqual(line.account_id, self.product_account,
                         "Line must fall back to the product account when journal has no default account")
        self.assertTrue(line.account_id,
                        "Line account must not be blank")

    def test_user_can_override_journal_account(self):
        """User's manual account choice survives a posted document (not re-overridden)."""
        self.sales_journal.default_account_id = self.default_account
        self.sales_journal.usar_cuenta_defecto_facturas = True

        invoice, line = self._new_invoice_line(self.sales_journal)

        # The onchange suggests the journal account.
        line._onchange_product_id_set_journal_account()
        self.assertEqual(line.account_id, self.default_account)

        # User overrides to a custom account, then we POST and confirm persistence.
        custom_account = self.env['account.account'].create({
            'name': 'Custom Account',
            'code': 'CUSTOM',
            'account_type': 'income',
            'company_ids': [(6, 0, self.company.ids)],
        })
        line.account_id = custom_account
        invoice.invoice_date = fields.Date.context_today(invoice)
        invoice.action_post()

        posted_line = invoice.invoice_line_ids.filtered('product_id')
        self.assertEqual(posted_line.account_id, custom_account,
                         "The user's manually chosen account must persist through posting")
        self.assertNotEqual(posted_line.account_id, self.default_account,
                            "Posting must not silently revert to the journal default account")

    def test_purchase_invoice_not_affected(self):
        """Leak guard: the SALE toggle on a purchase journal must NOT override the purchase account."""
        # Construct the real leak risk: enable the SALE toggle (not the purchase one)
        # on a purchase journal. The override must be scoped to purchases via
        # usar_cuenta_defecto_compras, so the sale toggle must have no effect here.
        purchase_journal = self.env['account.journal'].create({
            'name': 'Test Purchase Journal',
            'type': 'purchase',
            'code': 'TPJ',
            'company_id': self.company.id,
        })
        purchase_journal.default_account_id = self.default_account
        purchase_journal.usar_cuenta_defecto_facturas = True   # SALE toggle on a purchase journal
        purchase_journal.usar_cuenta_defecto_compras = False   # purchase override OFF

        # Give the product an expense account so it has a real fallback.
        expense_account = self.env['account.account'].create({
            'name': 'Test Product Expense Account',
            'code': 'TESTEXP',
            'account_type': 'expense',
            'company_ids': [(6, 0, self.company.ids)],
        })
        self.product.product_tmpl_id.property_account_expense_id = expense_account

        invoice = self.env['account.move'].create({
            'move_type': 'in_invoice',
            'partner_id': self.partner.id,
            'journal_id': purchase_journal.id,
        })
        line = self.env['account.move.line'].with_context(check_move_validity=False).create({
            'move_id': invoice.id,
            'name': 'Test Line',
            'quantity': 1,
            'price_unit': 100.0,
            'display_type': 'product',
            'product_id': self.product.id,
        })

        line._onchange_product_id_set_journal_account()

        # The journal default account must NOT leak onto the purchase line, because
        # the purchase override (usar_cuenta_defecto_compras) is OFF.
        self.assertNotEqual(line.account_id, self.default_account,
                            "Sale toggle must not override the account on a purchase line")
        self.assertEqual(line.account_id, expense_account,
                         "Purchase line must keep the product expense account")

    def test_both_taxes_and_account_override(self):
        """Tax mapping + account override together, proven on the POSTED document money."""
        self.sales_journal.default_account_id = self.default_account
        self.sales_journal.usar_cuenta_defecto_facturas = True

        # Product carries a 10% tax; journal maps it to the 21% journal tax.
        product_tax = self.env['account.tax'].create({
            'name': 'Product Tax 10%',
            'amount': 10,
            'type_tax_use': 'sale',
            'company_id': self.company.id,
        })
        self.product.taxes_id = product_tax

        self.env['account.journal.tax.mapping'].create({
            'journal_id': self.sales_journal.id,
            'original_tax_id': product_tax.id,
            'replacement_tax_id': self.tax.id,  # 21% journal tax
            'tax_type': 'sale',
        })

        invoice, line = self._new_invoice_line(self.sales_journal)

        # Apply account override (onchange) and tax mapping (computed taxes).
        line._onchange_product_id_set_journal_account()
        line.tax_ids = line._get_computed_taxes()

        # Line-level expectations.
        self.assertEqual(line.account_id, self.default_account,
                         "Line must use the journal default account")
        self.assertEqual(line.tax_ids, self.tax,
                         "Line must use the mapped journal tax (21%)")

        # FINANCIAL EFFECT: post and assert the mapped 21% drives money (NOT 10%),
        # and the product line account is the journal default account.
        invoice.invoice_date = fields.Date.context_today(invoice)
        invoice.action_post()

        self.assertAlmostEqual(invoice.amount_tax, 21.0,
                               msg="Mapped 21% must drive the booked tax, not product 10%")
        self.assertNotAlmostEqual(invoice.amount_tax, 10.0)
        self.assertAlmostEqual(invoice.amount_total, 121.0)
        self.assertTrue(
            invoice.line_ids.filtered(lambda l: l.tax_line_id == self.tax),
            "A GL tax line for the mapped 21% tax must exist",
        )
        self.assertFalse(
            invoice.line_ids.filtered(lambda l: l.tax_line_id == product_tax),
            "No GL tax line for the original 10% tax may exist",
        )
        posted_line = invoice.invoice_line_ids.filtered('product_id')
        self.assertEqual(posted_line.account_id, self.default_account,
                         "Posted product line must use the journal default account")
