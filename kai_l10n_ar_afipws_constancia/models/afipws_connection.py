import logging

from odoo import _, api, fields, models
from odoo.exceptions import RedirectWarning, UserError

_logger = logging.getLogger(__name__)

CONSTANCIA_WS = "ws_sr_constancia_inscripcion"
PADRON_A5_WS = "ws_sr_padron_a5"


class AfipwsConnection(models.Model):
    _inherit = "afipws.connection"

    afip_ws = fields.Selection(
        selection_add=[
            (CONSTANCIA_WS, "Consulta de Constancia de Inscripción"),
        ],
        ondelete={CONSTANCIA_WS: "cascade"},
    )

    @api.model
    def get_afip_ws_url(self, afip_ws, environment_type):
        if afip_ws == CONSTANCIA_WS:
            # Same endpoint as the old A5 service (personaServiceA5).
            return super().get_afip_ws_url(PADRON_A5_WS, environment_type)
        return super().get_afip_ws_url(afip_ws, environment_type)

    @api.model
    def _get_ws(self, afip_ws):
        if afip_ws == CONSTANCIA_WS:
            from pyafipws.ws_sr_padron import WSSrPadronA5

            return WSSrPadronA5()
        return super()._get_ws(afip_ws)

    def connect(self):
        """Reuse the A5 client for constancia: same WSDL, HOMO=False."""
        self.ensure_one()
        if self.afip_ws != CONSTANCIA_WS:
            return super().connect()

        _logger.info(
            "Getting connection to ws %s from libraries on connection id %s",
            self.afip_ws,
            self.id,
        )
        ws = self._get_ws(self.afip_ws)
        if not ws:
            raise UserError(
                _("AFIP Webservice %s not implemented yet") % self.afip_ws
            )
        ws.HOMO = False
        wsdl = self.afip_ws_url
        try:
            ws.Conectar("", wsdl or "", "")
        except Exception as error:
            if (
                "ExpatError" in repr(error)
                or "mismatched tag" in repr(error)
                or "Conexión reinicializada por la máquina remota" in repr(error)
                or "module 'httplib2' has no attribute 'SSLHandshakeError'" in repr(error)
            ):
                action = self.env.ref("l10n_ar_afipws.action_afip_padron")
                msg = _(
                    "It seems like AFIP service is not available.\nPlease try again later or try manually"
                )
                raise RedirectWarning(msg, action.id, _("Go and find data manually"))
            raise UserError(
                _(
                    "There was a connection problem to AFIP. Contact your Odoo Provider. Error\n\n%s"
                )
                % repr(error)
            ) from error

        # Same CUIT as afipws.connection.connect() in l10n_ar_afipws (19.0).
        cuit = self.company_id.partner_id.ensure_vat()
        ws.Cuit = cuit
        ws.Token = self.token
        ws.Sign = self.sign
        ws.Obs = ""
        ws.Errores = []
        _logger.info('Connection getted with url "%s", cuit "%s"', wsdl, ws.Cuit)
        return ws
