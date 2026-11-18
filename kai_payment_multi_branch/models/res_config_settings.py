from odoo import fields, models

from .res_company import ACCOUNT_DOMAIN


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    kai_branch_current_account_id = fields.Many2one(
        related="company_id.kai_branch_current_account_id",
        readonly=False,
        domain=ACCOUNT_DOMAIN,
    )
    kai_central_current_account_id = fields.Many2one(
        related="company_id.kai_central_current_account_id",
        readonly=False,
        domain=ACCOUNT_DOMAIN,
    )
