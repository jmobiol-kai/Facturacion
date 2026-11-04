from odoo import Command, fields
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged('post_install', '-at_install')
class TestKaiPartnerAccountSummary(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Kai Resumen',
        })
        cls.child = cls.env['res.partner'].create({
            'name': 'Kai Resumen hijo',
            'parent_id': cls.partner.id,
        })
        cls.other = cls.env['res.partner'].create({
            'name': 'Kai Otro',
        })

    def _move(self, move_type, partner, amount, post=True):
        purchase = move_type in ('in_invoice', 'in_refund', 'in_receipt')
        account = self.company_data[
            'default_account_expense' if purchase else 'default_account_revenue'
        ]
        today = fields.Date.today()
        return self._create_invoice(
            move_type=move_type,
            partner_id=partner.id,
            invoice_date=today,
            date=today,
            invoice_line_ids=[
                self._prepare_invoice_line(
                    price_unit=amount,
                    quantity=1.0,
                    name='Movimiento',
                    account_id=account.id,
                    tax_ids=[Command.clear()],
                ),
            ],
            post=post,
        )

    def _payment(self, partner, amount, payment_type, partner_type, post=True):
        payment = self.env['account.payment'].create({
            'amount': amount,
            'payment_type': payment_type,
            'partner_type': partner_type,
            'partner_id': partner.id,
            'journal_id': self.company_data['default_journal_bank'].id,
            'date': fields.Date.today(),
        })
        if post:
            payment.action_post()
        return payment

    def _assert_sides(self, line, debit, credit):
        self.assertAlmostEqual(line.debit, debit, places=2)
        self.assertAlmostEqual(line.credit, credit, places=2)
        self.assertAlmostEqual(line.amount, debit - credit, places=2)

    def test_partner_form_shows_movimientos_button(self):
        arch = self.env['res.partner'].get_view(view_type='form')['arch']
        self.assertIn('action_kai_view_account_summary', arch)
        self.assertIn('kai_total_movimientos', arch)
        self.assertIn('Movimientos', arch)

    def test_list_shows_debe_haber_saldo(self):
        line_model = self.env['kai.partner.account.summary.line']
        info = line_model.fields_get(['debit', 'credit', 'amount'])
        self.assertEqual(info['debit']['string'], 'Debe')
        self.assertEqual(info['credit']['string'], 'Haber')
        self.assertEqual(info['amount']['string'], 'Saldo')
        self.assertNotEqual(info['amount']['string'], 'Importe')

        list_arch = line_model.get_view(view_type='list')['arch']
        form_arch = line_model.get_view(view_type='form')['arch']
        for arch in (list_arch, form_arch):
            self.assertIn('name="debit"', arch)
            self.assertIn('name="credit"', arch)
            self.assertIn('name="amount"', arch)
            self.assertNotIn('Importe', arch)
        self.assertLess(list_arch.index('name="debit"'), list_arch.index('name="credit"'))
        self.assertLess(list_arch.index('name="credit"'), list_arch.index('name="amount"'))

    def test_total_matches_posted_documents_and_opens_list(self):
        invoice = self._move('out_invoice', self.partner, 100.0)
        refund = self._move('out_refund', self.partner, 30.0)
        bill = self._move('in_invoice', self.partner, 20.0)
        vendor_refund = self._move('in_refund', self.partner, 5.0)
        sale_receipt = self._move('out_receipt', self.partner, 10.0)
        purchase_receipt = self._move('in_receipt', self.partner, 8.0)
        child_invoice = self._move('out_invoice', self.child, 12.0)
        draft = self._move('out_invoice', self.partner, 500.0, post=False)
        other_invoice = self._move('out_invoice', self.other, 40.0)

        cobro = self._payment(self.partner, 15.0, 'inbound', 'customer')
        pago = self._payment(self.partner, 7.0, 'outbound', 'supplier')
        draft_payment = self._payment(self.partner, 9.0, 'inbound', 'customer', post=False)
        customer_outbound = self._payment(self.partner, 4.0, 'outbound', 'customer')
        vendor_inbound = self._payment(self.partner, 3.0, 'inbound', 'supplier')

        self.assertAlmostEqual(invoice.amount_total_signed, 100.0, places=2)
        self.assertAlmostEqual(refund.amount_total_signed, -30.0, places=2)
        self.assertAlmostEqual(bill.amount_total_signed, -20.0, places=2)
        self.assertAlmostEqual(vendor_refund.amount_total_signed, 5.0, places=2)
        self.assertAlmostEqual(sale_receipt.amount_total_signed, 10.0, places=2)
        self.assertAlmostEqual(purchase_receipt.amount_total_signed, -8.0, places=2)
        self.assertAlmostEqual(child_invoice.amount_total_signed, 12.0, places=2)
        self.assertAlmostEqual(cobro.amount_company_currency_signed, 15.0, places=2)
        self.assertAlmostEqual(pago.amount_company_currency_signed, -7.0, places=2)
        self.assertEqual(child_invoice.partner_id, self.child)
        self.assertEqual(cobro.move_id.move_type, 'entry')
        self.assertEqual(cobro.move_id.state, 'posted')

        # Side comes from the document, magnitude from the company-currency
        # total. Odoo signs cobros like ventas and pagos like compras; this
        # statement puts them on the opposite side, so do not sum those signs.
        # Debe: out_invoice, out_receipt, in_refund, pago.
        # Haber: out_refund, in_invoice, in_receipt, cobro.
        included = (
            abs(invoice.amount_total_signed)
            - abs(refund.amount_total_signed)
            - abs(bill.amount_total_signed)
            + abs(vendor_refund.amount_total_signed)
            + abs(sale_receipt.amount_total_signed)
            - abs(purchase_receipt.amount_total_signed)
            + abs(child_invoice.amount_total_signed)
            - abs(cobro.amount_company_currency_signed)
            + abs(pago.amount_company_currency_signed)
        )
        self.assertAlmostEqual(included, 100 - 30 - 20 + 5 + 10 - 8 + 12 - 15 + 7, places=2)
        self.assertAlmostEqual(self.partner.kai_total_movimientos, included, places=2)
        self.assertAlmostEqual(self.child.kai_total_movimientos, 12.0, places=2)
        self.assertAlmostEqual(self.other.kai_total_movimientos, 40.0, places=2)

        action = self.partner.action_kai_view_account_summary()
        self.assertEqual(action['res_model'], 'kai.partner.account.summary.line')
        lines = self.env['kai.partner.account.summary.line'].search(action['domain'])
        self.assertAlmostEqual(sum(lines.mapped('amount')), included, places=2)
        self.assertAlmostEqual(
            sum(lines.mapped('debit')) - sum(lines.mapped('credit')),
            included,
            places=2,
        )

        self.assertTrue(lines.filtered(lambda line: line.move_id == invoice and not line.payment_id))
        self.assertTrue(lines.filtered(lambda line: line.payment_id == cobro))
        self.assertTrue(lines.filtered(lambda line: line.payment_id == pago))
        self.assertFalse(lines.filtered(lambda line: line.move_id == draft))
        self.assertFalse(lines.filtered(lambda line: line.move_id == other_invoice))
        self.assertFalse(lines.filtered(lambda line: line.payment_id == draft_payment))
        self.assertFalse(lines.filtered(lambda line: line.payment_id == customer_outbound))
        self.assertFalse(lines.filtered(lambda line: line.payment_id == vendor_inbound))

        invoice_line = lines.filtered(lambda line: line.move_id == invoice and not line.payment_id)
        opened = invoice_line.action_open_document()
        self.assertEqual(opened['res_model'], 'account.move')
        self.assertEqual(opened['res_id'], invoice.id)

        cobro_line = lines.filtered(lambda line: line.payment_id == cobro)
        opened = cobro_line.action_open_document()
        self.assertEqual(opened['res_model'], 'account.payment')
        self.assertEqual(opened['res_id'], cobro.id)
        self.assertEqual(cobro_line.document_type, 'customer_inbound')
        self._assert_sides(cobro_line, 0.0, 15.0)
        pago_line = lines.filtered(lambda line: line.payment_id == pago)
        self.assertEqual(pago_line.document_type, 'supplier_outbound')
        self._assert_sides(pago_line, 7.0, 0.0)
        # Sales sit on Debe. Customer credit notes and cobros sit on Haber.
        self._assert_sides(invoice_line, 100.0, 0.0)
        self._assert_sides(
            lines.filtered(lambda line: line.move_id == refund and not line.payment_id),
            0.0,
            30.0,
        )
        self._assert_sides(
            lines.filtered(lambda line: line.move_id == sale_receipt and not line.payment_id),
            10.0,
            0.0,
        )
        # Purchases sit on Haber. Vendor credit notes and pagos sit on Debe.
        self._assert_sides(
            lines.filtered(lambda line: line.move_id == bill and not line.payment_id),
            0.0,
            20.0,
        )
        self._assert_sides(
            lines.filtered(lambda line: line.move_id == purchase_receipt and not line.payment_id),
            0.0,
            8.0,
        )
        self._assert_sides(
            lines.filtered(lambda line: line.move_id == vendor_refund and not line.payment_id),
            5.0,
            0.0,
        )
        self._assert_sides(
            lines.filtered(lambda line: line.move_id == child_invoice and not line.payment_id),
            12.0,
            0.0,
        )

    def test_customer_invoice_is_debit_cobro_and_credit_note_are_credit(self):
        partner = self.env['res.partner'].create({'name': 'Kai Solo ventas'})
        self._move('out_invoice', partner, 100.0)
        self._move('out_receipt', partner, 10.0)
        self._payment(partner, 40.0, 'inbound', 'customer')
        # Ventas on Debe, cobro on Haber: 110 − 40.
        self.assertAlmostEqual(partner.kai_total_movimientos, 70.0, places=2)
        self.assertGreater(partner.kai_total_movimientos, 0.0)

        lines = self.env['kai.partner.account.summary.line'].search([
            ('partner_id', '=', partner.id),
        ])
        self.assertAlmostEqual(sum(lines.mapped('debit')), 110.0, places=2)
        self.assertAlmostEqual(sum(lines.mapped('credit')), 40.0, places=2)

        self._move('out_refund', partner, 25.0)
        partner.invalidate_recordset(['kai_total_movimientos'])
        self.assertAlmostEqual(partner.kai_total_movimientos, 45.0, places=2)
        lines = self.env['kai.partner.account.summary.line'].search([
            ('partner_id', '=', partner.id),
        ])
        self.assertAlmostEqual(sum(lines.mapped('debit')), 110.0, places=2)
        self.assertAlmostEqual(sum(lines.mapped('credit')), 65.0, places=2)
        self.assertAlmostEqual(sum(lines.mapped('amount')), 45.0, places=2)

    def test_vendor_bill_is_credit_pago_and_credit_note_are_debit(self):
        partner = self.env['res.partner'].create({'name': 'Kai Solo compras'})
        self._move('in_invoice', partner, 80.0)
        self._move('in_receipt', partner, 8.0)
        self._payment(partner, 20.0, 'outbound', 'supplier')
        # Compras on Haber, pago on Debe: 20 − 88.
        self.assertAlmostEqual(partner.kai_total_movimientos, -68.0, places=2)
        self.assertLess(partner.kai_total_movimientos, 0.0)

        lines = self.env['kai.partner.account.summary.line'].search([
            ('partner_id', '=', partner.id),
        ])
        self.assertAlmostEqual(sum(lines.mapped('debit')), 20.0, places=2)
        self.assertAlmostEqual(sum(lines.mapped('credit')), 88.0, places=2)

        self._move('in_refund', partner, 10.0)
        partner.invalidate_recordset(['kai_total_movimientos'])
        self.assertAlmostEqual(partner.kai_total_movimientos, -58.0, places=2)
        lines = self.env['kai.partner.account.summary.line'].search([
            ('partner_id', '=', partner.id),
        ])
        self.assertAlmostEqual(sum(lines.mapped('debit')), 30.0, places=2)
        self.assertAlmostEqual(sum(lines.mapped('credit')), 88.0, places=2)
        self.assertAlmostEqual(sum(lines.mapped('amount')), -58.0, places=2)
