import requests
import json
import time
import paho.mqtt.client as mqtt
from typing import Dict, Any
import os
from datetime import datetime

class PortainerMonitor:
    def __init__(self, portainer_url: str, portainer_api_key: str, 
                 mqtt_host: str, mqtt_port: int = 1883,
                 mqtt_user: str = None, mqtt_pass: str = None):
        """Initialize the Portainer monitor."""
        self.portainer_url = portainer_url.rstrip('/')
        self.headers = {
            'X-API-Key': portainer_api_key,
            'Content-Type': 'application/json'
        }
        
        # MQTT setup
        self.mqtt_client = mqtt.Client()
        if mqtt_user and mqtt_pass:
            self.mqtt_client.username_pw_set(mqtt_user, mqtt_pass)
        self.mqtt_client.connect(mqtt_host, mqtt_port)
        self.mqtt_client.loop_start()

        # Discovery prefix for Home Assistant
        self.discovery_prefix = 'homeassistant'

    def publish_discovery_config(self, endpoint_name: str, container_name: str):
        """Publish MQTT discovery configuration for Home Assistant."""
        device_info = {
            "identifiers": [f"docker_{endpoint_name}_{container_name}"],
            "name": f"Docker {container_name}",
            "manufacturer": "Docker",
            "model": "Container",
            "via_device": f"docker_{endpoint_name}"
        }

        # Define sensors
        sensors = {
            'state': {
                'name': 'State',
                'icon': 'mdi:docker',
                'state_class': 'measurement'
            },
            'cpu_percent': {
                'name': 'CPU Usage',
                'icon': 'mdi:cpu-64-bit',
                'unit_of_measurement': '%',
                'state_class': 'measurement'
            },
            'memory_percent': {
                'name': 'Memory Usage',
                'icon': 'mdi:memory',
                'unit_of_measurement': '%',
                'state_class': 'measurement'
            }
        }

        base_topic = f"docker/{endpoint_name}/{container_name}"

        # Publish discovery config for each sensor
        for sensor_type, config in sensors.items():
            discovery_topic = f"{self.discovery_prefix}/sensor/{endpoint_name}_{container_name}_{sensor_type}/config"
            
            payload = {
                "name": f"{container_name} {config['name']}",
                "unique_id": f"docker_{endpoint_name}_{container_name}_{sensor_type}",
                "state_topic": f"{base_topic}/{sensor_type}",
                "icon": config['icon'],
                "device": device_info
            }

            if 'unit_of_measurement' in config:
                payload['unit_of_measurement'] = config['unit_of_measurement']
            
            if 'state_class' in config:
                payload['state_class'] = config['state_class']

            self.mqtt_client.publish(discovery_topic, json.dumps(payload), retain=True)

    def get_endpoints(self) -> list:
        """Get all Portainer endpoints (Docker hosts)."""
        response = requests.get(f"{self.portainer_url}/api/endpoints", 
                              headers=self.headers)
        return response.json()

    def get_containers(self, endpoint_id: int) -> list:
        """Get all containers for a specific endpoint."""
        response = requests.get(
            f"{self.portainer_url}/api/endpoints/{endpoint_id}/docker/containers/json?all=true",
            headers=self.headers
        )
        return response.json()

    def get_container_stats(self, endpoint_id: int, container_id: str) -> Dict[str, Any]:
        """Get stats for a specific container."""
        response = requests.get(
            f"{self.portainer_url}/api/endpoints/{endpoint_id}/docker/containers/{container_id}/stats?stream=false",
            headers=self.headers
        )
        stats = response.json()
        
        # Calculate CPU percentage
        cpu_delta = stats['cpu_stats']['cpu_usage']['total_usage'] - \
                   stats['precpu_stats']['cpu_usage']['total_usage']
        system_delta = stats['cpu_stats']['system_cpu_usage'] - \
                      stats['precpu_stats']['system_cpu_usage']
        cpu_percent = 0.0
        if system_delta > 0:
            cpu_percent = (cpu_delta / system_delta) * 100.0 * len(stats['cpu_stats']['cpu_usage']['percpu_usage'])

        # Calculate memory percentage
        mem_percent = (stats['memory_stats']['usage'] / stats['memory_stats']['limit']) * 100.0

        return {
            'cpu_percent': round(cpu_percent, 2),
            'memory_percent': round(mem_percent, 2)
        }

    def container_action(self, endpoint_id: int, container_id: str, action: str) -> bool:
        """Perform action (start/stop/restart) on a container."""
        response = requests.post(
            f"{self.portainer_url}/api/endpoints/{endpoint_id}/docker/containers/{container_id}/{action}",
            headers=self.headers
        )
        return response.status_code == 204

    def publish_container_data(self, endpoint_name: str, container_name: str, data: Dict[str, Any]):
        """Publish container data to MQTT."""
        base_topic = f"docker/{endpoint_name}/{container_name}"
        
        # Publish each metric separately
        for key, value in data.items():
            self.mqtt_client.publish(f"{base_topic}/{key}", value)

    def monitor_loop(self, interval: int = 60):
        """Main monitoring loop."""
        while True:
            try:
                endpoints = self.get_endpoints()
                
                for endpoint in endpoints:
                    containers = self.get_containers(endpoint['Id'])
                    
                    for container in containers:
                        container_name = container['Names'][0].lstrip('/')
                        
                        # Publish discovery configuration
                        self.publish_discovery_config(endpoint['Name'], container_name)
                        
                        # Get basic container info
                        container_data = {
                            'state': container['State'],
                            'status': container['Status'],
                            'last_updated': datetime.now().isoformat()
                        }
                        
                        # Only get stats if container is running
                        if container['State'] == 'running':
                            try:
                                stats = self.get_container_stats(endpoint['Id'], container['Id'])
                                container_data.update(stats)
                            except Exception as e:
                                print(f"Error getting stats for {container_name}: {str(e)}")
                        
                        self.publish_container_data(endpoint['Name'], container_name, container_data)
                
            except Exception as e:
                print(f"Error in monitoring loop: {str(e)}")
            
            time.sleep(interval)

if __name__ == "__main__":
    # Configuration
    PORTAINER_URL = os.getenv('PORTAINER_URL', 'http://localhost:9000')
    PORTAINER_API_KEY = os.getenv('PORTAINER_API_KEY')
    MQTT_HOST = os.getenv('MQTT_HOST', 'localhost')
    MQTT_PORT = int(os.getenv('MQTT_PORT', '1883'))
    MQTT_USER = os.getenv('MQTT_USER')
    MQTT_PASS = os.getenv('MQTT_PASS')
    UPDATE_INTERVAL = int(os.getenv('UPDATE_INTERVAL', '60'))

    monitor = PortainerMonitor(
        PORTAINER_URL,
        PORTAINER_API_KEY,
        MQTT_HOST,
        MQTT_PORT,
        MQTT_USER,
        MQTT_PASS
    )
    
    monitor.monitor_loop(UPDATE_INTERVAL)
