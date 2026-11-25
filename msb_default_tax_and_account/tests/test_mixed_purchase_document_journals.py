# -*- coding: utf-8 -*-

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon

MIXED_X_FE_MESSAGE = "No se pueden facturar pedidos X y FE juntos"


@tagged('post_install', '-at_install')
class TestMixedPurchaseDocumentJournals(AccountTestInvoicingCommon):
    """TransactionCase (AccountTestInvoicingCommon) for mixed X/FE purchase bills.

    X is a purchase journal with ``l10n_latam_use_documents`` False. FE is the
    same field True. Orders without a journal are neither.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.sudo().group_ids |= cls.env.ref('purchase.group_purchase_user')
        cls.company = cls.env.company
        cls.vendor = cls.env['res.partner'].create({
            'name': 'Proveedor X/FE',
            'is_company': True,
            'supplier_rank': 1,
        })
        cls.product = cls.env['product.product'].create({
            'name': 'Insumo facturable',
            'type': 'consu',
            'purchase_method': 'purchase',
            'list_price': 100.0,
        })
        cls.journal_fe = cls.env['account.journal'].create({
            'name': 'Compras FE Test',
            'type': 'purchase',
            'code': 'ZFE1',
            'company_id': cls.company.id,
        })
        cls.journal_fe_b = cls.env['account.journal'].create({
            'name': 'Compras FE Test B',
            'type': 'purchase',
            'code': 'ZFE2',
            'company_id': cls.company.id,
        })
        cls.journal_x = cls.env['account.journal'].create({
            'name': 'Compras X Test',
            'type': 'purchase',
            'code': 'ZX01',
            'company_id': cls.company.id,
        })
        cls.has_document_flag = (
            'l10n_latam_use_documents' in cls.env['account.journal']._fields
        )
        if cls.has_document_flag:
            cls.journal_fe.l10n_latam_use_documents = True
            cls.journal_fe_b.l10n_latam_use_documents = True
            cls.journal_x.l10n_latam_use_documents = False

    def _require_document_flag(self):
        if not self.has_document_flag:
            self.skipTest('l10n_latam_use_documents no está instalado')

    def _order(self, journal):
        values = {'partner_id': self.vendor.id, 'company_id': self.company.id}
        if journal:
            values['journal_id'] = journal.id
        order = self.env['purchase.order'].create(values)
        self.env['purchase.order.line'].create({
            'order_id': order.id,
            'product_id': self.product.id,
            'name': self.product.name,
            'product_qty': 1,
            'product_uom_id': self.product.uom_id.id,
            'price_unit': 100.0,
        })
        order.button_confirm()
        return order

    def _assert_mixed_message(self, callable_bill, orders):
        before = self.env['account.move'].search_count([
            ('move_type', '=', 'in_invoice'),
            ('company_id', '=', self.company.id),
        ])
        with self.assertRaises(UserError) as caught:
            callable_bill()
        self.assertEqual(str(caught.exception), MIXED_X_FE_MESSAGE)
        orders.invalidate_recordset(['invoice_ids'])
        self.assertFalse(orders.invoice_ids)
        after = self.env['account.move'].search_count([
            ('move_type', '=', 'in_invoice'),
            ('company_id', '=', self.company.id),
        ])
        self.assertEqual(before, after)

    def test_mixed_x_and_fe_raises_before_any_bill(self):
        """X + FE on one action_create_invoice raises the exact message and creates nothing."""
        self._require_document_flag()
        # The order without a journal is neither X nor FE and must not cancel the error.
        orders = self._order(self.journal_fe) | self._order(self.journal_x) | self._order(False)
        self._assert_mixed_message(orders.action_create_invoice, orders)

    def test_only_fe_creates_invoice(self):
        """Several FE orders that share a journal create the vendor bill on that journal."""
        self._require_document_flag()
        orders = self._order(self.journal_fe) | self._order(self.journal_fe)
        orders.action_create_invoice()
        bills = orders.invoice_ids
        self.assertEqual(len(bills), 1)
        self.assertEqual(bills.move_type, 'in_invoice')
        self.assertEqual(bills.journal_id, self.journal_fe)
        self.assertEqual(bills.state, 'draft')

    def test_two_fe_journals_do_not_raise_singleton(self):
        """Two different FE journals must not crash on journal_id.id."""
        self._require_document_flag()
        orders = self._order(self.journal_fe) | self._order(self.journal_fe_b)
        orders.action_create_invoice()
        bills = orders.invoice_ids
        self.assertTrue(bills)
        self.assertTrue(set(bills.journal_id.ids) <= {self.journal_fe.id, self.journal_fe_b.id})

    def test_orders_without_journal_are_not_x_or_fe(self):
        """A PO without journal_id does not turn an FE (or X) selection into a mix."""
        self._require_document_flag()
        fe_orders = self._order(self.journal_fe) | self._order(False)
        fe_orders.action_create_invoice()
        self.assertTrue(fe_orders.invoice_ids)

        x_orders = self._order(self.journal_x) | self._order(False)
        x_orders.action_create_invoice()
        self.assertTrue(x_orders.invoice_ids)

    def test_wizard_one_bill_per_order_rejects_mixed_before_creating(self):
        """The per-order wizard path never sees the mix inside action_create_invoice."""
        self._require_document_flag()
        if 'kai.purchase.bill.wizard' not in self.env:
            self.skipTest('kai_purchase_bill_by_order no está instalado')
        orders = self._order(self.journal_x) | self._order(self.journal_fe)
        wizard = self.env['kai.purchase.bill.wizard'].create({
            'purchase_order_ids': [(6, 0, orders.ids)],
            'consolidate_by_vendor': False,
            'invoice_line_mode': 'products',
        })
        self._assert_mixed_message(wizard.action_create_bills, orders)
