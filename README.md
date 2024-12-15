This solution provides several advantages over the Docker Monitor integration:

    Uses container names instead of IDs for consistent tracking
    Leverages Portainer's API for container management
    Publishes data via MQTT for reliable state tracking
    Supports multiple Docker hosts through Portainer endpoints
    Provides container actions (start/stop/restart)

The script publishes data to MQTT topics in this format:

Copy
docker/<endpoint_name>/<container_name>/state
docker/<endpoint_name>/<container_name>/cpu_percent
docker/<endpoint_name>/<container_name>/memory_percent
