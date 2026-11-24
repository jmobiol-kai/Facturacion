from . import models


def post_init_hook(env):
    """En la instalación el agrupamiento queda activo en todas las compañías.

    También se recalcula qty_invoiced de las líneas de venta ya vinculadas a
    una línea de factura compartida (facturas históricas sin flags).
    """
    env["res.company"].with_context(active_test=False).search([]).write(
        {"invoice_by_sale_order": True}
    )
    env["sale.order.line"]._kai_recompute_shared_invoice_qty()
