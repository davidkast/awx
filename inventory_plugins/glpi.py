from ansible.plugins.inventory import BaseInventoryPlugin
from ansible.errors import AnsibleError
import requests
import os
import sys

DOCUMENTATION = r'''
name: glpi
plugin_type: inventory
short_description: Debugging GLPI Groups
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
        
        glpi_url = config.get('glpi_url').rstrip('/')
        headers = {
            "App-Token": os.getenv("GLPI_APP_TOKEN"),
            "Authorization": f"user_token {os.getenv('GLPI_USER_TOKEN')}",
            "Content-Type": "application/json"
        }

        try:
            # 1. INIT
            session = requests.get(f"{glpi_url}/initSession", headers=headers)
            session.raise_for_status()
            headers["Session-Token"] = session.json()["session_token"]
            
            # 2. DETECTAR CAMPOS
            print("--- INICIO DEPURACION CAMPOS ---", file=sys.stderr)
            opts = requests.get(f"{glpi_url}/listSearchOptions/Computer", headers=headers).json()
            
            ip_id = "31"  # Fallback
            os_id = "45"  # Fallback
            
            # Buscar IDs
            for key, val in opts.items():
                if isinstance(val, dict):
                    name = val.get("name", "").lower()
                    
                    # IP: Buscamos 'public contact' o 'contact address'
                    if "public contact" in name: 
                        ip_id = key
                        print(f"DETECTADO CAMPO IP: '{val['name']}' -> ID: {key}", file=sys.stderr)
                    
                    # OS: Buscamos 'sistema operativo - nombre' o versiones similares
                    if "sistema operativo" in name and "nombre" in name:
                        os_id = key
                        print(f"DETECTADO CAMPO SO: '{val['name']}' -> ID: {key}", file=sys.stderr)
                    elif "operating system" in name and "name" in name:
                         os_id = key
                         print(f"DETECTADO CAMPO SO: '{val['name']}' -> ID: {key}", file=sys.stderr)

            print(f"IDs FINALES USADOS -> IP: {ip_id} | OS: {os_id}", file=sys.stderr)

            # 3. BUSQUEDA
            params = {
                "forcedisplay[0]": "1",      # Nombre
                "forcedisplay[1]": ip_id,    # IP
                "forcedisplay[2]": os_id,    # OS
                "range": "0-1000"
            }
            
            data = requests.get(f"{glpi_url}/search/Computer", headers=headers, params=params).json().get("data", [])

            # 4. CREAR GRUPOS
            inventory.add_group("linux")
            inventory.add_group("windows")
            inventory.add_group("otros")

            print("--- ANALISIS DE EQUIPOS ---", file=sys.stderr)
            for item in data:
                name = item.get("1")
                if not name: continue
                
                inventory.add_host(name)
                
                # IP
                raw_ip = item.get(str(ip_id))
                if raw_ip:
                    ip = str(raw_ip).replace("<br>", "\n").split("\n")[0].strip()
                    if "." in ip: inventory.set_variable(name, "ansible_host", ip)

                # GRUPOS (Aquí es donde miraremos el log)
                raw_os = item.get(str(os_id))
                os_str = str(raw_os).lower() if raw_os else "nulo"
                
                print(f"EQUIPO: {name} | VALOR RAW SO: '{raw_os}' | VALOR LOWER: '{os_str}'", file=sys.stderr)

                if "windows" in os_str:
                    inventory.add_child("windows", name)
                    print(f" -> Asignado a WINDOWS", file=sys.stderr)
                elif any(x in os_str for x in ["linux", "ubuntu", "debian", "red hat"]):
                    inventory.add_child("linux", name)
                    print(f" -> Asignado a LINUX", file=sys.stderr)
                else:
                    inventory.add_child("otros", name)
                    print(f" -> Asignado a OTROS", file=sys.stderr)

        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
        finally:
            requests.get(f"{glpi_url}/killSession", headers=headers)
