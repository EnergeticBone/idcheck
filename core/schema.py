from dataclasses import dataclass, field


@dataclass
class Box:
    frame: int
    x1: float
    y1: float
    x2: float
    y2: float
    label: str
    outside: bool = False
    occluded: bool = False
    keyframe: bool = True


@dataclass
class Track:
    id: int
    label: str
    boxes: dict = field(default_factory=dict)   # frame -> Box

    @property
    def start(self):
        return min(self.boxes)

    @property
    def end(self):
        return max(f for f, b in self.boxes.items() if not b.outside)