# -*- coding: utf-8 -*-

from odoo import api, fields, models, _


class AccountJournal(models.Model):
    _inherit = 'account.journal'
    
    # NEW: Tax mapping system
    tax_mapping_ids = fields.One2many(
        'account.journal.tax.mapping',
        'journal_id',
        string='Mapeos de Impuestos',
        help="Configure mapeos específicos de impuestos para este diario. "
             "Cuando los productos tengan impuestos que coincidan con el 'Impuesto Original', "
             "serán reemplazados con el 'Impuesto de Reemplazo'."
    )
    
    # Computed fields for easy access to mappings by type
    sale_tax_mapping_ids = fields.One2many(
        'account.journal.tax.mapping',
        'journal_id',
        string='Mapeos de Impuestos de Venta',
        domain=[('tax_type', '=', 'sale')],
        help="Mapeos de impuestos para operaciones de venta"
    )
    
    purchase_tax_mapping_ids = fields.One2many(
        'account.journal.tax.mapping',
        'journal_id', 
        string='Mapeos de Impuestos de Compra',
        domain=[('tax_type', '=', 'purchase')],
        help="Mapeos de impuestos para operaciones de compra"
    )
    
    usar_cuenta_defecto_facturas = fields.Boolean(
        string='Usar Cuenta por Defecto en Facturas',
        default=True,
        help="Si está activado, todas las líneas de factura usarán la cuenta por defecto del diario "
             "en lugar de la cuenta del producto. El usuario puede cambiarla manualmente después."
    )

    usar_cuenta_defecto_compras = fields.Boolean(
        string='Usar Cuenta por Defecto en Compras',
        default=False,
        help="Si está activado, las líneas de facturas de proveedor usarán la cuenta por defecto del diario "
             "en lugar de la cuenta del producto/categoría."
    )
    
    # Helper methods for tax mapping
    
    def map_taxes(self, taxes, tax_type='sale'):
        """
        Map taxes based on journal configuration.
        
        Args:
            taxes: recordset of account.tax
            tax_type: 'sale' or 'purchase'
            
        Returns:
            recordset of account.tax with mappings applied
        """
        if not taxes:
            return taxes
            
        # Get active mappings for this tax type
        mappings = self.tax_mapping_ids.filtered(
            lambda m: m.tax_type == tax_type and m.active
        )
        
        if not mappings:
            return taxes
            
        # Create mapping dictionary for efficiency
        mapping_dict = {m.original_tax_id.id: m.replacement_tax_id for m in mappings}
        
        # Apply mappings
        result_taxes = self.env['account.tax']
        for tax in taxes:
            mapped_tax = mapping_dict.get(tax.id, tax)
            result_taxes |= mapped_tax
            
        return result_taxes
    
    def get_mapped_tax(self, original_tax, tax_type='sale'):
        """
        Get the mapped tax for a single tax.
        
        Args:
            original_tax: single account.tax record
            tax_type: 'sale' or 'purchase'
            
        Returns:
            account.tax record (mapped or original)
        """
        if not original_tax:
            return original_tax
            
        mapping = self.tax_mapping_ids.filtered(
            lambda m: m.original_tax_id == original_tax 
                     and m.tax_type == tax_type 
                     and m.active
        )
        
        return mapping.replacement_tax_id if mapping else original_tax
    
    def has_tax_mappings(self, tax_type='sale'):
        """
        Check if journal has any active tax mappings for given type.
        
        Args:
            tax_type: 'sale' or 'purchase'
            
        Returns:
            bool: True if has active mappings
        """
        return bool(self.tax_mapping_ids.filtered(
            lambda m: m.tax_type == tax_type and m.active
        ))

    @api.model
    def _msb_resolve_line_taxes(self, journal, product, current_taxes, fiscal_position, company, tax_type='purchase'):
        """Taxes for a product line after the journal changes.

        Starts from the product taxes (supplier taxes for purchases), filtered
        by company, then applies this journal's mappings and the fiscal
        position. That is the historical ``_recompute_line_taxes`` pipeline.

        Mappings are also applied to the taxes already on the line. When that
        changes the set, the mapped line taxes win, so a reverse map
        (IVA 10,5% X → IVA 10,5%) works even if the product tax is a different
        rate. When the line taxes are already the replacement side of this
        journal's mappings, they are kept, so the same resolution can run
        again (onchange, then ``write``) without falling back to the product
        tax and undoing the reverse map.

        A journal with no mappings, or whose mappings do not mention the tax
        currently on the line, falls back to the product taxes. A leftover
        X tax is then removed when the line moves to an FE journal.
        """
        tax_model = self.env['account.tax']
        if product:
            source = product.supplier_taxes_id if tax_type == 'purchase' else product.taxes_id
            product_taxes = source._filter_taxes_by_company(company)
        else:
            product_taxes = tax_model.browse()
        current_taxes = current_taxes or tax_model.browse()

        has_maps = bool(journal) and journal.has_tax_mappings(tax_type)
        if not has_maps:
            taxes = product_taxes
        else:
            from_product = journal.map_taxes(product_taxes, tax_type) if product_taxes else tax_model.browse()
            if not current_taxes:
                taxes = from_product
            else:
                mapped_current = journal.map_taxes(current_taxes, tax_type)
                replacement_ids = set(journal.tax_mapping_ids.filtered(
                    lambda mapping: mapping.tax_type == tax_type and mapping.active
                ).mapped('replacement_tax_id').ids)
                if set(mapped_current.ids) != set(current_taxes.ids):
                    taxes = mapped_current
                elif set(current_taxes.ids) <= replacement_ids:
                    taxes = current_taxes
                else:
                    taxes = from_product

        if fiscal_position and taxes:
            taxes = fiscal_position.map_tax(taxes)
        return taxes

    # Legacy default tax fields fully removed; tax mapping model replaces them.
