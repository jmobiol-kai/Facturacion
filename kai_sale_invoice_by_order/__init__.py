from . import models


def post_init_hook(env):
    """En la instalación el agrupamiento queda activo en todas las compañías."""
    env["res.company"].with_context(active_test=False).search([]).write(
        {"invoice_by_sale_order": True}
    )
