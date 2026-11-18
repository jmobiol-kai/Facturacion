from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError


class KaiPaymentRegularizeWizard(models.TransientModel):
    _name = "kai.payment.regularize.wizard"
    _description = "Regularizar pago existente"

    date = fields.Date(
        string="Fecha de los asientos",
        help="Si la completás, todos los asientos de regularización usan esta fecha. "
        "Si la dejás vacía, cada pago usa su fecha original. Sirve cuando el período "
        "del pago ya está cerrado.",
    )
    line_ids = fields.One2many(
        "kai.payment.regularize.wizard.line",
        "wizard_id",
        string="Pagos",
    )
    eligible_count = fields.Integer(compute="_compute_eligible_count")

    @api.depends("line_ids.eligible")
    def _compute_eligible_count(self):
        for wizard in self:
            wizard.eligible_count = len(wizard.line_ids.filtered("eligible"))

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        payments = self.env["account.payment"].browse(self.env.context.get("active_ids"))
        if len(payments) == 1 and "date" in fields_list:
            res["date"] = payments.date
        if "line_ids" in fields_list:
            commands = []
            for payment in payments:
                reason = payment._kai_regularize_block_reason()
                commands.append(
                    Command.create(
                        {
                            "payment_id": payment.id,
                            "eligible": not reason,
                            "reason": reason or _("Se va a regularizar."),
                        }
                    )
                )
            res["line_ids"] = commands
        return res

    def action_regularize(self):
        self.ensure_one()
        eligible = self.line_ids.filtered("eligible").payment_id
        if not eligible:
            details = "\n".join(
                _("%(payment)s: %(reason)s", payment=line.payment_id.display_name, reason=line.reason)
                for line in self.line_ids
            )
            raise UserError(_("Ningún pago se puede regularizar.\n%s", details))
        for payment in eligible:
            payment._kai_regularize_existing(self.date or payment.date)
        return {"type": "ir.actions.act_window_close"}


class KaiPaymentRegularizeWizardLine(models.TransientModel):
    _name = "kai.payment.regularize.wizard.line"
    _description = "Pago a regularizar"

    wizard_id = fields.Many2one("kai.payment.regularize.wizard", required=True, ondelete="cascade")
    payment_id = fields.Many2one("account.payment", string="Pago", required=True)
    eligible = fields.Boolean(string="Se regulariza")
    reason = fields.Char(string="Detalle")
    partner_id = fields.Many2one(related="payment_id.partner_id")
    amount = fields.Monetary(related="payment_id.amount", currency_field="currency_id")
    currency_id = fields.Many2one(related="payment_id.currency_id")
    payment_date = fields.Date(related="payment_id.date", string="Fecha del pago")
    company_id = fields.Many2one(related="payment_id.company_id")
    payment_type = fields.Selection(related="payment_id.payment_type")
