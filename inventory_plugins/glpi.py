from ansible.plugins.inventory import BaseInventoryPlugin
from ansible.errors import AnsibleError
import requests
import os
import sys

DOCUMENTATION = r'''
name: glpi
plugin_type: inventory
short_description: Inventory plugin for GLPI
description:
  - Retrieves hosts from GLPI via REST API and sets ansible_host dynamically based on Public Contact Address
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

        # 1. INICIAR SESIÓN
        try:
            session = requests.get(f"{glpi_url}/initSession", headers=headers)
            session.raise_for_status()
            session_token = session.json()["session_token"]
            headers["Session-Token"] = session_token
        except Exception as e:
            raise AnsibleError(f"CRITICAL: Failed to init GLPI session: {e}")

        try:
            # 2. AUTODETECTAR EL ID DEL CAMPO CORRECTO
            print("INFO: Detecting IP field ID...", file=sys.stderr)
            opts_req = requests.get(f"{glpi_url}/listSearchOptions/Computer", headers=headers)
            opts_req.raise_for_status()
            search_options = opts_req.json()

            ip_field_id = None
            
            # Lista de nombres a buscar en orden de preferencia
            # "public contact address" es el que usa tu agente GLPI
            priority_names = ["public contact address", "contact address", "ip address", "dirección ip"]

            for key, val in search_options.items():
                if isinstance(val, dict) and "name" in val:
                    field_name = val["name"].lower()
                    
                    # Chequeamos si el nombre del campo coincide con alguno de nuestra lista prioritaria
                    for p_name in priority_names:
                        if p_name in field_name:
                            print(f"DEBUG: Found candidate field: ID {key} - Name: {val['name']}", file=sys.stderr)
                            ip_field_id = key
                            # Si encontramos "public contact address", rompemos el bucle y nos quedamos con este.
                            if "public" in field_name:
                                break
                    if ip_field_id and "public" in field_name:
                         break
            
            if not ip_field_id:
                print("WARNING: Could not auto-detect fields. Defaulting to standard ID 31.", file=sys.stderr)
                ip_field_id = "31" 
            else:
                print(f"INFO: Using ID {ip_field_id} for IP Address extraction.", file=sys.stderr)

            # 3. BUSCAR EQUIPOS
            search_params = {
                "forcedisplay[0]": "1",          # Nombre
                "forcedisplay[1]": ip_field_id,  # La IP detectada
                "range": "0-1000"
            }

            req = requests.get(f"{glpi_url}/search/Computer", headers=headers, params=search_params)
            req.raise_for_status()
            
            raw_response = req.json()
            data = raw_response.get("data", [])

            print(f"INFO: Found {raw_response.get('totalcount', 0)} computers.", file=sys.stderr)

            for item in data:
                hostname = item.get("1")
                raw_ip = item.get(str(ip_field_id))

                if not hostname:
                    continue

                inventory.add_host(hostname)
                
                if raw_ip:
                    # Limpiamos saltos de línea y etiquetas HTML si las hubiera
                    clean_ip = str(raw_ip).replace("<br>", "\n").split("\n")[0].strip()
                    
                    # Validamos que parezca una IP (que tenga puntos)
                    if "." in clean_ip:
                        inventory.set_variable(hostname, "ansible_host", clean_ip)
                    else:
                        print(f"DEBUG: Host {hostname} has invalid IP content: {clean_ip}", file=sys.stderr)
                else:
                    print(f"WARNING: Host {hostname} has no IP in field {ip_field_id}", file=sys.stderr)

        except Exception as e:
            print(f"CRITICAL ERROR: {e}", file=sys.stderr)
            raise AnsibleError(f"Plugin Failed: {e}")

        finally:
            try:
                requests.get(f"{glpi_url}/killSession", headers=headers)
            except:
                pass
