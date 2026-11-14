# -*- coding: utf-8 -*-

from odoo import models, fields, api


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    journal_id = fields.Many2one(
        'account.journal',
        string='Diario de Compras',
        domain="[('type', '=', 'purchase'), ('company_id', '=', company_id)]",
        help="Diario que se utilizará para crear facturas de proveedor. Si está configurado con impuestos/cuentas por defecto, se aplicarán a las líneas de compra."
    )

    tax_recompute_needed = fields.Boolean(
        string='Recálculo de Impuestos Necesario',
        default=False,
        help='Se activa cuando cambia el diario; muestra solicitud para recalcular impuestos (sobrescribir todo).'
    )

    @api.depends('partner_id', 'company_id')
    def _compute_journal_id(self):
        """
        Compute a suggested journal for the purchase order.
        Users can change this selection.
        """
        super()._compute_journal_id() if hasattr(super(), '_compute_journal_id') else None
        for order in self:
            if not order.journal_id:
                # Try to find a purchase journal with active tax mappings configured
                mapping_journal_ids = self.env['account.journal.tax.mapping'].search([
                    ('tax_type', '=', 'purchase'),
                    ('active', '=', True),
                    ('journal_id.company_id', '=', order.company_id.id),
                ]).mapped('journal_id')
                if mapping_journal_ids:
                    order.journal_id = mapping_journal_ids[0]

    def action_create_invoice(self, attachment_ids=False):
        """
        Override to ensure the selected journal is passed through to invoice creation.
        This allows purchase_order_line._prepare_account_move_line to access the journal.
        """
        if self.journal_id:
            # Pass journal in context so purchase lines can access it
            return super(PurchaseOrder, self.with_context(
                default_journal_id=self.journal_id.id
            )).action_create_invoice(attachment_ids=attachment_ids)
        return super().action_create_invoice(attachment_ids=attachment_ids)

    def _prepare_invoice(self):
        """
        Override to ensure the journal from the purchase order is properly transferred
        to the invoice during creation.
        """
        values = super()._prepare_invoice()
        
        # Ensure journal is transferred to invoice
        if self.journal_id:
            values['journal_id'] = self.journal_id.id
            
        return values

    def write(self, vals):
        # onchange updates the form, but a saved journal change (the user
        # confirms the PO, then switches Diario de Compras) only reaches
        # write(). Recompute here so the new mappings are stored.
        if self.env.context.get('msb_skip_journal_tax_sync') or 'journal_id' not in vals:
            return super().write(vals)
        previous = {order.id: order.journal_id.id for order in self}
        res = super().write(vals)
        changed = self.filtered(lambda order: previous.get(order.id) != order.journal_id.id)
        if changed:
            changed._msb_apply_purchase_journal_change()
        return res

    def _msb_apply_purchase_journal_change(self):
        """Remap PO lines and draft vendor bills after ``journal_id`` changed.

        Posted and cancelled bills are left alone. The bill journal is updated
        only when the PO journal is a purchase journal of the same company.
        ``msb_skip_journal_tax_sync`` stops the line writes from re-entering
        this hook.
        """
        self.with_context(msb_skip_journal_tax_sync=True)._recompute_line_taxes()
        for order in self:
            journal = order.journal_id
            if not (
                journal
                and journal.type == 'purchase'
                and journal.company_id == order.company_id
            ):
                continue
            drafts = order.invoice_ids.filtered(
                lambda move: move.state == 'draft'
                and move.move_type in ('in_invoice', 'in_refund')
                and move.company_id == order.company_id
            )
            if not drafts:
                continue
            # Changing the bill journal remaps every product line once.
            # Bills already on this journal only refresh lines linked to the PO,
            # which may still carry the previous journal's tax.
            to_retarget = drafts.filtered(lambda move: move.journal_id != journal)
            if to_retarget:
                to_retarget.write({'journal_id': journal.id})
            already = drafts - to_retarget
            if already:
                already._msb_remap_draft_vendor_bill_taxes(
                    purchase_lines_only=True, purchase_order=order,
                )
        flagged = self.filtered('tax_recompute_needed')
        if flagged:
            flagged.with_context(msb_skip_journal_tax_sync=True).write({
                'tax_recompute_needed': False,
            })

    def _recompute_line_taxes(self):
        """Recompute purchase order line taxes from the product and the journal.

        Section, note and down payment lines are left untouched. Safe to call
        from onchange (in-memory records) and from ``write``.
        """
        # Line tax writes must not re-enter the journal write hook.
        self = self.with_context(msb_skip_journal_tax_sync=True)
        resolver = self.env['account.journal']._msb_resolve_line_taxes
        for order in self:
            journal = order.journal_id
            fiscal_position = order.fiscal_position_id
            company = order.company_id
            for line in order.order_line:
                if line.display_type or line.is_downpayment:
                    continue
                taxes = resolver(
                    journal, line.product_id, line.tax_ids,
                    fiscal_position, company, 'purchase',
                )
                if set(line.tax_ids.ids) != set(taxes.ids):
                    line.tax_ids = taxes

    @api.onchange('journal_id')
    def _onchange_journal(self):
        """Recompute taxes in the form. ``write`` persists the same result and updates draft bills."""
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
