from ansible.plugins.inventory import BaseInventoryPlugin
from ansible.errors import AnsibleError
import requests
import os

DOCUMENTATION = r'''
name: glpi
plugin_type: inventory
short_description: Inventory plugin for GLPI
description:
  - Retrieves hosts from GLPI via REST API and sets ansible_host using the IP
options:
  plugin:
    required: true
    choices: ['glpi']
  glpi_url:
    required: true
'''

class InventoryModule(BaseInventoryPlugin):

    NAME = 'glpi'

    def verify_file(self, path):
        return super().verify_file(path) and path.endswith(('.yml', '.yaml'))

    def parse(self, inventory, loader, path, cache=True):
        super().parse(inventory, loader, path)

        config = self._read_config_data(path)

        glpi_url = config.get('glpi_url')
        if not glpi_url:
            raise AnsibleError("glpi_url is required")

        # Eliminamos la barra final si existe para evitar dobles slashes
        glpi_url = glpi_url.rstrip('/')

        app_token = os.getenv("GLPI_APP_TOKEN")
        user_token = os.getenv("GLPI_USER_TOKEN")

        if not app_token or not user_token:
            raise AnsibleError("GLPI tokens not found in environment variables")

        headers = {
            "App-Token": app_token,
            "Authorization": f"user_token {user_token}",
            "Content-Type": "application/json"
        }

        # 1. Iniciar sesión
        try:
            session = requests.get(f"{glpi_url}/initSession", headers=headers)
            session.raise_for_status()
            session_token = session.json()["session_token"]
            headers["Session-Token"] = session_token
        except Exception as e:
            raise AnsibleError(f"Error connecting to GLPI initSession: {e}")

        # 2. Obtener equipos USANDO SEARCH para sacar la IP (ID 31)
        # forcedisplay[0]=1  -> ID del campo Nombre
        # forcedisplay[1]=31 -> ID del campo Dirección IP
        # range=0-1000       -> Para traer hasta 1000 equipos (ajustar si tienes más)
        
        search_params = {
            "forcedisplay[0]": "1",
            "forcedisplay[1]": "31", 
            "range": "0-1000"
        }

        try:
            # Nota: usamos /search/Computer en lugar de /Computer
            req = requests.get(f"{glpi_url}/search/Computer", headers=headers, params=search_params)
            req.raise_for_status()
            data = req.json().get("data", [])
            
            # GLPI search devuelve una lista o un diccionario dependiendo de la versión/params.
            # Normalmente 'data' es una lista de objetos donde las claves son los IDs de los campos ("1", "31").

            for item in data:
                # El campo "1" es el Nombre
                hostname = item.get("1")
                
                # El campo "31" es la IP. A veces viene null o vacío.
                ip_address = item.get("31")

                if not hostname:
                    continue
                
                # Añadimos el host al inventario (Nombre visual)
                inventory.add_host(hostname)
                
                # Ponemos el ID de GLPI por si acaso
                # En search, el ID del item suele venir en el campo "2" o como clave interna, 
                # pero para conexión ssh solo nos importa la IP.

                # SI TENEMOS IP: Le decimos a Ansible que use esa IP para conectar
                if ip_address:
                    # GLPI a veces devuelve saltos de linea si tiene varias IPs. Nos quedamos la primera.
                    if isinstance(ip_address, str):
                        ip_address = ip_address.split('\n')[0].split('<br>')[0] # Limpieza básica
                    
                    inventory.set_variable(hostname, "ansible_host", ip_address)

        except Exception as e:
            # Intentamos cerrar sesión antes de fallar
            requests.get(f"{glpi_url}/killSession", headers=headers)
            raise AnsibleError(f"Error fetching hosts from GLPI: {e}")

        # 3. Cerrar sesión
        requests.get(f"{glpi_url}/killSession", headers=headers)
