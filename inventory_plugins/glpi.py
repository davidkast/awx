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
  - Retrieves hosts from GLPI via REST API with Group Support
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

        try:
            session = requests.get(f"{glpi_url}/initSession", headers=headers)
            session.raise_for_status()
            session_token = session.json()["session_token"]
            headers["Session-Token"] = session_token
        except Exception as e:
            raise AnsibleError(f"Failed to init GLPI session: {e}")

        try:
            # 1. DETECTAR IDs DE CAMPOS (IP y OS)
            print("INFO: Detecting fields in GLPI...", file=sys.stderr)
            opts_req = requests.get(f"{glpi_url}/listSearchOptions/Computer", headers=headers)
            opts_req.raise_for_status()
            search_options = opts_req.json()

            ip_field_id = None
            os_field_id = None

            # Listas de términos a buscar (incluyendo ESPAÑOL)
            # Prioridad IP: Public Contact (Agente) > Contact > IP Address
            ip_terms = ["public contact address", "contact address", "ip address", "dirección ip"]
            # Prioridad OS: Operating System > Sistema Operativo
            os_terms = ["sistema operativo", "operating system", "système d'exploitation"]

            for key, val in search_options.items():
                if not isinstance(val, dict): continue
                
                name = val.get("name", "").lower()
                
                # Detectar IP
                if not ip_field_id:
                    for term in ip_terms:
                        if term in name:
                            # Prioridad absoluta a Public Contact si está presente
                            if "public" in name:
                                ip_field_id = key
                                print(f"DEBUG: Found IP Field (BEST): '{val['name']}' ID: {key}", file=sys.stderr)
                                break
                            elif "ip" in name: # Candidato
                                ip_field_id = key

                # Detectar OS (Aquí fallaba antes)
                if not os_field_id:
                    for term in os_terms:
                        if term in name:
                            # Preferimos "Nombre" o "Name" si existe
                            if "name" in name or "nombre" in name:
                                os_field_id = key
                                print(f"DEBUG: Found OS Field (BEST): '{val['name']}' ID: {key}", file=sys.stderr)
                                break
                            os_field_id = key

            # Fallbacks
            if not ip_field_id: 
                ip_field_id = "31"
                print("WARNING: IP field not auto-detected. Using 31.", file=sys.stderr)
            if not os_field_id: 
                os_field_id = "45" # El 10 suele ser usuario, 40-45 suele ser OS
                print("WARNING: OS field not auto-detected. Using 45.", file=sys.stderr)

            print(f"INFO: Final IDs -> IP: {ip_field_id} | OS: {os_field_id}", file=sys.stderr)

            # 2. BUSQUEDA
            search_params = {
                "forcedisplay[0]": "1",           # Nombre
                "forcedisplay[1]": ip_field_id,   # IP
                "forcedisplay[2]": os_field_id,   # SO
                "range": "0-2000"
            }

            req = requests.get(f"{glpi_url}/search/Computer", headers=headers, params=search_params)
            req.raise_for_status()
            resp = req.json()
            data = resp.get("data", [])
            print(f"INFO: Found {resp.get('totalcount', 0)} hosts.", file=sys.stderr)

            # Crear grupos vacíos para asegurar que existen
            inventory.add_group("linux")
            inventory.add_group("windows")
            inventory.add_group("otros")

            for item in data:
                hostname = item.get("1")
                if not hostname: continue

                # Añadimos el host al inventario global primero
                inventory.add_host(hostname)

                # Gestionar IP
                raw_ip = item.get(str(ip_field_id))
                if raw_ip:
                    clean_ip = str(raw_ip).replace("<br>", "\n").split("\n")[0].strip()
                    if "." in clean_ip:
                        inventory.set_variable(hostname, "ansible_host", clean_ip)

                # Gestionar Grupos
                raw_os = item.get(str(os_field_id))
                os_name = str(raw_os).lower() if raw_os else "desconocido"
                
                # Debug para ver qué está leyendo (aparecerá en el log de AWX)
                # print(f"DEBUG HOST: {hostname} | OS: {os_name}", file=sys.stderr)

                if "windows" in os_name:
                    inventory.add_child("windows", hostname)
                elif any(x in os_name for x in ["linux", "ubuntu", "debian", "centos", "red hat"]):
                    inventory.add_child("linux", hostname)
                else:
                    inventory.add_child("otros", hostname)

        except Exception as e:
            print(f"CRITICAL ERROR: {e}", file=sys.stderr)
            raise AnsibleError(f"Plugin Failed: {e}")

        finally:
            try: requests.get(f"{glpi_url}/killSession", headers=headers)
            except: pass
