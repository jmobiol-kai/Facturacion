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

    def _recompute_line_taxes(self):
        """Core logic to recompute all purchase order line taxes using journal mapping + fiscal position.
        Can be called from onchange (in-memory records) or from action methods (saved records).
        """
        for order in self:
            journal = order.journal_id
            fpos = order.fiscal_position_id
            company = order.company_id

            for line in order.order_line.filtered(lambda l: not l.display_type):
                taxes = line.product_id and line.product_id.supplier_taxes_id._filter_taxes_by_company(company) or self.env['account.tax']
                if journal and journal.has_tax_mappings('purchase') and taxes:
                    taxes = journal.map_taxes(taxes, 'purchase')
                mapped = fpos.map_tax(taxes) if (fpos and taxes) else taxes
                line.tax_ids = mapped

    @api.onchange('journal_id')
    def _onchange_journal(self):
        """Automatically recompute taxes when journal changes."""
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
