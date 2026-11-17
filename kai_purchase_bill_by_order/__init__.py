from . import models
from . import wizard


def post_init_hook(env):
    """En la instalación el agrupamiento queda activo en todas las compañías."""
    env["res.company"].with_context(active_test=False).search([]).write(
        {"bill_by_purchase_order": True}
    )
