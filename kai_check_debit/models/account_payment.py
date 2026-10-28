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
        'kai_check_debit_move_ids.statement_line_id',
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

    def _kai_move_state_label(self, move):
        labels = {
            'draft': _('borrador'),
            'posted': _('publicado'),
            'cancel': _('cancelado'),
        }
        return labels.get(move.state, move.state)

    def _kai_statement_reversal_source(self, move):
        """Statement debit whose posted reversal is ``move``, if any."""
        self.ensure_one()
        return self.kai_check_debit_move_ids.filtered(
            lambda item: item.statement_line_id and move in item.reversal_move_id
        )[:1]

    def _kai_blocking_debit_move(self):
        """Live debit that still hits the bank, so another one must not be posted.

        A bank transaction stays live until its journal entry is cancelled or
        deleted. A posted reversal does not take it out of dashboard Balance,
        so it does not unlock the check; the reversal itself keeps the lock
        after the transaction is cancelled, because that reversal credits or
        debits the bank from Operaciones varias.

        A misc entry from 17.0.1.0.0 / 17.0.1.0.1 is live until it is cancelled
        or fully reversed. Reversal nets inside Operaciones varias, so the
        check can be debited again with the statement-line flow.
        """
        self.ensure_one()
        moves = self.kai_check_debit_move_ids.sorted('id')
        for move in moves:
            if move.statement_line_id:
                if move.state != 'cancel':
                    return move
                posted_reversal = move.reversal_move_id.filtered(lambda rev: rev.state == 'posted')
                if posted_reversal:
                    return posted_reversal[:1]
                continue
            if move.state == 'cancel':
                continue
            if move.state == 'posted' and move.reversal_move_id.filtered(lambda rev: rev.state == 'posted'):
                continue
            return move
        return self.env['account.move']

    def _kai_raise_if_debit_blocked(self):
        self.ensure_one()
        blocking = self._kai_blocking_debit_move()
        if not blocking:
            return
        state = self._kai_move_state_label(blocking)
        if blocking.statement_line_id:
            raise UserError(_(
                'El cheque ya tiene una transacción bancaria en estado %(state)s (%(move)s). '
                'Restablezca ese asiento a borrador y cancélelo, o elimine la línea de extracto, '
                'antes de debitarlo de nuevo. No lo revierta: el reverso no lo saca de Balance '
                'y dejaría otro movimiento de banco.',
                state=state,
                move=blocking.display_name,
            ))
        if self._kai_statement_reversal_source(blocking):
            raise UserError(_(
                'El débito bancario tiene un reverso publicado (%(move)s) que sigue '
                'moviendo la cuenta del banco en Operaciones varias. Cancélelo, y cancele '
                'también la transacción bancaria si sigue publicada, antes de debitar de nuevo.',
                move=blocking.display_name,
            ))
        raise UserError(_(
            'El cheque ya tiene un débito anterior (asiento varios) en estado %(state)s (%(move)s). '
            'Cancélelo, o deje publicada su reversión, antes de debitarlo otra vez. '
            'Ese asiento acredita el banco por fuera del extracto: mientras siga vigente '
            'no se crea la transacción nueva, para no acreditar el banco dos veces. '
            'Después de anularlo, Debitar cheque registra una línea de extracto y actualiza Balance.',
            state=state,
            move=blocking.display_name,
        ))

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
        self._kai_raise_if_debit_blocked()
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
        self._kai_ensure_outstanding_reconcilable(outstanding)
        open_lines = self._kai_get_deferred_liquidity_lines().filtered(lambda line: not line.reconciled)
        payment_currency = self.currency_id or self.company_currency_id
        if any(line.currency_id != payment_currency for line in open_lines):
            raise UserError(_('Los apuntes abiertos del cheque no están en la moneda del pago.'))
        self._kai_check_accounts_allowed_on_journal(journal, outstanding | bank_account)
        return outstanding, bank_account, balance, amount_currency

    def _kai_ensure_outstanding_reconcilable(self, outstanding):
        if outstanding.reconcile or outstanding.account_type in ('asset_cash', 'liability_credit_card'):
            return
        raise UserError(_(
            'La cuenta diferida %(account)s no permite conciliación. '
            'Actívela en la cuenta para saldar el crédito del cheque contra el banco.',
            account=outstanding.display_name,
        ))

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

    def _kai_check_debit_payment_ref(self):
        self.ensure_one()
        return _(
            'Débito cheque %(check)s (%(payment)s)',
            check=self._kai_check_label(),
            payment=self.display_name,
        )

    def _kai_statement_amount_in_journal_currency(self, journal, debit_date, balance, amount_currency):
        """Amount of the bank transaction, in the journal currency, signed outbound.

        ``balance`` and ``amount_currency`` are the open residual of the check
        (negative while the deferred credit is open). The dashboard Balance
        sums ``account.bank.statement.line.amount``, which is in journal currency.
        """
        self.ensure_one()
        company_currency = self.company_currency_id
        payment_currency = self.currency_id or company_currency
        journal_currency = journal.currency_id or company_currency
        if journal_currency == payment_currency:
            return journal_currency.round(amount_currency)
        if journal_currency == company_currency:
            return journal_currency.round(balance)
        return journal_currency.round(company_currency._convert(
            balance, journal_currency, self.company_id, debit_date,
        ))

    def _kai_prepare_check_debit_line_vals(self, journal, payment_ref, balance, amount_currency, journal_amount, outstanding):
        """Bank liquidity line and outstanding counterpart, with the open residual.

        Company-currency balances are the payment residual, not a conversion at
        the debit date, so reconciliation does not post an exchange difference.
        ``journal_amount`` is what Balance adds (negative for an outbound check).
        """
        self.ensure_one()
        company_currency = self.company_currency_id
        payment_currency = self.currency_id or company_currency
        journal_currency = journal.currency_id or company_currency
        partner_id = self.partner_id.id
        company_balance = company_currency.round(balance)
        return (
            {
                'name': payment_ref,
                'account_id': journal.default_account_id.id,
                'partner_id': partner_id,
                'currency_id': journal_currency.id,
                'balance': company_balance,
                'amount_currency': journal_currency.round(journal_amount),
            },
            {
                'name': payment_ref,
                'account_id': outstanding.id,
                'partner_id': partner_id,
                'currency_id': payment_currency.id,
                'balance': company_currency.round(-balance),
                'amount_currency': payment_currency.round(-amount_currency),
            },
        )

    def _kai_prepare_check_debit_statement_vals(self, journal, debit_date, outstanding, balance, amount_currency):
        """Values for ``account.bank.statement.line.create``.

        ``line_ids`` is honored by the Odoo 17 create: when it is present the
        method does not add the default suspense counterpart. The liquidity
        line is the only hit on the bank account and carries
        ``statement_line_id``, so the dashboard counts it in Balance and not
        in Operaciones varias. ``statement_id`` stays empty so the amount is
        included with the other lines that are not on a statement.

        Odoo 17.0 CE has no ``account.bank.statement.line.reconcile``.
        The outstanding line is reconciled afterwards with
        ``account.move.line.reconcile``.
        """
        self.ensure_one()
        company_currency = self.company_currency_id
        payment_currency = self.currency_id or company_currency
        journal_currency = journal.currency_id or company_currency
        journal_amount = self._kai_statement_amount_in_journal_currency(
            journal, debit_date, balance, amount_currency,
        )
        if journal_currency.compare_amounts(journal_amount, 0.0) >= 0:
            raise UserError(_('El importe de la transacción bancaria debe ser negativo.'))
        if payment_currency != journal_currency and payment_currency.is_zero(amount_currency):
            raise UserError(_(
                'El saldo en %(currency)s del cheque es cero. No se puede armar la transacción bancaria.',
                currency=payment_currency.name,
            ))
        payment_ref = self._kai_check_debit_payment_ref()
        liquidity_vals, counterpart_vals = self._kai_prepare_check_debit_line_vals(
            journal, payment_ref, balance, amount_currency, journal_amount, outstanding,
        )
        vals = {
            'journal_id': journal.id,
            'company_id': self.company_id.id,
            'date': debit_date,
            'payment_ref': payment_ref,
            'ref': payment_ref,
            'partner_id': self.partner_id.id,
            'amount': journal_amount,
            'statement_id': False,
            # Not account.move.payment_id. The audit link is
            # kai_check_debit_payment_id on the statement line's move.
            'payment_id': False,
            'kai_check_debit_payment_id': self.id,
            'line_ids': [
                Command.create(liquidity_vals),
                Command.create(counterpart_vals),
            ],
        }
        if payment_currency != journal_currency:
            vals['foreign_currency_id'] = payment_currency.id
            vals['amount_currency'] = payment_currency.round(amount_currency)
        return vals, journal_amount

    def _kai_same_amount(self, currency, left, right):
        if left is False or right is False:
            return False
        return currency.compare_amounts(left, right) == 0

    def _kai_split_check_debit_lines(self, st_line, outstanding):
        """Return ``(liquidity, counterpart)`` or raise if the entry is not the pair we posted."""
        self.ensure_one()
        liquidity, suspense, other = st_line._seek_for_lines()
        bank_lines = liquidity
        if len(bank_lines) != 1:
            raise UserError(_(
                'La transacción bancaria debe tener un solo apunte en la cuenta del banco (%s).',
                st_line.move_id.display_name,
            ))
        candidates = (suspense | other).filtered(lambda line: line.account_id == outstanding)
        if len(candidates) != 1 or (st_line.move_id.line_ids - bank_lines - candidates):
            raise UserError(_(
                'La transacción bancaria no tiene una sola contrapartida en la cuenta diferida (%s).',
                st_line.move_id.display_name,
            ))
        return bank_lines, candidates

    def _kai_statement_line_matches_residual(self, st_line, outstanding, journal_amount, balance, amount_currency):
        self.ensure_one()
        company_currency = self.company_currency_id
        payment_currency = self.currency_id or company_currency
        journal_currency = st_line.journal_id.currency_id or company_currency
        try:
            liquidity, counterpart = self._kai_split_check_debit_lines(st_line, outstanding)
        except UserError:
            return False
        if liquidity.account_id != st_line.journal_id.default_account_id:
            return False
        if liquidity.currency_id != journal_currency:
            return False
        if not self._kai_same_amount(company_currency, liquidity.balance, balance):
            return False
        if not self._kai_same_amount(company_currency, counterpart.balance, company_currency.round(-balance)):
            return False
        if not self._kai_same_amount(
            payment_currency, counterpart.amount_currency, payment_currency.round(-amount_currency),
        ):
            return False
        if counterpart.currency_id != payment_currency:
            return False
        if not self._kai_same_amount(journal_currency, liquidity.amount_currency, journal_amount):
            return False
        if not self._kai_same_amount(journal_currency, st_line.amount, journal_amount):
            return False
        if st_line.statement_id or st_line.move_id.payment_id or not st_line.move_id.statement_line_id:
            return False
        if payment_currency != journal_currency:
            if st_line.foreign_currency_id != payment_currency:
                return False
            if not self._kai_same_amount(payment_currency, st_line.amount_currency, amount_currency):
                return False
        elif st_line.foreign_currency_id:
            return False
        return True

    def _kai_rewrite_check_debit_statement_lines(self, st_line, journal, outstanding, journal_amount, balance, amount_currency):
        """Put the posted transaction back on the residual amounts.

        Used only if create did not keep the lines we sent. Writing the move
        with ``skip_account_move_synchronization`` stops
        ``_synchronize_to_moves`` from rebuilding the lines at the debit-date
        rate. ``force_delete`` is what Odoo uses in
        ``action_undo_reconciliation`` to replace lines of a posted statement.
        """
        self.ensure_one()
        payment_ref = self._kai_check_debit_payment_ref()
        liquidity_vals, counterpart_vals = self._kai_prepare_check_debit_line_vals(
            journal, payment_ref, balance, amount_currency, journal_amount, outstanding,
        )
        company_currency = self.company_currency_id
        payment_currency = self.currency_id or company_currency
        journal_currency = journal.currency_id or company_currency
        st_line_vals = {
            'amount': journal_amount,
            'payment_ref': payment_ref,
            'statement_id': False,
            'payment_id': False,
        }
        if payment_currency != journal_currency:
            st_line_vals['foreign_currency_id'] = payment_currency.id
            st_line_vals['amount_currency'] = payment_currency.round(amount_currency)
        else:
            st_line_vals['foreign_currency_id'] = False
            st_line_vals['amount_currency'] = 0.0
        st_line.with_context(skip_account_move_synchronization=True).write(st_line_vals)
        st_line.move_id.with_context(
            force_delete=True,
            skip_account_move_synchronization=True,
        ).write({
            'payment_id': False,
            'ref': payment_ref,
            'line_ids': [
                Command.clear(),
                Command.create(liquidity_vals),
                Command.create(counterpart_vals),
            ],
        })

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
        active on confirm. The statement line inherits ``account.move``, which
        has its own ``payment_id``, so create copies the default onto the new
        entry. ``action_post`` then calls ``payment.action_post()`` and posts
        the payment move, which is already posted (``must be in draft``).

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

    def _kai_reconcile_check_debit_statement_line(self, st_line, outstanding):
        """Clear the deferred credit against the statement line counterpart.

        ``account.move.line.reconcile`` is the public CE API. There is no
        ``account.bank.statement.line.reconcile`` on Odoo 17.0.
        """
        self.ensure_one()
        _liquidity, counterpart = self._kai_split_check_debit_lines(st_line, outstanding)
        open_lines = self._kai_get_deferred_liquidity_lines().filtered(
            lambda line: line.account_id == outstanding and not line.reconciled
        )
        if not open_lines:
            raise UserError(_('El cheque ya no tiene apuntes diferidos abiertos para conciliar.'))
        (open_lines | counterpart).reconcile()
        partials = counterpart.matched_debit_ids | counterpart.matched_credit_ids
        if any(partial.exchange_move_id for partial in partials):
            raise UserError(_(
                'La conciliación del cheque generó una diferencia de cambio. '
                'No se dejó registrado el débito.'
            ))
        open_balance, _amount_currency = self._kai_deferred_open_amounts()
        if self.company_currency_id.compare_amounts(open_balance, 0.0) < 0:
            raise UserError(_(
                'El crédito diferido sigue abierto después de conciliar. No se dejó registrado el débito.'
            ))

    def action_kai_create_check_debit(self, journal, debit_date):
        """Register the check debit as a bank statement line and reconcile it.

        The statement line amount is negative (money out). Its journal entry
        credits the bank account once and debits the deferred outstanding
        account for the open residual. Reconciliation clears that credit.
        No second miscellaneous entry is posted.
        """
        self._kai_lock_for_debit()
        # Covers the statement move. default_payment_id must not land on it.
        self = self.with_context(self._kai_check_debit_move_context())
        moves = self.env['account.move']
        for pay in self:
            outstanding, _bank_account, balance, amount_currency = pay._kai_ensure_can_debit_check(
                journal, debit_date,
            )
            vals, journal_amount = pay._kai_prepare_check_debit_statement_vals(
                journal, debit_date, outstanding, balance, amount_currency,
            )
            vals['payment_id'] = False
            st_line = self.env['account.bank.statement.line'].create(vals)
            if st_line.move_id.payment_id:
                raise UserError(_(
                    'La transacción bancaria quedó vinculada como asiento del pago. '
                    'No se debitó el cheque.'
                ))
            if not pay._kai_statement_line_matches_residual(
                st_line, outstanding, journal_amount, balance, amount_currency,
            ):
                pay._kai_rewrite_check_debit_statement_lines(
                    st_line, journal, outstanding, journal_amount, balance, amount_currency,
                )
                st_line.invalidate_recordset()
                st_line.move_id.invalidate_recordset()
            if not pay._kai_statement_line_matches_residual(
                st_line, outstanding, journal_amount, balance, amount_currency,
            ):
                raise UserError(_(
                    'No se pudo dejar la transacción bancaria por el saldo abierto del cheque.'
                ))
            if st_line.move_id.state != 'posted':
                st_line.move_id.action_post()
            pay._kai_reconcile_check_debit_statement_line(st_line, outstanding)
            pay.message_post(body=_(
                'Cheque debitado con la transacción bancaria %s. '
                'El importe entra en Balance del diario. '
                'Para anularlo, restablezca ese asiento a borrador y cancélelo, '
                'o elimine la línea de extracto. No lo revierta.',
                st_line.move_id._get_html_link(),
            ))
            st_line.move_id.message_post(body=_(
                'Débito del cheque %s. La contrapartida se concilió con el saldo diferido del pago.',
                pay._get_html_link(),
            ))
            moves |= st_line.move_id
        return moves

    def action_kai_debit_check(self):
        self.ensure_one()
        self._kai_check_debit_access()
        if not self._kai_is_deferred_own_check():
            raise UserError(_(
                'Solo se puede debitar un pago publicado de cheque propio diferido '
                'cuya cuenta pendiente no sea la cuenta del banco.'
            ))
        lines = self._kai_get_deferred_liquidity_lines()
        self._kai_ensure_outstanding_reconcilable(lines.account_id)
        self._kai_raise_if_debit_blocked()
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
        for pay in self:
            blocking = pay._kai_blocking_debit_move()
            if not blocking:
                continue
            if blocking.statement_line_id:
                hint = _(
                    'Restablezca la transacción bancaria a borrador y cancélela, o elimine la línea de extracto.'
                )
            elif pay._kai_statement_reversal_source(blocking):
                hint = _('Cancele el reverso publicado de la transacción bancaria.')
            else:
                hint = _('Cancele primero el asiento de débito.')
            raise UserError(_(
                'No se puede modificar el pago porque el cheque ya fue debitado (%(move)s). %(hint)s',
                move=blocking.display_name,
                hint=hint,
            ))

    def action_draft(self):
        self._kai_ensure_no_live_check_debit()
        return super().action_draft()

    def action_cancel(self):
        self._kai_ensure_no_live_check_debit()
        return super().action_cancel()
