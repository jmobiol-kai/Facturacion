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
    kai_bridge_journal_id = fields.Many2one(
        "account.journal",
        string="Diario de asientos puente",
        check_company=True,
        domain="[('type', '=', 'general')]",
        help="Diario de los asientos puente y de la reclasificación a la cuenta "
        "corriente. Si queda vacío, se usa el diario de operaciones misceláneas "
        "(MISC o MISCE), no el de punto de venta ni otros diarios generales.",
    )
