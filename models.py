"""
Models for the Home Simulator.

This module defines the core data classes to replace tuple-based data structures.
"""

from typing import Optional, List
from dataclasses import dataclass, field


@dataclass
class Point:
    """Represents a reference point on the map."""
    name: str
    x: int
    y: int

    def tuple(self) -> tuple:
        """Return tuple representation for backward compatibility."""
        return (self.name, self.x, self.y)

    @staticmethod
    def from_tuple(t: tuple) -> 'Point':
        """Create Point from tuple (name, x, y)."""
        return Point(name=t[0], x=t[1], y=t[2])


@dataclass
class Sensor:
    """Represents a sensor in the simulation."""
    name: str
    x: int
    y: int
    type: str  # "PIR", "Temperature", "Switch", "Smart Meter", "Weight"
    min_val: float
    max_val: float
    step: float
    state: float
    direction: Optional[int] = None  # for PIR/directional sensors
    consumption: Optional[float] = None  # for Smart Meter
    associated_device: Optional[str] = None  # device linked to this sensor

    def tuple(self) -> tuple:
        """Return tuple representation for backward compatibility."""
        return (
            self.name, self.x, self.y, self.type,
            self.min_val, self.max_val, self.step, self.state,
            self.direction, self.consumption, self.associated_device
        )

    @staticmethod
    def from_tuple(t: tuple) -> 'Sensor':
        """Create Sensor from tuple (name, x, y, type, min_val, max_val, step, state, direction, consumption, associated_device)."""
        return Sensor(
            name=t[0], x=t[1], y=t[2], type=t[3],
            min_val=t[4], max_val=t[5], step=t[6], state=t[7],
            direction=t[8] if len(t) > 8 else None,
            consumption=t[9] if len(t) > 9 else None,
            associated_device=t[10] if len(t) > 10 else None
        )


@dataclass
class Device:
    """Represents a device in the simulation."""
    name: str
    x: int
    y: int
    type: str  # "Fridge", "Washing_Machine", "Oven", "Coffee_Machine", "Computer", "Dishwasher"
    power: float  # Watt
    state: int  # 0 = OFF, 1 = ON
    min_consumption: float
    max_consumption: float
    current_consumption: float = 0.0
    consumption_direction: int = 1  # 1 = increasing, -1 = decreasing

    def tuple(self) -> tuple:
        """Return tuple representation for backward compatibility."""
        return (
            self.name, self.x, self.y, self.type, self.power,
            self.state, self.min_consumption, self.max_consumption,
            self.current_consumption, self.consumption_direction
        )

    @staticmethod
    def from_tuple(t: tuple) -> 'Device':
        """Create Device from tuple (name, x, y, type, power, state, min_consumption, max_consumption, current_consumption, direction)."""
        return Device(
            name=t[0], x=t[1], y=t[2], type=t[3], power=t[4],
            state=t[5], min_consumption=t[6], max_consumption=t[7],
            current_consumption=t[8] if len(t) > 8 else 0.0,
            consumption_direction=t[9] if len(t) > 9 else 1
        )

    def is_on(self) -> bool:
        """Check if device is ON."""
        return self.state == 1

    def toggle(self):
        """Toggle device state."""
        self.state = 1 - self.state


@dataclass
class Door:
    """Represents a door between two points."""
    x1: int
    y1: int
    x2: int
    y2: int
    state: str = "open"  # "open" or "close"

    def tuple(self) -> tuple:
        """Return tuple representation for backward compatibility."""
        return (self.x1, self.y1, self.x2, self.y2, self.state)

    @staticmethod
    def from_tuple(t: tuple) -> 'Door':
        """Create Door from tuple (x1, y1, x2, y2, state)."""
        return Door(x1=t[0], y1=t[1], x2=t[2], y2=t[3], state=t[4] if len(t) > 4 else "open")

    def is_closed(self) -> bool:
        """Check if door is closed."""
        return self.state == "close"

    def is_open(self) -> bool:
        """Check if door is open."""
        return self.state == "open"


@dataclass
class Wall:
    """Represents a wall between two points."""
    x1: int
    y1: int
    x2: int
    y2: int
    doors: List[Door] = field(default_factory=list)

    def tuple(self) -> tuple:
        """Return tuple representation for backward compatibility."""
        return (self.x1, self.y1, self.x2, self.y2)

    @staticmethod
    def from_tuple(t: tuple) -> 'Wall':
        """Create Wall from tuple (x1, y1, x2, y2)."""
        return Wall(x1=t[0], y1=t[1], x2=t[2], y2=t[3])

    def add_door(self, door: Door):
        """Add a door to this wall."""
        self.doors.append(door)


@dataclass
class SimulationState:
    """Container for all simulation objects.
    
    This replaces the scattered global variables (sensors, devices, points, etc.)
    """
    sensors: List[Sensor] = field(default_factory=list)
    devices: List[Device] = field(default_factory=list)
    points: List[Point] = field(default_factory=list)
    walls: List[Wall] = field(default_factory=list)
    doors: List[Door] = field(default_factory=list)

    def get_sensor(self, name: str) -> Optional[Sensor]:
        """Get sensor by name."""
        for s in self.sensors:
            if s.name == name:
                return s
        return None

    def get_device(self, name: str) -> Optional[Device]:
        """Get device by name."""
        for d in self.devices:
            if d.name == name:
                return d
        return None

    def get_point(self, name: str) -> Optional[Point]:
        """Get point by name."""
        for p in self.points:
            if p.name == name:
                return p
        return None

    def sensor_names(self) -> List[str]:
        """Get all sensor names."""
        return [s.name for s in self.sensors]

    def device_names(self) -> List[str]:
        """Get all device names."""
        return [d.name for d in self.devices]

    def point_names(self) -> List[str]:
        """Get all point names."""
        return [p.name for p in self.points]
