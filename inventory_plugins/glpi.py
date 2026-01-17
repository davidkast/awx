from ansible.plugins.inventory import BaseInventoryPlugin
from ansible.errors import AnsibleError
import requests
import os

DOCUMENTATION = r'''
name: glpi
plugin_type: inventory
short_description: Inventory plugin for GLPI
description:
  - Retrieves hosts from GLPI via REST API
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

        app_token = os.getenv("GLPI_APP_TOKEN")
        user_token = os.getenv("GLPI_USER_TOKEN")

        if not app_token or not user_token:
            raise AnsibleError("GLPI tokens not found in environment variables")

        headers = {
            "App-Token": app_token,
            "Authorization": f"user_token {user_token}"
        }

        # Iniciar sesión
        session = requests.get(f"{glpi_url}/initSession", headers=headers)
        session.raise_for_status()
        session_token = session.json()["session_token"]
        headers["Session-Token"] = session_token

        # Obtener equipos
        computers = requests.get(f"{glpi_url}/Computer", headers=headers)
        computers.raise_for_status()

        for host in computers.json():
            hostname = host.get("name")
            if not hostname:
                continue

            inventory.add_host(hostname)
            inventory.set_variable(hostname, "glpi_id", host["id"])

        # Cerrar sesión
        requests.get(f"{glpi_url}/killSession", headers=headers)
