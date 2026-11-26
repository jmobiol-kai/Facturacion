# PyAfipWs 3.12.0 still does "from configparser import SafeConfigParser".
# That name was removed in Python 3.12, so WSSrPadronA5 cannot be imported
# unless we restore the alias before the first import of ws_sr_padron.
import configparser

if not hasattr(configparser, "SafeConfigParser"):
    configparser.SafeConfigParser = configparser.ConfigParser

from . import models
from . import wizard
