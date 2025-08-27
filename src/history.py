import json
from pathlib import Path
from typing import Dict, List, Tuple

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
HISTORY_PATH = DATA_DIR / "history.json"


def _load_all() -> Dict[str, List[Dict[str, str]]]:
    if not HISTORY_PATH.exists():
        return {}
    try:
        with HISTORY_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_all(data: Dict[str, List[Dict[str, str]]]) -> None:
    with HISTORY_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_history(buyer_name: str) -> List[Tuple[str, str]]:
    data = _load_all()
    raw = data.get(buyer_name, [])
    return [(d.get("role", ""), d.get("text", "")) for d in raw]


def append_history(buyer_name: str, pairs: List[Tuple[str, str]], max_depth: int = 20) -> None:
    data = _load_all()
    items = data.get(buyer_name, [])
    for role, text in pairs:
        entry = {"role": role, "text": text}
        if entry not in items:
            items.append(entry)
    data[buyer_name] = items[-max_depth:]
    _save_all(data)
