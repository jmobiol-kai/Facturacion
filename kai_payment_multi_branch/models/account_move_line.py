from odoo import fields, models


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    kai_settled_company_id = fields.Many2one(
        "res.company",
        string="Sucursal cancelada",
        copy=False,
        index=True,
        check_company=False,
        help="Sucursal cuya deuda cubre esta línea del pago.",
    )
    kai_debt_move_line_id = fields.Many2one(
        "account.move.line",
        string="Línea de deuda",
        copy=False,
        index=True,
        check_company=False,
        help="Línea a pagar o cobrar que esta línea del asiento puente cancela.",
    )
