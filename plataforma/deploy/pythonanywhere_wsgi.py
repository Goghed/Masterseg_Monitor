# Cole TODO este conteúdo no arquivo WSGI do PythonAnywhere
# (aba Web > seção "Code" > link "WSGI configuration file"), apagando o que houver lá.
# Troque SEU_USUARIO pelo seu usuário do PythonAnywhere.
import os
import sys

PROJETO = "/home/SEU_USUARIO/masterseg-monitor/plataforma"
if PROJETO not in sys.path:
    sys.path.insert(0, PROJETO)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from django.core.wsgi import get_wsgi_application  # noqa: E402

application = get_wsgi_application()
