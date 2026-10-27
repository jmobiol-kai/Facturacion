# -*- coding: utf-8 -*-
from odoo import fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    # Link kept off account.move.payment_id on purpose: in Odoo 17 that field
    # means "this journal entry is the payment" and posting it calls
    # payment.action_post(). Own checks are account.payment records
    # (there is no l10n_latam.check model on 17.0).
    kai_check_debit_payment_id = fields.Many2one(
        comodel_name='account.payment',
        string='Pago del cheque',
        copy=False,
        index='btree_not_null',
        ondelete='restrict',
        check_company=True,
        help='Pago con cheque propio diferido que originó este débito.',
    )

    def _get_valid_journal_types(self):
        """Allow the debit entry to stay on the bank/cash journal.

        A normal ``entry`` without ``payment_id`` is only valid on a
        miscellaneous journal. ``_compute_journal_id`` would otherwise
        replace the bank journal. The stored ``kai_check_debit_payment_id``
        link is what keeps the journal valid. ``is_payment`` is not used:
        that context belongs to the payment's own entry, and posting a
        move that Odoo treats as the payment syncs the already-posted one.
        """
        if len(self) == 1 and self.kai_check_debit_payment_id:
            return ['bank', 'cash']
        return super()._get_valid_journal_types()
