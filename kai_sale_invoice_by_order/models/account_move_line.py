from odoo import fields, models


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    kai_invoice_by_order = fields.Boolean(
        string="Línea agrupada por pedido de venta",
        copy=True,
    )
    kai_invoice_by_order_qty_map = fields.Json(copy=True)
    kai_invoice_by_order_amount_map = fields.Json(copy=True)
