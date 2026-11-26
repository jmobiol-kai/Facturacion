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
