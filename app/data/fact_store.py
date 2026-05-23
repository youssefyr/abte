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
            # nudge (10)
            Fact(str(uuid.uuid4()), "Take a deep breath. You've got this.", "nudge"),
            Fact(str(uuid.uuid4()), "Focus is a muscle. It's okay if it gets tired. Ready to try again?", "nudge"),
            Fact(str(uuid.uuid4()), "One small step forward is still progress.", "nudge"),
            Fact(str(uuid.uuid4()), "You've handled harder things than this. Keep going.", "nudge"),
            Fact(str(uuid.uuid4()), "Distraction is normal \u2014 noticing it is the first step back.", "nudge"),
            Fact(str(uuid.uuid4()), "What's the single next action on your task?", "nudge"),
            Fact(str(uuid.uuid4()), "Try working for just 5 minutes. Often that's all it takes to restart.", "nudge"),
            Fact(str(uuid.uuid4()), "Close unnecessary tabs. Your future self will thank you.", "nudge"),
            Fact(str(uuid.uuid4()), "Silence notifications for 25 minutes and see what happens.", "nudge"),
            Fact(str(uuid.uuid4()), "What would make this task 10% easier right now?", "nudge"),
            # motivation (10)
            Fact(str(uuid.uuid4()), "Remember why you started this task.", "motivation"),
            Fact(str(uuid.uuid4()), "If you're feeling stuck, try breaking the task down into a smaller 5-minute chunk.", "motivation"),
            Fact(str(uuid.uuid4()), "Consistency beats intensity. Even 20 focused minutes daily compounds over time.", "motivation"),
            Fact(str(uuid.uuid4()), "Your attention is your most valuable resource. Spend it wisely.", "motivation"),
            Fact(str(uuid.uuid4()), "Done is better than perfect \u2014 ship a rough version and iterate.", "motivation"),
            Fact(str(uuid.uuid4()), "The hardest part is usually starting. You've already done that.", "motivation"),
            Fact(str(uuid.uuid4()), "Small wins count. What can you finish in the next 10 minutes?", "motivation"),
            Fact(str(uuid.uuid4()), "Energy follows attention. Direct yours, and momentum builds.", "motivation"),
            Fact(str(uuid.uuid4()), "Each task you complete creates space and clarity for the next one.", "motivation"),
            Fact(str(uuid.uuid4()), "You are capable of more focused work than you feel right now.", "motivation"),
            # break (10)
            Fact(str(uuid.uuid4()), "Your eyes look tired. Have you tried looking 20 feet away for 20 seconds?", "break"),
            Fact(str(uuid.uuid4()), "Stand up, stretch for 60 seconds, then come back refreshed.", "break"),
            Fact(str(uuid.uuid4()), "Drink a glass of water. Hydration improves concentration.", "break"),
            Fact(str(uuid.uuid4()), "Take a 2-minute walk. Movement resets your cognitive state.", "break"),
            Fact(str(uuid.uuid4()), "Step away from the screen briefly. You'll return with clearer thinking.", "break"),
            Fact(str(uuid.uuid4()), "Box breathing: inhale 4s, hold 4s, exhale 4s, hold 4s. Repeat twice.", "break"),
            Fact(str(uuid.uuid4()), "A short break now prevents a much longer recovery later.", "break"),
            Fact(str(uuid.uuid4()), "Look out a window for 20 seconds \u2014 your eye muscles need the rest.", "break"),
            Fact(str(uuid.uuid4()), "Roll your shoulders back, then forward. Release the tension.", "break"),
            Fact(str(uuid.uuid4()), "A 5-minute break every 50 minutes is clinically linked to better recall.", "break"),
        ]
        self._facts.extend(defaults)
        self.save()
