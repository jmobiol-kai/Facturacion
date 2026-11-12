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
    
    # Legacy default tax fields fully removed; tax mapping model replaces them.
