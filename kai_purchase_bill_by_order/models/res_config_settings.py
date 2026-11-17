from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bill_by_purchase_order = fields.Boolean(
        related="company_id.bill_by_purchase_order",
        readonly=False,
    )
