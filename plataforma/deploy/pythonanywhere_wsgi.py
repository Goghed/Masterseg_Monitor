# Cole TODO este conteúdo no arquivo WSGI do PythonAnywhere
# (aba Web > seção "Code" > link "WSGI configuration file"), apagando o que houver lá.
# PROJETO = caminho da pasta "plataforma" (o setup mostra o caminho certo no final).
import os
import sys

PROJETO = "/home/SEU_USUARIO/Masterseg_Monitor/plataforma"
if PROJETO not in sys.path:
    sys.path.insert(0, PROJETO)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from django.core.wsgi import get_wsgi_application  # noqa: E402

application = get_wsgi_application()
