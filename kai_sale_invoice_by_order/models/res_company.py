from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    invoice_by_sale_order = fields.Boolean(
        string="Una línea de factura por pedido de venta",
        default=True,
        help="Al crear una factura desde pedidos de venta, se agrupa en una "
             "sola línea por pedido (o por pedido e impuesto, si un pedido "
             "combina alícuotas distintas).",
    )
