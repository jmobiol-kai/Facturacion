from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    invoice_by_sale_order = fields.Boolean(
        related="company_id.invoice_by_sale_order",
        readonly=False,
    )
