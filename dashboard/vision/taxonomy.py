"""Virtual folder taxonomy. Each Node has a path and optional `q` filter
dict; child nodes inherit their parent's filters via AND."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Node:
    path: str
    label: str = ""
    q: dict = field(default_factory=dict)         # attribute key→value filters
    target: int = 6                                # gap-fill target count
    children: list["Node"] = field(default_factory=list)

    def __post_init__(self):
        if not self.label:
            self.label = self.path.rsplit("/", 1)[-1] or self.path


def _people_color(color: str) -> Node:
    return Node(f"/Catalogue/People/Female/{color}", q={"hair_color": _color_value(color)})


def _color_value(label: str) -> str:
    return {"Blonde": "blonde", "Brunette": "brown", "Black": "black",
            "Red": "red", "Gray": "gray"}[label]


TAXONOMY: list[Node] = [
    Node("/Inbound",   q={"source": "inbound"}),
    Node("/Generated", q={"source": "generated"}),
    Node("/Scraped",   q={"source": "scraped"}),

    Node("/Catalogue", children=[
        Node("/Catalogue/People", q={"image_type": "person"}, children=[
            Node("/Catalogue/People/Female", q={"gender": "female"}, children=[
                _people_color("Blonde"),
                _people_color("Brunette"),
                _people_color("Black"),
                _people_color("Red"),
            ]),
            Node("/Catalogue/People/Male", q={"gender": "male"}),
        ]),
        Node("/Catalogue/Faces", q={"source": "crop"}, children=[
            Node("/Catalogue/Faces/Female", q={"gender": "female"}, children=[
                Node("/Catalogue/Faces/Female/Eyes", q={"crops.part": "eye"}),
                Node("/Catalogue/Faces/Female/Lips", q={"crops.part": "lips"}),
                Node("/Catalogue/Faces/Female/Face", q={"crops.part": "face"}),
            ]),
            Node("/Catalogue/Faces/Male", q={"gender": "male"}, children=[
                Node("/Catalogue/Faces/Male/Eyes", q={"crops.part": "eye"}),
                Node("/Catalogue/Faces/Male/Lips", q={"crops.part": "lips"}),
                Node("/Catalogue/Faces/Male/Face", q={"crops.part": "face"}),
            ]),
        ]),
        Node("/Catalogue/Body", q={"source": "crop"}, children=[
            Node("/Catalogue/Body/Hands", q={"crops.part": "hand"}),
            Node("/Catalogue/Body/Feet",  q={"crops.part": "foot"}),
            Node("/Catalogue/Body/Torso", q={"crops.part": "torso"}),
            Node("/Catalogue/Body/Legs",  q={"crops.part": "leg"}),
        ]),
        Node("/Catalogue/Style", children=[
            Node("/Catalogue/Style/Swimwear",   q={"clothing_style": "swimwear"}),
            Node("/Catalogue/Style/Formal",     q={"clothing_style": "formal"}),
            Node("/Catalogue/Style/Sportswear", q={"clothing_style": "sportswear"}),
            Node("/Catalogue/Style/Casual",     q={"clothing_style": "casual"}),
        ]),
        Node("/Catalogue/Scenes", children=[
            Node("/Catalogue/Scenes/Beach",   q={"setting": "beach"}),
            Node("/Catalogue/Scenes/Studio",  q={"setting": "studio"}),
            Node("/Catalogue/Scenes/Outdoor", q={"setting": "outdoor-nature"}),
            Node("/Catalogue/Scenes/Urban",   q={"setting": "outdoor-urban"}),
            Node("/Catalogue/Scenes/Indoor",  q={"setting": "indoor"}),
        ]),
        Node("/Catalogue/Mood", children=[
            Node("/Catalogue/Mood/Smiling", q={"mood": "smiling"}),
            Node("/Catalogue/Mood/Pensive", q={"mood": "pensive"}),
            Node("/Catalogue/Mood/Serious", q={"mood": "serious"}),
            Node("/Catalogue/Mood/Playful", q={"mood": "playful"}),
        ]),
    ]),
]


def _walk(nodes: list[Node]):
    for n in nodes:
        yield n
        yield from _walk(n.children)


def all_nodes() -> list[Node]:
    return list(_walk(TAXONOMY))


def find_node(path: str) -> Optional[Node]:
    for n in all_nodes():
        if n.path == path:
            return n
    return None


def ancestor_filters(path: str) -> dict:
    """Compose all filter dicts from /Catalogue down to `path` (inclusive)."""
    parts = path.split("/")
    out: dict = {}
    for i in range(2, len(parts) + 1):
        sub = "/".join(parts[:i])
        n = find_node(sub)
        if n:
            for k, v in n.q.items():
                out[k] = v
    return out
