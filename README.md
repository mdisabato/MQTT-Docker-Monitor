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


The key changes:

    Added MQTT discovery support - sensors will now automatically appear in Home Assistant
    No configuration.yaml changes needed
    Added proper device grouping in Home Assistant
    Added icons and unit measurements
    Added state classes for proper history tracking
