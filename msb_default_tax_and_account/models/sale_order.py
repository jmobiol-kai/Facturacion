# -*- coding: utf-8 -*-

from odoo import models, fields, api


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    tax_recompute_needed = fields.Boolean(
        string='Recálculo de Impuestos Necesario',
        default=False,
        help='Se activa cuando cambia el diario o lista de precios; muestra solicitud para recalcular impuestos (sobrescribir todo).'
    )

    @api.depends('partner_id', 'company_id')
    def _compute_journal_id(self):
        """
        Compute the journal for the sale order.
        This suggests an appropriate journal but users can change it.
        Ensures the correct journal is used when creating invoices.
        """
        # AFIP carve-out: base sale._compute_journal_id() unconditionally resets
        # journal_id = False (odoo/addons/sale/models/sale_order.py:376-377), which
        # would let the steering below grab the first tax-mapping journal and strip an
        # AFIP-bound journal (and its document-type binding). Snapshot AFIP-bound
        # journals BEFORE super() wipes them so we can restore + skip steering.
        # getattr() keeps this CE-safe: on a stack without l10n_ar the field is absent
        # and the snapshot stays empty, so steering behaves exactly as before.
        afip_journal_before = {}
        for order in self:
            journal = order.journal_id
            if journal and getattr(journal, 'l10n_ar_afip_pos_number', False):
                afip_journal_before[order] = journal

        super()._compute_journal_id()
        for order in self:
            # Restore an AFIP-bound journal that super() reset, and never steer away
            # from it: the AFIP POS number drives the AR document type, so the user's
            # explicit AFIP journal must win over any tax-mapping "steer magnet".
            afip_journal = afip_journal_before.get(order)
            if afip_journal:
                order.journal_id = afip_journal
                continue

            # Prefer a sales journal that has active tax mappings configured
            mapping_journal_ids = self.env['account.journal.tax.mapping'].search([
                ('tax_type', '=', 'sale'),
                ('active', '=', True),
                ('journal_id.company_id', '=', order.company_id.id),
            ]).mapped('journal_id')

            preferred = mapping_journal_ids and mapping_journal_ids[0] or False

            # Override if none set by super, or if the current journal lacks tax mappings
            if preferred and (
                not order.journal_id or not order.journal_id.has_tax_mappings('sale')
            ):
                order.journal_id = preferred

    def _prepare_invoice(self):
        """
        Override to ensure the journal from the sale order is properly transferred
        to the invoice during creation.
        """
        values = super()._prepare_invoice()

        # Ensure a journal is transferred to the invoice WITHOUT clobbering one an
        # inner/earlier writer (e.g. oo_mercado_libre, AFIP) already chose. Base
        # sale.order._prepare_invoice (sale/models/sale_order.py:1448-1449) already
        # sets values['journal_id'] = self.journal_id.id, so in the normal path this
        # setdefault is a no-op (key already present with the same value); it only
        # fills the journal when no previous writer provided one, letting an outer
        # writer's MELI/AFIP journal win.
        if self.journal_id:
            values.setdefault('journal_id', self.journal_id.id)

        return values

    def _recompute_line_taxes(self):
        """Core logic to recompute all sale order line taxes using journal mapping + fiscal position.
        Can be called from onchange (in-memory records) or from action methods (saved records).
        """
        for order in self:
            journal = order.journal_id
            fpos = order.fiscal_position_id
            company = order.company_id

            for line in order.order_line.filtered(lambda l: not l.display_type):
                taxes = line.product_id and line.product_id.taxes_id._filter_taxes_by_company(company) or self.env['account.tax']
                if journal and journal.has_tax_mappings('sale') and taxes:
                    taxes = journal.map_taxes(taxes, 'sale')
                mapped = fpos.map_tax(taxes) if (fpos and taxes) else taxes
                line.tax_ids = mapped

    @api.onchange('journal_id', 'pricelist_id')
    def _onchange_journal_or_pricelist(self):
        """Automatically recompute taxes when journal or pricelist changes."""
        for order in self:
            if order.order_line:
                order._recompute_line_taxes()
            order.tax_recompute_needed = False

    def action_recompute_taxes_force(self):
        """Overwrite all line taxes using journal mapping + fiscal position.
        Applies to product lines only. Clears the recompute flag afterwards.
        Kept for backward compatibility.
        """
        self._recompute_line_taxes()
        self.write({'tax_recompute_needed': False})
        return True
