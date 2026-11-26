from odoo import models


class ResCompany(models.Model):
    _inherit = "res.company"

    def get_connection(self, afip_ws):
        # ARCA renamed ws_sr_padron_a5 to ws_sr_constancia_inscripcion.
        # The button AFIP A5 still asks for the old id; request the current one.
        if afip_ws == "ws_sr_padron_a5":
            afip_ws = "ws_sr_constancia_inscripcion"
        return super().get_connection(afip_ws)
