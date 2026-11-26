{
    "name": "AFIP/ARCA padrón: Constancia de Inscripción",
    "version": "19.0.1.1.0",
    "summary": "El botón AFIP A5 usa el servicio vigente ws_sr_constancia_inscripcion",
    "description": """
ARCA deprecó el id de servicio WSAA ws_sr_padron_a5 y lo reemplazó por
ws_sr_constancia_inscripcion (manual WS Constancia de Inscripción v3.7).
l10n_ar_afipws sigue pidiendo el nombre viejo, y ARCA responde
«Computador no autorizado a acceder al servicio» aunque el certificado
ya tenga adherida la Constancia de Inscripción.

Este módulo:
* hace que el botón AFIP A5 pida el ticket WSAA con ws_sr_constancia_inscripcion;
* sigue consultando el WSDL de personaServiceA5 (el endpoint no cambió);
* parchea SafeConfigParser para que PyAfipWs 3.12 importe WSSrPadronA5 en Python 3.12;
* el wizard AFIP A5 también escribe provincia, país y responsabilidad ARCA
  (el padrón de ADHOC no incluye esos campos, no matchea «RIO NEGRO» con
  «Río Negro», y en Odoo 19 un Many2one no acepta el id en texto);
* traduce el mensaje final del wizard al español.
No modifica ingadhoc.
""",
    "category": "Accounting/Localizations",
    "author": "Kaizendo",
    "website": "https://kaizendo.com.ar",
    "license": "LGPL-3",
    "depends": ["l10n_ar_afipws"],
    "data": [
        "views/res_partner_update_from_padron_wizard_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
