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

            ip_id = None
            os_id = None
            
            # Listas de prioridad (Minúsculas para comparar)
            ip_priority = ["public contact address", "contact address", "ip address", "dirección ip"]
            # AQUI ESTA EL CAMBIO: Añadido "sistema operativo - nombre"
            os_priority = ["sistema operativo - nombre", "operating system - name", "sistema operativo", "operating system", "système d'exploitation"]

            for key, val in opts.items():
                if isinstance(val, dict) and "name" in val:
                    fname = val["name"].lower()
                    
                    # Detectar IP
                    if not ip_id:
                        for p_ip in ip_priority:
                            if p_ip in fname:
                                if "public" in fname: # Prioridad absoluta
                                    ip_id = key
                                    print(f"DEBUG: IP Field FOUND (High Priority): '{val['name']}' (ID: {key})", file=sys.stderr)
                                    break
                                elif ip_id is None: # Si no tenemos ninguno, cogemos el primero que coincida
                                    ip_id = key
                                    print(f"DEBUG: IP Field Candidate: '{val['name']}' (ID: {key})", file=sys.stderr)

                    # Detectar SO
                    if not os_id:
                        for p_os in os_priority:
                            if p_os in fname:
                                os_id = key
                                print(f"DEBUG: OS Field FOUND: '{val['name']}' (ID: {key})", file=sys.stderr)
                                break
            
            # Fallbacks si no detecta nada
            if not ip_id: 
                ip_id = "31"
                print("WARNING: IP field not detected. Using default ID 31.", file=sys.stderr)
            if not os_id: 
                os_id = "45"
                print("WARNING: OS field not detected. Using default ID 45.", file=sys.stderr)
            
            print(f"INFO: FINAL IDs -> IP: {ip_id} | OS: {os_id}", file=sys.stderr)

            # 3. BUSCAR EQUIPOS
            params = {
                "forcedisplay[0]": "1",      # Nombre
                "forcedisplay[1]": ip_id,    # IP
                "forcedisplay[2]": os_id,    # OS
                "range": "0-1000"
            }
            
            # search devuelve { totalcount: X, data: [ ... ] }
            resp = requests.get(f"{glpi_url}/search/Computer", headers=headers, params=params).json()
            data = resp.get("data", [])

            # 4. PROCESAR HOSTS Y GRUPOS
            # Creamos los grupos explícitamente
            inventory.add_group("windows")
            inventory.add_group("linux")
            inventory.add_group("otros")

            for item in data:
                # El campo '1' siempre es el nombre del equipo
                name = item.get("1")
                
                # Recuperamos los valores usando los IDs detectados
                raw_ip = item.get(str(ip_id))
                raw_os = item.get(str(os_id)) 

                if not name: continue
                
                inventory.add_host(name)

                # -- Lógica de IP --
                if raw_ip:
                    # Limpieza de IP (quitar <br> y coger la primera)
                    ip = str(raw_ip).replace("<br>", "\n").split("\n")[0].strip()
                    if "." in ip: 
                        inventory.set_variable(name, "ansible_host", ip)

                # -- Lógica de Grupos (Windows vs Linux) --
                os_name = str(raw_os).lower() if raw_os else "desconocido"
                
                # Debug para ver qué está leyendo realmente
                # print(f"DEBUG HOST: {name} | OS RAW: {raw_os}", file=sys.stderr)

                if "windows" in os_name:
                    inventory.add_child("windows", name)
                elif any(x in os_name for x in ["linux", "ubuntu", "debian", "red hat", "centos", "fedora", "suse"]):
                    inventory.add_child("linux", name)
                else:
                    inventory.add_child("otros", name)
                    
        except Exception as e:
            print(f"ERROR CRITICO: {e}", file=sys.stderr)
            raise AnsibleError(e)
        finally:
            try: requests.get(f"{glpi_url}/killSession", headers=headers)
            except: pass
