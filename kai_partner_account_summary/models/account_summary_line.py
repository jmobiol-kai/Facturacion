from odoo import _, fields, models
from odoo.exceptions import UserError
from odoo.tools import SQL


# Posted customer and vendor invoices, refunds and receipts.
# Receipts are account.move documents. Payments are separate account.payment
# records whose journal entry is an 'entry', so they are not counted twice.
#
# The column is chosen from the document nature, not from the sign Odoo stores
# on amount_total_signed. That sign puts cobros on the same side as ventas and
# pagos on the same side as compras, which is the opposite of this statement.
# Debe (adds to the line saldo): customer invoices and sale receipts, vendor
# credit notes (less of what we owe) and supplier payments.
# Haber (subtracts): vendor bills and purchase receipts, customer credit notes
# (less of what the partner owes us) and customer payments.
DEBIT_MOVE_TYPES = (
    'out_invoice',
    'out_receipt',
    'in_refund',
)
CREDIT_MOVE_TYPES = (
    'out_refund',
    'in_invoice',
    'in_receipt',
)
MOVE_TYPES = DEBIT_MOVE_TYPES + CREDIT_MOVE_TYPES

# Odoo 19 posts a payment by moving it to in_process or paid and posting move_id.
POSTED_PAYMENT_STATES = ('in_process', 'paid')


