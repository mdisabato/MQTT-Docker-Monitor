import requests
import json
import time
import paho.mqtt.client as mqtt
from typing import Dict, Any
import os
from datetime import datetime, timedelta
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
        
        # Setup logging
        logging.basicConfig(
            format='%(asctime)s - %(levelname)s - %(message)s',
            level=logging.INFO,
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # Store MQTT connection details
        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port
        self.mqtt_user = mqtt_user
        self.mqtt_pass = mqtt_pass
        
        # Discovery prefix for Home Assistant
        self.discovery_prefix = 'homeassistant'
        
        # Endpoint health tracking
        self.endpoint_health = {}
        
        # Initialize MQTT client
        self.setup_mqtt()

    def setup_mqtt(self, max_retries=5, retry_delay=5):
        """Setup MQTT connection with retry logic."""
        retry_count = 0
        while retry_count < max_retries:
            try:
                logging.info(f"Attempting to connect to MQTT broker at {self.mqtt_host}:{self.mqtt_port}")
                self.mqtt_client = mqtt.Client()
                
                if self.mqtt_user and self.mqtt_pass:
                    logging.info("Configuring MQTT authentication")
                    self.mqtt_client.username_pw_set(self.mqtt_user, self.mqtt_pass)
                
                self.mqtt_client.connect(self.mqtt_host, self.mqtt_port)
                self.mqtt_client.loop_start()
                logging.info("Successfully connected to MQTT broker")
                return
            
            except Exception as e:
                retry_count += 1
                if retry_count < max_retries:
                    logging.error(f"Failed to connect to MQTT broker: {str(e)}")
                    logging.info(f"Retrying in {retry_delay} seconds... (Attempt {retry_count+1}/{max_retries})")
                    time.sleep(retry_delay)
                else:
                    logging.critical(f"Failed to connect to MQTT broker after {max_retries} attempts")
                    logging.error(f"Please check your MQTT broker settings:")
                    logging.error(f"Host: {self.mqtt_host}")
                    logging.error(f"Port: {self.mqtt_port}")
                    logging.error(f"User: {self.mqtt_user if self.mqtt_user else 'Not set'}")
                    sys.exit(1)

    def get_endpoints(self) -> list:
        """Get all Portainer endpoints (Docker hosts)."""
        try:
            response = requests.get(f"{self.portainer_url}/api/endpoints", 
                                headers=self.headers)
            response.raise_for_status()
            endpoints = response.json()
            logging.info(f"Successfully retrieved {len(endpoints)} endpoints from Portainer")
            return endpoints
        except requests.exceptions.RequestException as e:
            logging.error(f"Error getting endpoints: {str(e)}")
            return []

    def update_endpoint_health(self, endpoint_id: int, endpoint_name: str, success: bool):
        """Track endpoint health status."""
        now = datetime.now()
        
        if endpoint_id not in self.endpoint_health:
            self.endpoint_health[endpoint_id] = {
                'name': endpoint_name,
                'failures': 0,
                'last_success': now if success else None,
                'first_failure': now if not success else None
            }
        
        if success:
            if self.endpoint_health[endpoint_id]['failures'] > 0:
                logging.info(f"Endpoint {endpoint_name} (ID: {endpoint_id}) has recovered after "
                           f"{self.endpoint_health[endpoint_id]['failures']} failures")
            self.endpoint_health[endpoint_id]['failures'] = 0
            self.endpoint_health[endpoint_id]['last_success'] = now
            self.endpoint_health[endpoint_id]['first_failure'] = None
        else:
            if self.endpoint_health[endpoint_id]['failures'] == 0:
                self.endpoint_health[endpoint_id]['first_failure'] = now
            self.endpoint_health[endpoint_id]['failures'] += 1
            
            # Alert if endpoint has been failing for more than 1 hour
            if (self.endpoint_health[endpoint_id]['first_failure'] and 
                now - self.endpoint_health[endpoint_id]['first_failure'] > timedelta(hours=1)):
                logging.warning(
                    f"Endpoint {endpoint_name} (ID: {endpoint_id}) has been failing for over an hour. "
                    f"Total failures: {self.endpoint_health[endpoint_id]['failures']}. "
                    f"Last success: {self.endpoint_health[endpoint_id]['last_success']}"
                )

    def get_containers(self, endpoint_id: int, endpoint_name: str) -> list:
        """Get all containers for a specific endpoint."""
        try:
            response = requests.get(
                f"{self.portainer_url}/api/endpoints/{endpoint_id}/docker/containers/json?all=true",
                headers=self.headers
            )
            response.raise_for_status()
            containers = response.json()
            self.update_endpoint_health(endpoint_id, endpoint_name, True)
            return containers
        except requests.exceptions.RequestException as e:
            logging.error(f"Error getting containers for endpoint {endpoint_id} ({endpoint_name}): {str(e)}")
            self.update_endpoint_health(endpoint_id, endpoint_name, False)
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
            
            # Calculate CPU percentage
            cpu_delta = stats['cpu_stats']['cpu_usage']['total_usage'] - \
                       stats['precpu_stats']['cpu_usage']['total_usage']
            system_delta = stats['cpu_stats']['system_cpu_usage'] - \
                          stats['precpu_stats']['system_cpu_usage']
            
            cpu_percent = 0.0
            if system_delta > 0:
                cpu_percent = (cpu_delta / system_delta) * 100.0

            # Calculate memory percentage
            mem_percent = (stats['memory_stats']['usage'] / stats['memory_stats']['limit']) * 100.0

            return {
                'cpu_percent': round(cpu_percent, 2),
                'memory_percent': round(mem_percent, 2)
            }
        except Exception as e:
            logging.error(f"Error getting container stats: {str(e)}")
            return {
                'cpu_percent': 0.0,
                'memory_percent': 0.0
            }

    def publish_discovery_config(self, endpoint_name: str, container_name: str):
        """Publish MQTT discovery configuration for Home Assistant."""
        device_info = {
            "identifiers": [f"docker_{endpoint_name}_{container_name}"],
            "name": f"Docker {container_name}",
            "manufacturer": "Docker",
            "model": "Container",
            "via_device": f"docker_{endpoint_name}"
        }

        # Define the single sensor with all attributes
        discovery_topic = f"{self.discovery_prefix}/sensor/{endpoint_name}_{container_name}/config"
        
        payload = {
            "name": f"{container_name}",
            "unique_id": f"docker_{endpoint_name}_{container_name}",
            "state_topic": f"docker/{endpoint_name}/{container_name}/state",
            "icon": "mdi:docker",
            "device": device_info,
            "json_attributes_topic": f"docker/{endpoint_name}/{container_name}/attributes",
            "value_template": "{{ value_json.state }}"
        }

        self.mqtt_client.publish(discovery_topic, json.dumps(payload), retain=True)

    def publish_container_data(self, endpoint_name: str, container_name: str, data: Dict[str, Any]):
        """Publish container data to MQTT."""
        base_topic = f"docker/{endpoint_name}/{container_name}"
        
        # Publish each metric separately
        for key, value in data.items():
            self.mqtt_client.publish(f"{base_topic}/{key}", value)

    def monitor_loop(self, interval: int = 60):
        """Main monitoring loop."""
        logging.info("Starting monitoring loop...")
        while True:
            try:
                endpoints = self.get_endpoints()
                
                for endpoint in endpoints:
                    endpoint_name = endpoint.get('Name', f"Unknown-{endpoint['Id']}")
                    containers = self.get_containers(endpoint['Id'], endpoint_name)
                    
                    if containers:
                        logging.info(f"Successfully retrieved {len(containers)} containers from {endpoint_name}")
                    
                    for container in containers:
                        container_name = container['Names'][0].lstrip('/')
                        
                        # Publish discovery configuration
                        self.publish_discovery_config(endpoint['Name'], container_name)
                        
                        # Build the state message
                        state_data = {
                            "state": container['State'],
                        }

                        # Build the attributes message
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
                                logging.error(f"Error getting stats for {container_name}: {str(e)}")
                        
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
        logging.error("Error: Missing required environment variables:")
        for var in missing_vars:
            logging.error(f"- {var}")
        logging.info("\nPlease set the following environment variables:")
        logging.info("export PORTAINER_URL='http://your-portainer:9000'")
        logging.info("export PORTAINER_API_KEY='your-api-key'")
        logging.info("export MQTT_HOST='your-mqtt-broker'")
        logging.info("Optional variables:")
        logging.info("export MQTT_PORT='1883'")
        logging.info("export MQTT_USER='your-username'")
        logging.info("export MQTT_PASS='your-password'")
        logging.info("export UPDATE_INTERVAL='60'")
        sys.exit(1)

if __name__ == "__main__":
    print(f"Running script from: {__file__}")
    
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
        logging.info("\nShutting down...")
        sys.exit(0)
