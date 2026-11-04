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

    def test_partner_form_shows_movimientos_button(self):
        arch = self.env['res.partner'].get_view(view_type='form')['arch']
        self.assertIn('action_kai_view_account_summary', arch)
        self.assertIn('kai_total_movimientos', arch)
        self.assertIn('Movimientos', arch)

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
        self.assertEqual(child_invoice.partner_id, self.child)
        self.assertEqual(cobro.move_id.move_type, 'entry')
        self.assertEqual(cobro.move_id.state, 'posted')

        included = (
            abs(invoice.amount_total_signed)
            + abs(refund.amount_total_signed)
            + abs(bill.amount_total_signed)
            + abs(vendor_refund.amount_total_signed)
            + abs(sale_receipt.amount_total_signed)
            + abs(purchase_receipt.amount_total_signed)
            + abs(child_invoice.amount_total_signed)
            + abs(cobro.amount_company_currency_signed)
            + abs(pago.amount_company_currency_signed)
        )
        # Refunds and vendor bills add their absolute total. They do not net off.
        self.assertAlmostEqual(included, 100 + 30 + 20 + 5 + 10 + 8 + 12 + 15 + 7, places=2)
        self.assertAlmostEqual(self.partner.kai_total_movimientos, included, places=2)
        self.assertAlmostEqual(self.child.kai_total_movimientos, 12.0, places=2)
        self.assertAlmostEqual(self.other.kai_total_movimientos, 40.0, places=2)

        action = self.partner.action_kai_view_account_summary()
        self.assertEqual(action['res_model'], 'kai.partner.account.summary.line')
        lines = self.env['kai.partner.account.summary.line'].search(action['domain'])
        self.assertAlmostEqual(sum(lines.mapped('amount')), included, places=2)

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
        self.assertEqual(
            lines.filtered(lambda line: line.payment_id == pago).document_type,
            'supplier_outbound',
        )
