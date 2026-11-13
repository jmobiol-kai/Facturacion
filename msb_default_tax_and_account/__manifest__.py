# -*- coding: utf-8 -*-
{
    "name": "MSB Default Tax and Account",
    "summary": "Configuración de impuestos y cuentas por defecto en diarios para facturas de venta y compra",
    "description": """
        Módulo que simplifica la gestión de impuestos y cuentas contables en facturas mediante un 
        sistema de mapeo de impuestos granular por diario contable.
        
        **VERSIÓN 2.0 - SISTEMA DE MAPEO DE IMPUESTOS:**
        
        **Problema que resuelve:**
        - Evita cambios manuales de impuestos específicos en cada línea de factura
        - Permite mapeos granulares por diario (ej: IVA 21% → IVA 21% Export)  
        - Agiliza la creación de facturas con configuraciones específicas por tipo de operación
        - Mantiene flexibilidad: solo reemplaza impuestos específicos configurados
        
        **Características principales:**
        
        *Sistema de Mapeo de Impuestos (NUEVO v2.0):*
        - Mapeo granular: impuesto original → impuesto de reemplazo
        - Configuración por diario para ventas y compras
        - Ejemplo: "IVA 21%" se mapea a "IVA 21% Export" en diario de exportación
        - Si no hay mapeo configurado, se mantiene el impuesto original
        - Funciona con productos que tienen múltiples impuestos
        
        *Cuentas por Defecto:*
        - Usa la cuenta por defecto del diario en lugar de la cuenta del producto
        - Configuración opcional activable/desactivable por diario
        - Utiliza el campo estándar "Cuenta por Defecto" de Odoo
        
        *Control Total del Usuario:*
        - El usuario mantiene control total para modificar manualmente
        - Funciona en interfaz de usuario, órdenes de venta/compra y facturas manuales
        - Integración transparente con posiciones fiscales
        
        **Configuración del Mapeo:**
        1. Ir a Contabilidad > Configuración > Diarios
        2. Editar el diario deseado (venta o compra)
        3. En la sección "Tax Mappings" configurar:
           - Impuesto Original: el impuesto del producto
           - Reemplazo: el impuesto a usar en su lugar
        4. Repetir para cada mapeo necesario
        
        **Flujo de trabajo del mapeo:**
        1. Usuario agrega producto con impuestos (ej: "IVA 21%")
        2. Sistema verifica si hay mapeo en el diario seleccionado
        3. Si existe mapeo, reemplaza "IVA 21%" → "IVA 21% Export"
        4. Si no hay mapeo, mantiene "IVA 21%" original
        5. Usuario puede modificar manualmente si necesario
        
        **Compatibilidad:**
        - Odoo 19.0
        - Compatible con posiciones fiscales
        - Compatible con configuraciones multi-moneda  
        - Migración automática desde sistema anterior de impuestos por defecto
        - Respeta configuraciones existentes de cuentas contables
        
        **Ventajas del Mapeo vs Override Completo:**
        - Mayor flexibilidad: solo cambia impuestos específicos
        - Transparencia: claro qué impuesto se mapea a cuál
        - Control granular: diferentes mapeos por diario
        - Compatibilidad: funciona con productos multi-impuesto
    """,
    "author": "Mariano Boisselier",
    "maintainers": ["Mariano Boisselier"],
    "website": "",
    "license": "AGPL-3",
    "category": "Accounting/Accounting",
    "version": "19.0.2.0.2",
    "development_status": "Beta",
    "application": False,
    "installable": True,
    "auto_install": False,
    "depends": ["account", "purchase", "sale"],
    "data": [
        "security/ir.model.access.csv",
        "views/account_journal_views.xml",
        "views/account_journal_tax_mapping_views.xml",
        "views/recompute_taxes_wizard_views.xml",
        "views/account_move_views.xml",
        "views/purchase_order_views.xml",
        "views/sale_order_views.xml",
    ],
}
