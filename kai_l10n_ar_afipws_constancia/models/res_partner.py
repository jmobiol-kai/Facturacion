import unicodedata

from odoo import models


def _kai_strip_accents(value):
    text = (value or "").strip().upper()
    return "".join(
        ch for ch in unicodedata.normalize("NFD", text) if unicodedata.category(ch) != "Mn"
    )


class ResPartner(models.Model):
    _inherit = "res.partner"

    def parce_census_vals(self, census):
        vals = super().parce_census_vals(census)
        argentina = self.env.ref("base.ar", raise_if_not_found=False)
        if argentina:
            vals["country_id"] = argentina.id
        if census.provincia and not vals.get("state_id"):
            state = self._kai_find_afip_state(census.provincia, argentina)
            if state:
                vals["state_id"] = state.id
        return vals

    def _kai_find_afip_state(self, provincia, country=None):
        """Match AFIP names like 'RIO NEGRO' to Odoo states like 'Río Negro'."""
        wanted = _kai_strip_accents(provincia)
        if not wanted:
            return self.env["res.country.state"]
        domain = [("country_id.code", "=", "AR")]
        if country:
            domain = [("country_id", "=", country.id)]
        # CABA uses codes C / CABA / ABA in l10n_ar_afipws
        if wanted in {
            "CIUDAD AUTONOMA BUENOS AIRES",
            "CIUDAD AUTONOMA DE BUENOS AIRES",
            "CABA",
            "CAPITAL FEDERAL",
        }:
            return self.env["res.country.state"].search(
                domain + [("code", "in", ["C", "CABA", "ABA"])],
                limit=1,
            )
        for state in self.env["res.country.state"].search(domain):
            if _kai_strip_accents(state.name) == wanted:
                return state
        return self.env["res.country.state"]
