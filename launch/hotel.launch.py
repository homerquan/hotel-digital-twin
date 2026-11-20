"""Launch the hotel-only Open-RMF graph and Isaac Sim bridge."""

import os
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import ExecuteProcess, IncludeLaunchDescription
from launch_xml.launch_description_sources import XMLLaunchDescriptionSource
from launch_ros.actions import Node


ROOT = Path(__file__).resolve().parents[1]
EVENTS = f"ws://127.0.0.1:{os.environ.get('HOTEL_DASHBOARD_PORT', '8787')}/rmf/events"
FLEETS = ("tinyRobot", "cleanerBotA", "deliveryRobot")


def generate_launch_description():
    sim_time = {"use_sim_time": True}
    processes = [
        ExecuteProcess(cmd=["python3", "-m", "hotel_demo.ros_bridge"], cwd=str(ROOT), output="screen"),
        Node(package="rmf_traffic_ros2", executable="rmf_traffic_schedule",
             name="rmf_traffic_schedule_primary", parameters=[sim_time], output="screen"),
        Node(package="rmf_traffic_ros2", executable="rmf_traffic_blockade", parameters=[sim_time]),
        Node(package="rmf_fleet_adapter", executable="door_supervisor", parameters=[sim_time]),
        Node(package="rmf_fleet_adapter", executable="lift_supervisor", parameters=[sim_time]),
        Node(package="rmf_building_map_tools", executable="building_map_server",
             arguments=[str(ROOT / "assets/upstream/hotel.building.yaml")], parameters=[sim_time]),
        Node(package="rmf_task_ros2", executable="rmf_task_dispatcher", parameters=[sim_time, {
            "server_uri": EVENTS, "use_unique_hex_string_with_task_id": True,
        }], output="screen"),
    ]
    adapter = Path(get_package_share_directory("rmf_demos_fleet_adapter")) / "launch/fleet_adapter.launch.xml"
    for index, fleet in enumerate(FLEETS):
        processes.append(IncludeLaunchDescription(XMLLaunchDescriptionSource(str(adapter)), launch_arguments={
            "config_file": str(ROOT / f"assets/upstream/fleets/{fleet}_config.yaml"),
            "nav_graph_file": str(ROOT / f"assets/nav_graphs/{index}.yaml"),
            "use_sim_time": "true", "server_uri": EVENTS,
        }.items()))
    return LaunchDescription(processes)
