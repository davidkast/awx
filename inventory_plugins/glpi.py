from ansible.plugins.inventory import BaseInventoryPlugin
from ansible.errors import AnsibleError
import requests
import os
import sys

DOCUMENTATION = r'''
name: glpi
plugin_type: inventory
short_description: Plugin GLPI con Grupos Automáticos (Windows/Linux)
description:
  - Recupera hosts, detecta IP y crea grupos por Sistema Operativo.
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
        if not glpi_url: raise AnsibleError("glpi_url is required")
        glpi_url = glpi_url.rstrip('/')

        app_token = os.getenv("GLPI_APP_TOKEN")
        user_token = os.getenv("GLPI_USER_TOKEN")

        if not app_token or not user_token:
            raise AnsibleError("GLPI tokens missing env vars")

        headers = {
            "App-Token": app_token,
            "Authorization": f"user_token {user_token}",
            "Content-Type": "application/json"
        }

        try:
            # 1. INIT SESSION
            session = requests.get(f"{glpi_url}/initSession", headers=headers)
            session.raise_for_status()
            headers["Session-Token"] = session.json()["session_token"]
            
            # 2. DETECTAR IDs DE CAMPOS (IP y OS)
            print("INFO: Detecting fields...", file=sys.stderr)
            opts = requests.get(f"{glpi_url}/listSearchOptions/Computer", headers=headers).json()

            ip_id = "31" # Default fallback
            os_id = "45" # Default fallback (Operating System)
            
            # Bucle de detección inteligente
            for key, val in opts.items():
                if isinstance(val, dict) and "name" in val:
                    fname = val["name"].lower()
                    
                    # Detectar IP (Prioridad: Public Contact > IP Address)
                    if "public contact address" in fname: ip_id = key
                    elif "ip address" in fname and ip_id == "31": ip_id = key
                    
                    # Detectar Sistema Operativo
                    if fname in ["operating system", "sistema operativo", "système d'exploitation"]:
                        os_id = key

            print(f"INFO: Using ID {ip_id} for IP and ID {os_id} for OS.", file=sys.stderr)

            # 3. BUSCAR EQUIPOS
            params = {
                "forcedisplay[0]": "1",      # Nombre
                "forcedisplay[1]": ip_id,    # IP
                "forcedisplay[2]": os_id,    # OS
                "range": "0-1000"
            }
            
            data = requests.get(f"{glpi_url}/search/Computer", headers=headers, params=params).json().get("data", [])

            # 4. PROCESAR HOSTS Y GRUPOS
            # Aseguramos que los grupos existan
            inventory.add_group("windows")
            inventory.add_group("linux")
            inventory.add_group("otros")

            for item in data:
                name = item.get("1")
                raw_ip = item.get(str(ip_id))
                raw_os = item.get(str(os_id)) # El nombre del SO (ej: Ubuntu 22.04)

                if not name: continue
                
                inventory.add_host(name)

                # -- Lógica de IP --
                if raw_ip:
                    ip = str(raw_ip).replace("<br>", "\n").split("\n")[0].strip()
                    if "." in ip: inventory.set_variable(name, "ansible_host", ip)

                # -- Lógica de Grupos (Windows vs Linux) --
                os_name = str(raw_os).lower() if raw_os else ""
                
                # Clasificación simple
                if "windows" in os_name:
                    inventory.add_child("windows", name)
                elif any(x in os_name for x in ["linux", "ubuntu", "debian", "red hat", "centos", "fedora", "suse"]):
                    inventory.add_child("linux", name)
                else:
                    inventory.add_child("otros", name)
                    
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
        finally:
            try: requests.get(f"{glpi_url}/killSession", headers=headers)
            except: pass
