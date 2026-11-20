FROM ros:jazzy-ros-base

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-aiohttp \
    ros-jazzy-rmf-building-map-tools \
    ros-jazzy-rmf-demos-fleet-adapter \
    ros-jazzy-rmf-demos-tasks \
    ros-jazzy-rmf-fleet-adapter \
    ros-jazzy-rmf-task-ros2 \
    ros-jazzy-rmf-traffic-ros2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
