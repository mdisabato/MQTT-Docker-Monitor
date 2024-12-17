import requests
import json
import time
import paho.mqtt.client as mqtt
from typing import Dict, Any
import os
from datetime import datetime
import logging
import sys

class PortainerMonitor:
    def __init__(self, portainer_url: str, portainer_api_key: str, 
                 mqtt_host: str, mqtt_port: int = 1883,
                 mqtt_user: str = None, mqtt_pass: str = None) -> None:
        """Initialize the Portainer monitor."""
        self.portainer_url = portainer_url.rstrip('/')
        self.headers = {
            'X-API-Key': portainer_api_key,
            'Content-Type': 'application/json'
        }
        
        # Store MQTT connection details
        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port
        self.mqtt_user = mqtt_user
        self.mqtt_pass = mqtt_pass
        self.connected = False
        
        # Discovery prefix for Home Assistant
        self.discovery_prefix = 'homeassistant'
        
        # Initialize MQTT client
        self.setup_mqtt()

    def setup_mqtt(self, max_retries=5, retry_delay=5):
        """Setup MQTT connection with retry logic."""
        retry_count = 0
        while retry_count < max_retries:
            try:
                logging.info(f"Connecting to MQTT broker at {self.mqtt_host}")
                self.mqtt_client = mqtt.Client()
                
                # Setup callbacks
                self.mqtt_client.on_connect = self.on_connect
                self.mqtt_client.on_disconnect = self.on_disconnect
                
                if self.mqtt_user and self.mqtt_pass:
                    logging.debug("Configuring MQTT authentication")
                    self.mqtt_client.username_pw_set(self.mqtt_user, self.mqtt_pass)
                
                self.mqtt_client.connect(self.mqtt_host, self.mqtt_port)
                self.mqtt_client.loop_start()
                
                # Wait for connection to be established
                wait_count = 0
                while not self.connected and wait_count < 10:
                    time.sleep(1)
                    wait_count += 1
                    
                if not self.connected:
                    raise Exception("Timed out waiting for MQTT connection")
                    
                return
                
            except Exception as e:
                retry_count += 1
                if retry_count < max_retries:
                    logging.error(f"MQTT connection failed: {str(e)}")
                    logging.info(f"Retrying in {retry_delay} seconds (Attempt {retry_count+1}/{max_retries})")
                    time.sleep(retry_delay)
                else:
                    logging.critical("Failed to connect to MQTT after multiple attempts")
                    sys.exit(1)

    def on_connect(self, client, userdata, flags, rc):
        """Callback when MQTT connects."""
        if rc == 0:
            self.connected = True
            logging.info("MQTT connection established")
        else:
            logging.error(f"MQTT connection failed with code {rc}")

    def on_disconnect(self, client, userdata, rc):
        """Callback when MQTT disconnects."""
        self.connected = False
        if rc != 0:
            logging.warning("Unexpected MQTT disconnection")

    def get_endpoints(self) -> list:
        """Get all Portainer endpoints (Docker hosts)."""
        try:
            response = requests.get(f"{self.portainer_url}/api/endpoints", 
                                headers=self.headers)
            response.raise_for_status()
            endpoints = response.json()
            logging.debug(f"Retrieved {len(endpoints)} endpoints from Portainer")
            return endpoints
        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to get endpoints: {str(e)}")
            return []

    def get_containers(self, endpoint_id: int, endpoint_name: str) -> list:
        """Get all containers for a specific endpoint."""
        try:
            response = requests.get(
                f"{self.portainer_url}/api/endpoints/{endpoint_id}/docker/containers/json?all=true",
                headers=self.headers
            )
            response.raise_for_status()
            containers = response.json()
            logging.debug(f"Retrieved {len(containers)} containers from {endpoint_name}")
            return containers
        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to get containers from {endpoint_name}: {str(e)}")
            return []

    def get_container_stats(self, endpoint_id: int, container_id: str) -> Dict[str, Any]:
        """Get stats for a specific container."""
        try:
            response = requests.get(
                f"{self.portainer_url}/api/endpoints/{endpoint_id}/docker/containers/{container_id}/stats?stream=false",
                headers=self.headers
            )
            response.raise_for_status()
            stats = response.json()
            
            cpu_delta = stats['cpu_stats']['cpu_usage']['total_usage'] - \
                       stats['precpu_stats']['cpu_usage']['total_usage']
            system_delta = stats['cpu_stats']['system_cpu_usage'] - \
                          stats['precpu_stats']['system_cpu_usage']
            
            cpu_percent = 0.0
            if system_delta > 0:
                cpu_percent = (cpu_delta / system_delta) * 100.0

            mem_percent = (stats['memory_stats']['usage'] / stats['memory_stats']['limit']) * 100.0

            return {
                'cpu_percent': round(cpu_percent, 2),
                'memory_percent': round(mem_percent, 2)
            }
        except Exception as e:
            logging.error(f"Failed to get container stats: {str(e)}")
            return {
                'cpu_percent': 0.0,
                'memory_percent': 0.0
            }

    def publish_discovery_config(self, endpoint_name: str, container_name: str):
        """Publish MQTT discovery configuration for Home Assistant."""
        logging.debug(f"Creating discovery config for {container_name} on {endpoint_name}")
        
        # Clean up names
        clean_container = container_name.replace('-', '_').replace('.', '_').lstrip('/')
        clean_endpoint = endpoint_name.replace('-', '_').replace('.', '_')
        
        # Create identifiers
        host_id = f"{clean_endpoint}_docker"
        entity_id = f"{clean_endpoint}_docker_{clean_container}"
        
        # Create device info for the Docker host
        device_info = {
            "identifiers": [host_id],
            "name": f"{endpoint_name} Docker",
            "manufacturer": "Docker",
            "model": "Host",
        }

        # Define the single sensor with all attributes
        discovery_topic = f"{self.discovery_prefix}/sensor/{entity_id}/config"
        
        payload = {
            "name": f"{container_name}",
            "unique_id": entity_id,
            "state_topic": f"docker/{endpoint_name}/{container_name}/state",
            "icon": "mdi:docker",
            "device": device_info,
            "json_attributes_topic": f"docker/{endpoint_name}/{container_name}/attributes",
            "value_template": "{{ value_json.state }}"
        }

        result = self.mqtt_client.publish(discovery_topic, json.dumps(payload), retain=True)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            logging.error(f"Failed to publish discovery config: {mqtt.error_string(result.rc)}")
        else:
            logging.debug(f"Published discovery config for {container_name}")

    def monitor_loop(self, interval: int = 60):
        """Main monitoring loop."""
        logging.info("Starting monitoring loop")
        while True:
            try:
                endpoints = self.get_endpoints()
                
                for endpoint in endpoints:
                    endpoint_name = endpoint.get('Name', f"Unknown-{endpoint['Id']}")
                    containers = self.get_containers(endpoint['Id'], endpoint_name)
                    
                    for container in containers:
                        container_name = container['Names'][0].lstrip('/')
                        logging.debug(f"Processing {container_name} on {endpoint_name}")
                        
                        # Publish discovery configuration
                        self.publish_discovery_config(endpoint['Name'], container_name)
                        
                        # State message - just the running state
                        state_data = {
                            "state": container['State']
                        }

                        # Attributes message - all the details
                        attributes = {
                            "status": container['Status'],
                            "last_updated": datetime.now().isoformat()
                        }
                        
                        # Only get stats if container is running
                        if container['State'] == 'running':
                            try:
                                stats = self.get_container_stats(endpoint['Id'], container['Id'])
                                attributes.update(stats)
                            except Exception as e:
                                logging.error(f"Failed to get stats for {container_name}: {str(e)}")
                        
                        # Publish state and attributes
                        base_topic = f"docker/{endpoint['Name']}/{container_name}"
                        self.mqtt_client.publish(f"{base_topic}/state", json.dumps(state_data))
                        self.mqtt_client.publish(f"{base_topic}/attributes", json.dumps(attributes))
                
            except Exception as e:
                logging.error(f"Error in monitoring loop: {str(e)}")
            
            time.sleep(interval)

