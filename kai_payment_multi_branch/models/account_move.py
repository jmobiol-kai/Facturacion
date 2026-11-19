from odoo import fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    kai_origin_payment_id = fields.Many2one(
        "account.payment",
        string="Pago origen",
        index=True,
        copy=False,
        ondelete="set null",
        check_company=False,
        help="Pago de otra sucursal de la misma entidad legal que originó este asiento puente.",
    )
