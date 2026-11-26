# kai_l10n_ar_afipws_constancia

El botón **AFIP A5** de `l10n_ar_afipws` pide a WSAA el servicio `ws_sr_padron_a5`. ARCA lo deprecó y responde `coe.notAuthorized` («Computador no autorizado a acceder al servicio»), aunque el certificado ya tenga adherida la Constancia de Inscripción.

Este módulo, sin modificar ingadhoc:

- redirige `get_connection('ws_sr_padron_a5')` a `ws_sr_constancia_inscripcion`;
- sigue consultando el mismo WSDL de personaServiceA5
  (`https://aws.afip.gov.ar/sr-padron/webservices/personaServiceA5?wsdl`;
  en homologación, el equivalente de `awshomo.afip.gov.ar`);
- restaura `configparser.SafeConfigParser = ConfigParser` para que PyAfipWs 3.12.0
  importe `WSSrPadronA5` en Python 3.12.

Versión `19.0.1.0.0`. Depende de `l10n_ar_afipws`.

En 19.0.1.1.0 el wizard «AFIP A5» también:

- incluye `state_id` y `country_id` entre los campos a actualizar;
- pone el país en Argentina y compara la provincia sin acentos (ARCA devuelve
  «RIO NEGRO», Odoo tiene «Río Negro»);
- convierte a entero los ids Many2one al grabar, porque en Odoo 19 un
  `write({'l10n_ar_afip_responsibility_type_id': '6'})` queda en blanco;
- muestra el mensaje final en español: «No quedan más contactos para actualizar
  en esta solicitud.» (es el cierre normal del asistente, no un error).
