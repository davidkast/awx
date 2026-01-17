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
            opts_req = requests.get(f"{glpi_url}/listSearchOptions/Computer", headers=headers)
            opts_req.raise_for_status()
            search_options = opts_req.json()

            ip_field_id = None
            os_field_id = None

            for key, val in search_options.items():
                if not isinstance(val, dict):
                    continue
                name = val.get("name", "").lower()

                if not ip_field_id and "public contact address" in name:
                    ip_field_id = key

                if not os_field_id and "operating system" in name:
                    os_field_id = key

            if not ip_field_id:
                ip_field_id = "31"

            if not os_field_id:
                os_field_id = "10"

            search_params = {
                "forcedisplay[0]": "1",
                "forcedisplay[1]": ip_field_id,
                "forcedisplay[2]": os_field_id,
                "range": "0-2000"
            }

            req = requests.get(
                f"{glpi_url}/search/Computer",
                headers=headers,
                params=search_params
            )
            req.raise_for_status()

            data = req.json().get("data", [])

            inventory.add_group("linux")
            inventory.add_group("windows")

            for item in data:
                hostname = item.get("1")
                raw_ip = item.get(str(ip_field_id))
                os_name = str(item.get(str(os_field_id), "")).lower()

                if not hostname:
                    continue

                inventory.add_host(hostname)

                if raw_ip:
                    clean_ip = str(raw_ip).replace("<br>", "\n").split("\n")[0].strip()
                    if "." in clean_ip:
                        inventory.set_variable(hostname, "ansible_host", clean_ip)

                if "windows" in os_name:
                    inventory.add_host(hostname, group="windows")
                elif any(x in os_name for x in ["linux", "ubuntu", "debian", "centos", "red hat", "rocky", "alma"]):
                    inventory.add_host(hostname, group="linux")

        except Exception as e:
            raise AnsibleError(f"Plugin Failed: {e}")

        finally:
            try:
                requests.get(f"{glpi_url}/killSession", headers=headers)
            except:
                pass
