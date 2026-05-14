import random
from typing import Optional
from PySide6.QtCore import QObject
from app.data.fact_store import FactStore, Fact

class FactService(QObject):
    def __init__(self, fact_store: FactStore, parent=None):
        super().__init__(parent)
        self._store = fact_store

    def get_random_fact(self, category: str) -> Optional[Fact]:
        facts = self._store.get_by_category(category)
        if not facts:
            return None
        return random.choice(facts)

    def get_nudge(self) -> str:
        fact = self.get_random_fact("nudge")
        return fact.text if fact else "Take a breath. Ready to jump back in?"
        
    def get_motivation(self) -> str:
        fact = self.get_random_fact("motivation")
        return fact.text if fact else "You can do this."
