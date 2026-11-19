{
    "name": "Factura de compra: una línea por orden de compra",
    "summary": "Al facturar órdenes de compra, una sección por orden seguida de los productos reales. El PDF muestra un importe por orden sin sacar el producto del asiento.",
    "version": "19.0.1.1.0",
    "category": "Inventory/Purchase",
    "author": "Mariano Boisselier",
    "license": "LGPL-3",
    "depends": ["purchase", "account"],
    "data": [
        "security/ir.model.access.csv",
        "views/res_config_settings_views.xml",
        "wizard/purchase_bill_wizard_views.xml",
        "views/purchase_order_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
}
