from odoo import Command, api, fields, models


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
    kai_settled_by_company_ids = fields.Many2many(
        "res.company",
        "kai_move_line_settled_company_rel",
        "line_id",
        "company_id",
        string="Visible desde la compañía del pago",
        copy=False,
        help="Compañías que cancelaron esta línea con un asiento puente. "
        "Permite verla en la pestaña Pagado aunque el selector no incluya la sucursal.",
    )

    def init(self):
        # account.account_move_line_comp_rule is noupdate, so XML cannot widen it.
        # Branch invoice lines must stay readable from the paying company.
        domain = (
            "['|', ('company_id', 'in', company_ids), "
            "('kai_settled_by_company_ids', 'in', company_ids)]"
        )
        self.env.cr.execute(
            """
            UPDATE ir_rule
               SET domain_force = %s
             WHERE id = (
                SELECT res_id FROM ir_model_data
                 WHERE module = 'account' AND name = 'account_move_line_comp_rule'
             )
               AND domain_force IS DISTINCT FROM %s
            """,
            [domain, domain],
        )
        if self.env.cr.rowcount:
            self.env.registry.clear_cache()

    def _kai_refresh_settled_companies(self):
        """Qué compañías pagaron esta línea a través de un asiento puente."""
        for line in self.sudo().exists():
            partials = line.matched_debit_ids | line.matched_credit_ids
            others = (partials.debit_move_id | partials.credit_move_id) - line
            companies = others.move_id.filtered("kai_origin_payment_id").kai_origin_payment_id.company_id
            line.kai_settled_by_company_ids = [Command.set(companies.ids)]

    def _kai_matched_amount_from_partials(self, debit_partials, credit_partials, payment):
        """Mismo signo que account_payment_pro: débito menos crédito, en la moneda del pago."""
        target_currency = payment.destination_currency_id or payment.company_currency_id
        company_currency = payment.company_currency_id
        accounting_rate = payment.accounting_rate or 1.0
        counterpart_rate = payment.counterpart_rate or 1.0
        if target_currency == company_currency:
            return sum(debit_partials.mapped("amount")) - sum(credit_partials.mapped("amount"))
        debit_amount_rec = sum(debit_partials.mapped("debit_amount_currency"))
        credit_amount_rec = sum(credit_partials.mapped("credit_amount_currency"))
        amount_in_rec_currency = debit_amount_rec - credit_amount_rec
        rec_currency = self.currency_id or company_currency
        if rec_currency == target_currency:
            return amount_in_rec_currency
        if rec_currency == company_currency:
            amount_in_a = amount_in_rec_currency * accounting_rate
            if target_currency == payment.currency_id:
                return amount_in_a
            return amount_in_a * counterpart_rate
        if target_currency == payment.currency_id:
            return amount_in_rec_currency
        return amount_in_rec_currency * counterpart_rate

    @api.depends_context("matched_payment_ids")
    def _compute_payment_matched_values(self):
        super()._compute_payment_matched_values()
        matched_payment_ids = self.env.context.get("matched_payment_ids")
        if not matched_payment_ids:
            return
        payments = self.env["account.payment"].browse(matched_payment_ids).exists()
        if not payments:
            return
        main_payment = payments[:1]
        valid_types = ("asset_receivable", "liability_payable")
        bridge_lines = payments.sudo().kai_bridge_move_ids.line_ids.filtered(
            lambda line: (
                line.account_type in valid_types
                and line.move_id.company_id != line.move_id.kai_origin_payment_id.company_id
            )
        )
        if not bridge_lines:
            return
        for line in self:
            debit_partials = bridge_lines.matched_debit_ids.filtered(lambda partial: partial.debit_move_id == line)
            credit_partials = bridge_lines.matched_credit_ids.filtered(lambda partial: partial.credit_move_id == line)
            if not debit_partials and not credit_partials:
                continue
            line.payment_matched_currency_id = main_payment.destination_currency_id or main_payment.company_currency_id
            line.payment_matched_amount += line._kai_matched_amount_from_partials(
                debit_partials, credit_partials, main_payment
            )
