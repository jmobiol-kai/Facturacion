from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    bill_by_purchase_order = fields.Boolean(
        string="Una línea de factura por orden de compra",
        default=True,
        help="Al crear una factura de proveedor desde órdenes de compra, se agrupa "
             "en una sola línea por orden (o por orden e impuesto, si una orden "
             "combina alícuotas distintas).",
    )
