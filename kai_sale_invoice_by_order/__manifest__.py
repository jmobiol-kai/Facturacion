{
    "name": "Factura: una línea por pedido de venta",
    "summary": "Al facturar pedidos de venta, una sección por pedido con los productos en el asiento (el PDF muestra un importe por pedido), una línea por producto, o el detalle sin consolidar.",
    "version": "19.0.1.3.1",
    "category": "Sales/Sales",
    "author": "Mariano Boisselier",
    "license": "LGPL-3",
    "depends": ["sale", "account"],
    "data": [
        "views/res_config_settings_views.xml",
        "views/sale_advance_payment_inv_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
}