class KaiPartnerAccountSummaryLine(models.Model):
    _name = 'kai.partner.account.summary.line'
    _description = 'Línea del resumen de cuenta del contacto'
    _auto = False
    _rec_name = 'name'
    _order = 'date desc, name desc, id desc'

    # Flush the documents this query reads before selecting, same idea as
    # account.invoice.report.
    _depends = {
        'account.move': [
            'name', 'state', 'move_type', 'partner_id', 'company_id',
            'date', 'amount_total_signed',
        ],
        'account.payment': [
            'name', 'state', 'partner_id', 'company_id', 'date',
            'payment_type', 'partner_type', 'amount_company_currency_signed',
            'move_id',
        ],
        'res.company': ['currency_id'],
    }

    name = fields.Char(string='Número', readonly=True)
    date = fields.Date(string='Fecha', readonly=True)
    document_type = fields.Selection(
        selection=[
            ('out_invoice', 'Factura de cliente'),
            ('out_refund', 'Nota de crédito de cliente'),
            ('in_invoice', 'Factura de proveedor'),
            ('in_refund', 'Nota de crédito de proveedor'),
            ('out_receipt', 'Recibo de venta'),
            ('in_receipt', 'Recibo de compra'),
            ('customer_inbound', 'Cobro'),
            ('supplier_outbound', 'Pago'),
        ],
        string='Tipo',
        readonly=True,
    )
    partner_id = fields.Many2one('res.partner', string='Contacto', readonly=True)
    company_id = fields.Many2one('res.company', string='Compañía', readonly=True)
    currency_id = fields.Many2one('res.currency', string='Moneda', readonly=True)
    debit = fields.Monetary(
        string='Debe',
        currency_field='currency_id',
        readonly=True,
        help=(
            'Ventas y pagos a proveedor, en moneda de la compañía: factura de '
            'cliente, recibo de venta, pago y nota de crédito de proveedor. '
            'Siempre cero o positivo.'
        ),
    )
    credit = fields.Monetary(
        string='Haber',
        currency_field='currency_id',
        readonly=True,
        help=(
            'Compras y cobros, en moneda de la compañía: factura de proveedor, '
            'recibo de compra, cobro y nota de crédito de cliente. Siempre '
            'cero o positivo.'
        ),
    )
    amount = fields.Monetary(
        string='Saldo',
        currency_field='currency_id',
        readonly=True,
        help=(
            'Saldo de la línea: Debe menos Haber. Positivo en ventas y pagos '
            'a proveedor, negativo en compras y cobros. La suma de la columna '
            'es el total del botón Movimientos. La factura de cliente queda en '
            'el Debe y su cobro en el Haber. No es un saldo acumulado ni el '
            'saldo del mayor: solo entran estos comprobantes y pagos publicados.'
        ),
    )
    move_id = fields.Many2one('account.move', string='Comprobante', readonly=True)
    payment_id = fields.Many2one('account.payment', string='Pago', readonly=True)
    document_ref = fields.Reference(
        selection=[
            ('account.move', 'Comprobante'),
            ('account.payment', 'Pago'),
        ],
        string='Documento',
        readonly=True,
    )

    @property
    def _table_query(self):
        return SQL(
            """
            SELECT
                move.id AS id,
                move.name AS name,
                move.date AS date,
                move.move_type AS document_type,
                move.partner_id AS partner_id,
                move.company_id AS company_id,
                company.currency_id AS currency_id,
                -- Magnitude is the company-currency total. The side comes from
                -- move_type: out_invoice, out_receipt and in_refund are Debe;
                -- out_refund, in_invoice and in_receipt are Haber. Odoo signs
                -- amount_total_signed the same way for a normal document, but
                -- a negative price must not flip the column.
                CASE
                    WHEN move.move_type IN %s THEN ABS(move.amount_total_signed)
                    ELSE 0 * move.amount_total_signed
                END AS debit,
                CASE
                    WHEN move.move_type IN %s THEN ABS(move.amount_total_signed)
                    ELSE 0 * move.amount_total_signed
                END AS credit,
                CASE
                    WHEN move.move_type IN %s THEN ABS(move.amount_total_signed)
                    WHEN move.move_type IN %s THEN -ABS(move.amount_total_signed)
                    ELSE 0 * move.amount_total_signed
                END AS amount,
                move.id AS move_id,
                NULL::integer AS payment_id,
                ('account.move,' || move.id::text) AS document_ref
            FROM account_move move
            JOIN res_company company ON company.id = move.company_id
            WHERE move.state = 'posted'
              AND move.move_type IN %s
              AND move.partner_id IS NOT NULL

            UNION ALL

            SELECT
                -payment.id AS id,
                payment.name AS name,
                payment.date AS date,
                CASE
                    WHEN payment.payment_type = 'inbound' THEN 'customer_inbound'
                    ELSE 'supplier_outbound'
                END AS document_type,
                payment.partner_id AS partner_id,
                payment.company_id AS company_id,
                company.currency_id AS currency_id,
                -- Cobros (inbound customer) reduce what the partner owes us,
                -- so they are Haber. Pagos (outbound supplier) reduce what we
                -- owe, so they are Debe. amount_company_currency_signed is
                -- positive for every inbound payment and negative for every
                -- outbound one, which is the opposite side; use the absolute
                -- value and classify by payment_type and partner_type.
                CASE
                    WHEN payment.payment_type = 'outbound'
                     AND payment.partner_type = 'supplier'
                        THEN ABS(payment.amount_company_currency_signed)
                    ELSE 0 * payment.amount_company_currency_signed
                END AS debit,
                CASE
                    WHEN payment.payment_type = 'inbound'
                     AND payment.partner_type = 'customer'
                        THEN ABS(payment.amount_company_currency_signed)
                    ELSE 0 * payment.amount_company_currency_signed
                END AS credit,
                CASE
                    WHEN payment.payment_type = 'outbound'
                     AND payment.partner_type = 'supplier'
                        THEN ABS(payment.amount_company_currency_signed)
                    WHEN payment.payment_type = 'inbound'
                     AND payment.partner_type = 'customer'
                        THEN -ABS(payment.amount_company_currency_signed)
                    ELSE 0 * payment.amount_company_currency_signed
                END AS amount,
                payment.move_id AS move_id,
                payment.id AS payment_id,
                ('account.payment,' || payment.id::text) AS document_ref
            FROM account_payment payment
            JOIN account_move move ON move.id = payment.move_id
            JOIN res_company company ON company.id = payment.company_id
            WHERE move.state = 'posted'
              AND move.move_type = 'entry'
              AND payment.state IN %s
              AND payment.partner_id IS NOT NULL
              AND (
                    (payment.payment_type = 'inbound' AND payment.partner_type = 'customer')
                 OR (payment.payment_type = 'outbound' AND payment.partner_type = 'supplier')
              )
            """,
            DEBIT_MOVE_TYPES,
            CREDIT_MOVE_TYPES,
            DEBIT_MOVE_TYPES,
            CREDIT_MOVE_TYPES,
            MOVE_TYPES,
            POSTED_PAYMENT_STATES,
        )

    def action_open_document(self):
        self.ensure_one()
        document = self.payment_id or self.move_id
        if not document:
            raise UserError(_('Este movimiento no tiene un documento asociado.'))
        return {
            'type': 'ir.actions.act_window',
            'name': document.display_name,
            'res_model': document._name,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'res_id': document.id,
            'target': 'current',
        }
