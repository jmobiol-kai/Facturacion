# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class KaiCheckDebitWizard(models.TransientModel):
    _name = 'kai.check.debit.wizard'
    _description = 'Debitar cheque diferido'

    payment_id = fields.Many2one(
        comodel_name='account.payment',
        string='Pago',
        required=True,
        readonly=True,
    )
    company_id = fields.Many2one(
        related='payment_id.company_id',
    )
    currency_id = fields.Many2one(
        related='payment_id.currency_id',
    )
    check_number = fields.Char(
        string='Número de cheque',
        related='payment_id.check_number',
    )
    debit_date = fields.Date(
        string='Fecha de débito',
        required=True,
        default=fields.Date.context_today,
    )
    journal_id = fields.Many2one(
        comodel_name='account.journal',
        string='Diario bancario',
        required=True,
        check_company=True,
        domain="[('type', '=', 'bank'), ('company_id', '=', company_id)]",
    )
    outstanding_account_id = fields.Many2one(
        comodel_name='account.account',
        string='Cuenta diferida',
        compute='_compute_preview',
    )
    bank_account_id = fields.Many2one(
        comodel_name='account.account',
        string='Cuenta del banco',
        related='journal_id.default_account_id',
    )
    amount = fields.Monetary(
        string='Importe a debitar',
        currency_field='currency_id',
        compute='_compute_preview',
    )

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        payment = self.env['account.payment'].browse(
            values.get('payment_id') or self.env.context.get('active_id')
        )
        if payment and payment.exists() and 'account.payment' == payment._name:
            values.setdefault('payment_id', payment.id)
            if payment.journal_id.type == 'bank':
                values.setdefault('journal_id', payment.journal_id.id)
        return values

    @api.depends('payment_id', 'currency_id')
    def _compute_preview(self):
        for wizard in self:
            payment = wizard.payment_id
            if not payment:
                wizard.outstanding_account_id = False
                wizard.amount = 0.0
                continue
            lines = payment._kai_get_deferred_liquidity_lines()
            wizard.outstanding_account_id = lines.account_id[:1]
            _balance, amount_currency = payment._kai_deferred_open_amounts()
            wizard.amount = abs(amount_currency)

    def action_confirm(self):
        self.ensure_one()
        payment = self.payment_id
        if not payment:
            raise UserError(_('Falta el pago a debitar.'))
        payment.action_kai_create_check_debit(self.journal_id, self.debit_date)
        return {'type': 'ir.actions.act_window_close'}