def validate_environment():
    """Validate required environment variables."""
    required_vars = {
        'PORTAINER_URL': os.getenv('PORTAINER_URL'),
        'PORTAINER_API_KEY': os.getenv('PORTAINER_API_KEY'),
        'MQTT_HOST': os.getenv('MQTT_HOST')
    }
    
    missing_vars = [var for var, value in required_vars.items() if not value]
    
    if missing_vars:
        logging.error("Missing required environment variables:")
        for var in missing_vars:
            logging.error(f"- {var}")
        logging.info("\nRequired environment variables:")
        logging.info("export PORTAINER_URL='http://your-portainer:9000'")
        logging.info("export PORTAINER_API_KEY='your-api-key'")
        logging.info("export MQTT_HOST='your-mqtt-broker'")
        logging.info("\nOptional variables:")
        logging.info("export MQTT_PORT='1883'")
        logging.info("export MQTT_USER='your-username'")
        logging.info("export MQTT_PASS='your-password'")
        logging.info("export UPDATE_INTERVAL='60'")
        logging.info("export DEBUG='true'")
        sys.exit(1)

if __name__ == "__main__":
    # Set up logging based on DEBUG environment variable
    log_level = logging.DEBUG if os.getenv('DEBUG', '').lower() == 'true' else logging.INFO
    logging.basicConfig(
        format='%(asctime)s - %(levelname)s - %(message)s',
        level=log_level,
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Validate environment variables
    validate_environment()
    
    # Configuration
    PORTAINER_URL = os.getenv('PORTAINER_URL')
    PORTAINER_API_KEY = os.getenv('PORTAINER_API_KEY')
    MQTT_HOST = os.getenv('MQTT_HOST')
    MQTT_PORT = int(os.getenv('MQTT_PORT', '1883'))
    MQTT_USER = os.getenv('MQTT_USER')
    MQTT_PASS = os.getenv('MQTT_PASS')
    UPDATE_INTERVAL = int(os.getenv('UPDATE_INTERVAL', '60'))

    try:
        monitor = PortainerMonitor(
            portainer_url=PORTAINER_URL,
            portainer_api_key=PORTAINER_API_KEY,
            mqtt_host=MQTT_HOST,
            mqtt_port=MQTT_PORT,
            mqtt_user=MQTT_USER,
            mqtt_pass=MQTT_PASS
        )
        
        monitor.monitor_loop(UPDATE_INTERVAL)
    except KeyboardInterrupt:
        logging.info("Shutting down...")
        sys.exit(0)
