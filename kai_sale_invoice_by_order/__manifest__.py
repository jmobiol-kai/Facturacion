{
    "name": "Factura: una línea por pedido de venta",
    "summary": "Al facturar uno o varios pedidos de venta, agrupa las líneas en una sola por pedido (y por alícuota, si un pedido combina impuestos distintos).",
    "version": "19.0.1.0.2",
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
