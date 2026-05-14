import json
import logging
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

@dataclass
class Fact:
    id: str
    text: str
    category: str  # e.g., 'nudge', 'motivation', 'break'
    author: Optional[str] = None
    
class FactStore:
    def __init__(self, data_dir: Path):
        self._filepath = data_dir / "facts.json"
        self._facts: List[Fact] = []
        self._load()
        if not self._facts:
            self._seed_default_facts()

    def _load(self):
        if not self._filepath.exists():
            return
        try:
            with open(self._filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self._facts = [Fact(**item) for item in data]
        except Exception as e:
            logger.error(f"Failed to load facts from {self._filepath}: {e}")

    def save(self):
        try:
            self._filepath.parent.mkdir(parents=True, exist_ok=True)
            with open(self._filepath, 'w', encoding='utf-8') as f:
                json.dump([asdict(fact) for fact in self._facts], f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save facts to {self._filepath}: {e}")

    def add_fact(self, fact: Fact):
        self._facts.append(fact)
        self.save()

    def get_by_category(self, category: str) -> List[Fact]:
        return [f for f in self._facts if f.category == category]
        
    def _seed_default_facts(self):
        defaults = [
            Fact(str(uuid.uuid4()), "Take a deep breath. You've got this.", "nudge"),
            Fact(str(uuid.uuid4()), "Focus is a muscle. It's okay if it gets tired. Ready to try again?", "nudge"),
            Fact(str(uuid.uuid4()), "Remember why you started this task.", "motivation"),
            Fact(str(uuid.uuid4()), "If you're feeling stuck, try breaking the task down into a smaller 5-minute chunk.", "motivation"),
            Fact(str(uuid.uuid4()), "Your eyes look tired. Have you tried looking 20 feet away for 20 seconds?", "break")
        ]
        self._facts.extend(defaults)
        self.save()
