from odoo import fields, models

ACCOUNT_DOMAIN = [
    ("active", "=", True),
    (
        "account_type",
        "not in",
        (
            "asset_receivable",
            "liability_payable",
            "asset_cash",
            "liability_credit_card",
            "off_balance",
        ),
    ),
]


class ResCompany(models.Model):
    _inherit = "res.company"

    kai_branch_current_account_id = fields.Many2one(
        "account.account",
        string="Cuenta corriente con sucursales",
        check_company=True,
        domain=ACCOUNT_DOMAIN,
        help="Cuenta que usa esta compañía en el pago o recibo cuando cancela "
        "deuda de otras sucursales de la misma entidad legal. En la casa "
        "central es la cuenta corriente con sucursales.",
    )
    kai_central_current_account_id = fields.Many2one(
        "account.account",
        string="Cuenta corriente con casa central",
        check_company=True,
        domain=ACCOUNT_DOMAIN,
        help="Cuenta que usa esta compañía en el asiento puente cuando otra "
        "sucursal de la misma entidad legal le cancela deuda. En cada "
        "sucursal es la cuenta corriente con la casa central.",
    )
