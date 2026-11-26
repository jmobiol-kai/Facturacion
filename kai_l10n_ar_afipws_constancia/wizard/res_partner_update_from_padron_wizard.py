from ast import literal_eval

from odoo import api, models

MANY2ONE_FIELDS = (
    "state_id",
    "country_id",
    "l10n_ar_afip_responsibility_type_id",
)


class ResPartnerUpdateFromPadronWizard(models.TransientModel):
    _inherit = "res.partner.update.from.padron.wizard"

    @api.model
    def _get_domain(self):
        fields_names = [
            "name",
            "street",
            "city",
            "zip",
            "state_id",
            "country_id",
            "l10n_ar_afip_responsibility_type_id",
            "last_update_census",
        ]
        return [
            ("model", "=", "res.partner"),
            ("name", "in", fields_names),
        ]

    @api.onchange("partner_id")
    def change_partner(self):
        self.ensure_one()
        self.field_ids.unlink()
        partner = self.partner_id
        fields_names = self.field_to_update_ids.mapped("name")
        if partner:
            partner_vals = partner.get_data_from_padron_afip()
            lines = []
            fields_names = list(set(partner_vals) & set(fields_names))
            for key in fields_names:
                old_value = partner[key]
                new_value = partner_vals[key]
                if new_value == "":
                    new_value = False
                if self.title_case and key in ("name", "city", "street"):
                    new_value = new_value and new_value.title()
                if key in ("impuestos_padron", "actividades_padron"):
                    old_value = old_value.ids
                elif key in MANY2ONE_FIELDS:
                    old_value = old_value.id
                if new_value and key in fields_names and old_value != new_value:
                    lines.append(
                        (
                            0,
                            False,
                            {
                                "wizard_id": self.id,
                                "field": key,
                                "old_value": old_value,
                                "new_value": new_value or False,
                            },
                        )
                    )
            self.field_ids = lines

    def _update(self):
        self.ensure_one()
        vals = {}
        for field in self.field_ids:
            if field.field in ("impuestos_padron", "actividades_padron"):
                vals[field.field] = [(6, False, literal_eval(field.new_value))]
            elif field.field in MANY2ONE_FIELDS:
                # Odoo 19 Many2one.convert_to_cache turns a string id into None,
                # so write({'l10n_ar_afip_responsibility_type_id': '6'}) is a no-op.
                raw = field.new_value
                vals[field.field] = int(raw) if raw not in (False, None, "") else False
            else:
                vals[field.field] = field.new_value
        if vals:
            self.partner_id.write(vals)
