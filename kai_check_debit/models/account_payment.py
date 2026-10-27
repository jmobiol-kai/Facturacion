# -*- coding: utf-8 -*-
from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    kai_check_debit_move_ids = fields.One2many(
        comodel_name='account.move',
        inverse_name='kai_check_debit_payment_id',
        string='Débitos de cheque',
        copy=False,
        readonly=True,
    )
    kai_check_debit_move_count = fields.Integer(
        string='Cantidad de débitos',
        compute='_compute_kai_check_debit_move_count',
    )
    kai_show_check_debit_button = fields.Boolean(
        string='Mostrar Debitar cheque',
        compute='_compute_kai_show_check_debit_button',
    )

    @api.depends('kai_check_debit_move_ids')
    def _compute_kai_check_debit_move_count(self):
        for pay in self:
            pay.kai_check_debit_move_count = len(pay.kai_check_debit_move_ids)

    @api.depends(
        'state',
        'payment_type',
        'is_internal_transfer',
        'payment_method_line_id.code',
        'journal_id.l10n_latam_manual_checks',
        'journal_id.default_account_id',
        'company_currency_id',
        'currency_id',
        'move_id.line_ids.account_id',
        'move_id.line_ids.balance',
        'move_id.line_ids.amount_currency',
        'move_id.line_ids.amount_residual',
        'move_id.line_ids.amount_residual_currency',
        'move_id.line_ids.reconciled',
        'kai_check_debit_move_ids.state',
        'kai_check_debit_move_ids.reversal_move_id.state',
    )
    def _compute_kai_show_check_debit_button(self):
        for pay in self:
            pay.kai_show_check_debit_button = bool(
                pay._kai_is_deferred_own_check()
                and not pay._kai_blocking_debit_move()
                and pay._kai_has_open_deferred_balance()
            )

    def _kai_is_deferred_own_check(self):
        """Own deferred/electronic check whose issue entry did not hit the bank.

        Verified on Odoo 17.0 ``l10n_latam_check``: there is no
        ``l10n_latam.check`` model. A deferred or electronic own check is an
        ``account.payment`` with method code ``check_printing`` and
        ``journal_id.l10n_latam_manual_checks``. Posting credits
        ``outstanding_account_id`` (payment method ``payment_account_id``, or
        the company outbound outstanding account). That is the deferred
        liability when it is not the journal ``default_account_id``.
        """
        self.ensure_one()
        if self.state != 'posted' or self.payment_type != 'outbound' or self.is_internal_transfer:
            return False
        if self.payment_method_code != 'check_printing' or not self.l10n_latam_manual_checks:
            return False
        lines = self._kai_get_deferred_liquidity_lines()
        if not lines or len(lines.account_id) != 1:
            return False
        bank_account = self.journal_id.default_account_id
        if not bank_account or lines.account_id == bank_account:
            return False
        return True

    def _kai_get_deferred_liquidity_lines(self):
        """Liquidity lines of the payment entry (the outstanding credit)."""
        self.ensure_one()
        liquidity_lines, _counterpart_lines, _writeoff_lines = self._seek_for_lines()
        return liquidity_lines

    def _kai_deferred_open_amounts(self):
        """Return ``(balance, amount_currency)`` still open on the liquidity lines.

        ``balance`` is in company currency and is negative while the issued
        credit is still open. Reconcilable outstanding accounts (Odoo turns
        ``payment_account_id`` on when the method line is saved) expose the
        open amount on the residual. Other accounts keep the original balance.
        """
        self.ensure_one()
        lines = self._kai_get_deferred_liquidity_lines()
        account = lines.account_id[:1]
        company_currency = self.company_currency_id
        currency = self.currency_id or company_currency
        if not lines or not account or not company_currency:
            return 0.0, 0.0
        tracks_residual = bool(
            account.reconcile or account.account_type in ('asset_cash', 'liability_credit_card')
        )
        if tracks_residual:
            balance = sum(lines.mapped('amount_residual'))
            amount_currency = sum(lines.mapped('amount_residual_currency'))
        else:
            balance = sum(lines.mapped('balance'))
            amount_currency = sum(lines.mapped('amount_currency'))
        balance = company_currency.round(balance)
        amount_currency = currency.round(amount_currency)
        if currency == company_currency:
            amount_currency = balance
        return balance, amount_currency

    def _kai_has_open_deferred_balance(self):
        self.ensure_one()
        balance, _amount_currency = self._kai_deferred_open_amounts()
        company_currency = self.company_currency_id
        if not company_currency:
            return False
        return company_currency.compare_amounts(balance, 0.0) < 0

    def _kai_blocking_debit_move(self):
        """Posted or draft debit that is not cancelled or fully reversed."""
        self.ensure_one()
        moves = self.kai_check_debit_move_ids.sorted('id')
        for move in moves:
            if move.state == 'cancel':
                continue
            if move.state == 'posted' and move.reversal_move_id.filtered(lambda rev: rev.state == 'posted'):
                continue
            return move
        return self.env['account.move']

    def _kai_check_debit_access(self):
        if not self.env.su and not self.env.user.has_group('account.group_account_user'):
            raise UserError(_('Se requiere permiso de contabilidad para debitar el cheque.'))

    def _kai_check_label(self):
        self.ensure_one()
        return self.check_number or self.l10n_latam_check_number or self.display_name

    def _kai_ensure_can_debit_check(self, journal, debit_date):
        self.ensure_one()
        self._kai_check_debit_access()
        if not self._kai_is_deferred_own_check():
            raise UserError(_(
                'Solo se puede debitar un pago publicado de cheque propio diferido '
                '(cheques electrónicos/diferidos) cuya cuenta pendiente no sea la cuenta del banco.'
            ))
        blocking = self._kai_blocking_debit_move()
        if blocking:
            raise UserError(_(
                'El cheque ya tiene un débito en estado %(state)s (%(move)s). '
                'Cancele ese asiento antes de debitarlo de nuevo.',
                state=blocking.state,
                move=blocking.display_name,
            ))
        balance, amount_currency = self._kai_deferred_open_amounts()
        company_currency = self.company_currency_id
        currency = self.currency_id or company_currency
        if company_currency.compare_amounts(balance, 0.0) >= 0:
            raise UserError(_('El cheque ya no tiene saldo acreedor pendiente en la cuenta diferida.'))
        if currency.compare_amounts(amount_currency, 0.0) > 0:
            raise UserError(_(
                'El saldo abierto del cheque no es un crédito. Revise el apunte de liquidez del pago.'
            ))
        if not debit_date:
            raise UserError(_('Indique la fecha de débito.'))
        if self.date and debit_date < self.date:
            raise UserError(_(
                'La fecha de débito no puede ser anterior a la fecha del pago (%s).',
                self.date,
            ))
        if not journal or journal.type != 'bank':
            raise UserError(_('Seleccione el diario bancario cuya cuenta se acredita.'))
        if journal.company_id != self.company_id:
            raise UserError(_('El diario bancario debe ser de la misma compañía que el pago.'))
        bank_account = journal.default_account_id
        if not bank_account:
            raise UserError(_(
                'El diario %(journal)s no tiene cuenta por defecto.',
                journal=journal.display_name,
            ))
        outstanding = self._kai_get_deferred_liquidity_lines().account_id
        if outstanding == bank_account:
            raise UserError(_('La cuenta pendiente del pago ya es la cuenta del banco.'))
        self._kai_check_accounts_allowed_on_journal(journal, outstanding | bank_account)
        return outstanding, bank_account, balance, amount_currency

    def _kai_check_accounts_allowed_on_journal(self, journal, accounts):
        for account in accounts:
            if account.deprecated:
                raise UserError(_('La cuenta %s está deprecada.', account.display_name))
            if account.allowed_journal_ids and journal not in account.allowed_journal_ids:
                raise UserError(_(
                    'La cuenta %(account)s no está habilitada en el diario %(journal)s '
                    '(diarios permitidos de la cuenta).',
                    account=account.display_name,
                    journal=journal.display_name,
                ))
            if account in (journal.default_account_id, journal.suspense_account_id):
                continue
            if journal.account_control_ids and account not in journal.account_control_ids:
                raise UserError(_(
                    'El diario %(journal)s no permite usar la cuenta %(account)s. '
                    'Agrégela en Cuentas permitidas del diario (Ajustes avanzados).',
                    journal=journal.display_name,
                    account=account.display_name,
                ))

    def _kai_prepare_check_debit_move_vals(self, journal, debit_date, outstanding, bank_account, balance, amount_currency):
        self.ensure_one()
        label = _('Débito cheque %s', self._kai_check_label())
        ref = _('Débito cheque %(check)s (%(payment)s)', check=self._kai_check_label(), payment=self.display_name)
        partner_id = self.partner_id.id
        currency_id = (self.currency_id or self.company_currency_id).id
        return {
            'move_type': 'entry',
            'journal_id': journal.id,
            'company_id': self.company_id.id,
            'date': debit_date,
            'ref': ref,
            'partner_id': partner_id,
            # Not account.move.payment_id: that field means this entry is the
            # payment. The audit link is kai_check_debit_payment_id only.
            'payment_id': False,
            'kai_check_debit_payment_id': self.id,
            'line_ids': [
                Command.create({
                    'name': label,
                    'account_id': outstanding.id,
                    'partner_id': partner_id,
                    'currency_id': currency_id,
                    'balance': -balance,
                    'amount_currency': -amount_currency,
                }),
                Command.create({
                    'name': label,
                    'account_id': bank_account.id,
                    'partner_id': partner_id,
                    'currency_id': currency_id,
                    'balance': balance,
                    'amount_currency': amount_currency,
                }),
            ],
        }

    def _kai_lock_for_debit(self):
        """Serialize debits of the same payment so two clicks cannot both post."""
        if not self.ids:
            return
        self.env.flush_all()
        self.env.cr.execute(
            'SELECT id FROM account_payment WHERE id IN %s FOR UPDATE',
            [tuple(self.ids)],
        )
        self.env.invalidate_all()

    def _kai_check_debit_move_context(self):
        """Context for every ``account.move`` created while debiting.

        The wizard is opened with ``default_payment_id``. That key is still
        active on confirm. ``account.move`` has its own ``payment_id``, so
        create copies the default onto the new entry. ``action_post`` then
        calls ``payment.action_post()`` and posts the payment move, which
        is already posted (``must be in draft``).

        The wizard key is removed, then set to ``False``. ``default_get``
        uses a present ``default_*`` context key as the field value, so the
        debit move's ``payment_id`` stays empty instead of becoming the
        payment. ``is_payment`` is dropped too: bank journals stay valid
        through ``kai_check_debit_payment_id``.
        """
        ctx = dict(self.env.context)
        ctx.pop('default_payment_id', None)
        ctx['default_payment_id'] = False
        ctx.pop('is_payment', None)
        return ctx

    def action_kai_create_check_debit(self, journal, debit_date):
        """Post the transfer from the deferred liability to the bank account.

        Debit the outstanding account credited at issue (open residual when the
        account is reconcilable). Credit ``journal.default_account_id``. Amounts
        are the open payment amounts, not a revaluation at ``debit_date``, so
        the liability clears without an exchange-difference line.
        """
        self._kai_lock_for_debit()
        # Covers the debit entry and any exchange move reconcile() creates.
        self = self.with_context(self._kai_check_debit_move_context())
        moves = self.env['account.move']
        for pay in self:
            outstanding, bank_account, balance, amount_currency = pay._kai_ensure_can_debit_check(
                journal, debit_date,
            )
            vals = pay._kai_prepare_check_debit_move_vals(
                journal, debit_date, outstanding, bank_account, balance, amount_currency,
            )
            # Last word before create: never the payment's own journal entry.
            vals['payment_id'] = False
            move = self.env['account.move'].create(vals)
            move.action_post()
            if outstanding.reconcile:
                liquidity_lines = pay._kai_get_deferred_liquidity_lines().filtered(
                    lambda line: line.account_id == outstanding and not line.reconciled
                )
                debit_line = move.line_ids.filtered(lambda line: line.account_id == outstanding)
                (liquidity_lines + debit_line).reconcile()
            pay.message_post(body=_(
                'Cheque debitado con el asiento %s.',
                move._get_html_link(),
            ))
            move.message_post(body=_(
                'Asiento de débito del cheque %s.',
                pay._get_html_link(),
            ))
            moves |= move
        return moves

    def action_kai_debit_check(self):
        self.ensure_one()
        self._kai_check_debit_access()
        if not self._kai_is_deferred_own_check():
            raise UserError(_(
                'Solo se puede debitar un pago publicado de cheque propio diferido '
                'cuya cuenta pendiente no sea la cuenta del banco.'
            ))
        blocking = self._kai_blocking_debit_move()
        if blocking:
            raise UserError(_(
                'El cheque ya tiene un débito en estado %(state)s (%(move)s).',
                state=blocking.state,
                move=blocking.display_name,
            ))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Debitar cheque'),
            'res_model': 'kai.check.debit.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                # Defaults the wizard's payment_id. Cleared again before the
                # debit account.move is created (same field name on the move).
                'default_payment_id': self.id,
                'default_journal_id': self.journal_id.id,
                'default_debit_date': fields.Date.context_today(self),
            },
        }

    def action_open_kai_check_debit_moves(self):
        self.ensure_one()
        moves = self.kai_check_debit_move_ids
        action = {
            'name': _('Débito de cheque'),
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'context': {'create': False},
        }
        if len(moves) == 1:
            action.update({
                'view_mode': 'form',
                'res_id': moves.id,
            })
        else:
            action.update({
                'view_mode': 'tree,form',
                'domain': [('id', 'in', moves.ids)],
            })
        return action

    def _kai_ensure_no_live_check_debit(self):
        blocked = self.filtered(lambda pay: pay._kai_blocking_debit_move())
        if not blocked:
            return
        names = ', '.join(
            move.display_name
            for move in blocked.mapped(lambda pay: pay._kai_blocking_debit_move())
        )
        raise UserError(_(
            'No se puede modificar el pago porque el cheque ya fue debitado (%s). '
            'Cancele primero el asiento de débito.',
            names,
        ))

    def action_draft(self):
        self._kai_ensure_no_live_check_debit()
        return super().action_draft()

    def action_cancel(self):
        self._kai_ensure_no_live_check_debit()
        return super().action_cancel()
