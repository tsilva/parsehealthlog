"""Entity registry with deterministic state machine for health timeline management.

This module handles:
- Entity matching (fuzzy name matching for medications/supplements)
- State machine enforcement (valid transitions only)
- Sequential ID assignment (no gaps)
- Relationship validation (no orphan references)
- Output generation (current.yaml, history.csv, entities.json)
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final

import yaml


# State machine: valid transitions for each entity type
# None = entity doesn't exist yet (initial state)
VALID_TRANSITIONS: Final[dict[str, dict[str | None, set[str]]]] = {
    "condition": {
        None: {"diagnosed", "suspected", "noted", "flare"},
        "diagnosed": {"diagnosed", "improved", "worsened", "stable", "resolved", "flare"},
        "suspected": {"suspected", "diagnosed", "improved", "worsened", "stable", "resolved", "flare"},
        "noted": {"noted", "improved", "worsened", "stable", "resolved", "flare"},
        "flare": {"improved", "worsened", "stable", "resolved"},
        "improved": {"improved", "worsened", "stable", "resolved", "flare"},
        "worsened": {"improved", "worsened", "stable", "resolved", "flare"},
        "stable": {"improved", "worsened", "stable", "resolved", "flare"},
        "resolved": {"flare"},  # Only flare can reopen a resolved condition
    },
    "symptom": {
        None: {"noted"},
        "noted": {"improved", "worsened", "stable", "resolved"},
        "improved": {"improved", "worsened", "stable", "resolved"},
        "worsened": {"improved", "worsened", "stable", "resolved"},
        "stable": {"improved", "worsened", "stable", "resolved"},
        "resolved": set(),  # Terminal - symptom is gone
    },
    "medication": {
        None: {"started"},
        "started": {"adjusted", "stopped"},
        "adjusted": {"adjusted", "stopped"},
        "stopped": set(),  # Terminal - restart creates new entity
    },
    "supplement": {
        None: {"started"},
        "started": {"adjusted", "stopped"},
        "adjusted": {"adjusted", "stopped"},
        "stopped": set(),  # Terminal - restart creates new entity
    },
    "experiment": {
        None: {"started"},
        "started": {"update", "ended"},
        "update": {"update", "ended"},
        "ended": set(),  # Terminal
    },
    "provider": {
        None: {"visit"},
        "visit": set(),  # Each visit is a separate entity
    },
    "todo": {
        None: {"added"},
        "added": {"completed"},
        "completed": set(),  # Terminal
    },
}

# Terminal events that close an entity
TERMINAL_EVENTS: Final[set[str]] = {"stopped", "resolved", "ended", "completed"}


@dataclass
class Entity:
    """Represents a tracked health entity (condition, medication, etc.)."""

    id: str
    entity_type: str
    canonical_name: str
    current_state: str
    first_seen: str  # YYYY-MM-DD
    last_updated: str  # YYYY-MM-DD
    related_to: str | None = None  # Entity ID this relates to (e.g., medication for condition)

    def is_terminal(self) -> bool:
        """Check if entity is in a terminal state."""
        return self.current_state in TERMINAL_EVENTS


@dataclass
class HistoryEvent:
    """A single event in the history log."""

    date: str
    entity_id: str
    name: str
    entity_type: str
    event: str
    details: str
    related_entity: str  # Entity ID, not name


@dataclass
class EntityRegistry:
    """Registry of all health entities with state machine enforcement."""

    entities: dict[str, Entity] = field(default_factory=dict)
    history: list[HistoryEvent] = field(default_factory=list)
    _next_id: int = 1
    _name_index: dict[str, list[str]] = field(default_factory=dict)  # normalized_name -> [entity_ids]

    def _normalize_name(self, name: str) -> str:
        """Normalize entity name for matching.

        - Lowercase
        - Remove apostrophes (handles "Gilbert's" vs "Gilberts")
        - Strip possessive 's' before medical terms (handles "Gilberts Syndrome" vs "Gilbert Syndrome")
        - Remove dosage for medications/supplements (e.g., "Vitamin D 5000IU" -> "vitamin d")
        - Strip extra whitespace
        """
        name = name.lower().strip()

        # Remove apostrophe-like characters (handles Gilbert's vs Gilberts)
        name = re.sub(r"[''`']", "", name)

        # Strip possessive 's' before common medical terms
        # "Gilberts Syndrome" -> "Gilbert Syndrome", "Crohns Disease" -> "Crohn Disease"
        name = re.sub(
            r"s\b(?=\s+(syndrome|disease|sign|phenomenon|palsy|tremor))",
            "",
            name,
            flags=re.IGNORECASE,
        )

        # Remove dosage patterns: numbers followed by units
        name = re.sub(r'\s*\d+\s*(mg|mcg|iu|g|ml|units?)\b', '', name, flags=re.IGNORECASE)
        # Remove PRN, daily, etc.
        name = re.sub(r'\s*(prn|daily|weekly|monthly|as needed)\b', '', name, flags=re.IGNORECASE)

        # Collapse multiple spaces
        name = re.sub(r'\s+', ' ', name)

        return name.strip()

    def _generate_id(self) -> str:
        """Generate next sequential entity ID."""
        entity_id = f"ent-{self._next_id:03d}"
        self._next_id += 1
        return entity_id

    def find_entity(
        self,
        name: str,
        entity_type: str,
        *,
        include_terminal: bool = False,
    ) -> Entity | None:
        """Find an existing entity by name and type.

        Args:
            name: Entity name to search for (fuzzy matched)
            entity_type: Type of entity (condition, medication, etc.)
            include_terminal: If False, skip entities in terminal states

        Returns:
            Matching entity or None if not found
        """
        normalized = self._normalize_name(name)
        entity_ids = self._name_index.get(normalized, [])

        for eid in reversed(entity_ids):  # Most recent first
            entity = self.entities.get(eid)
            if entity and entity.entity_type == entity_type:
                if include_terminal or not entity.is_terminal():
                    return entity

        return None

    def find_entity_by_id(self, entity_id: str) -> Entity | None:
        """Find entity by exact ID."""
        return self.entities.get(entity_id)

    def find_condition_by_name(self, condition_name: str) -> Entity | None:
        """Find a condition entity by name for linking purposes."""
        return self.find_entity(condition_name, "condition", include_terminal=True)

    def apply_event(
        self,
        date: str,
        entity_type: str,
        name: str,
        event: str,
        details: str = "",
        for_condition: str | None = None,
    ) -> tuple[Entity, HistoryEvent, list[str]]:
        """Apply an event to an entity, enforcing state machine rules.

        Args:
            date: Event date (YYYY-MM-DD)
            entity_type: Type of entity
            name: Entity name
            event: Event type (started, stopped, improved, etc.)
            details: Optional clinical details
            for_condition: Optional condition name this entity relates to

        Returns:
            Tuple of (entity, history_event, warnings)
            - entity: The affected entity (new or existing)
            - history_event: The recorded event
            - warnings: List of warning messages (empty if all OK)

        Raises:
            ValueError: If the transition is invalid and cannot be handled
        """
        warnings = []

        # Resolve for_condition to entity ID
        related_entity_id = ""
        if for_condition:
            related = self.find_condition_by_name(for_condition)
            if related:
                related_entity_id = related.id
            else:
                warnings.append(
                    f"Condition '{for_condition}' not found for {entity_type} '{name}'"
                )

        # Provider visits and TODOs always create new entities
        if entity_type in ("provider", "todo"):
            entity = self._create_entity(
                entity_type, name, event, date, related_entity_id
            )
            history_event = self._record_event(
                date, entity, event, details, related_entity_id
            )
            return entity, history_event, warnings

        # Find existing entity (non-terminal)
        existing = self.find_entity(name, entity_type)

        if existing:
            # Check if transition is valid
            current_state = existing.current_state
            valid_next = VALID_TRANSITIONS.get(entity_type, {}).get(current_state, set())

            if event in valid_next:
                # Valid transition - update existing entity
                existing.current_state = event
                existing.last_updated = date
                # Update canonical_name if it changed (e.g., dosage adjustment)
                if name != existing.canonical_name:
                    existing.canonical_name = name
                history_event = self._record_event(
                    date, existing, event, details, related_entity_id
                )
                return existing, history_event, warnings
            else:
                # Check if this is an initial event on an already-active entity
                valid_initial = VALID_TRANSITIONS.get(entity_type, {}).get(None, set())
                if event in valid_initial:
                    # Initial event on active entity - don't create duplicate
                    # Examples: "started" on "adjusted", "suspected" on "stable"
                    if name != existing.canonical_name:
                        # Name changed (e.g., dosage) - treat as "adjusted" if available
                        if "adjusted" in valid_next:
                            existing.current_state = "adjusted"
                            existing.canonical_name = name
                            existing.last_updated = date
                            history_event = self._record_event(
                                date, existing, "adjusted", details, related_entity_id
                            )
                            return existing, history_event, warnings
                        else:
                            # Just update the name
                            existing.canonical_name = name
                    # Record event but keep existing entity (no duplicate)
                    existing.last_updated = date
                    history_event = self._record_event(
                        date, existing, event, details, related_entity_id
                    )
                    return existing, history_event, warnings
                else:
                    # Truly invalid - skip with warning
                    warnings.append(
                        f"Skipping invalid transition for '{name}' ({entity_type}): "
                        f"'{current_state}' -> '{event}' is not allowed"
                    )
                    # Record anyway but return existing entity unchanged
                    history_event = self._record_event(
                        date, existing, event, details, related_entity_id
                    )
                    return existing, history_event, warnings
        else:
            # No existing entity - check if this is a valid initial event
            valid_initial = VALID_TRANSITIONS.get(entity_type, {}).get(None, set())

            if event in valid_initial:
                # Valid initial event - create new entity
                entity = self._create_entity(
                    entity_type, name, event, date, related_entity_id
                )
                history_event = self._record_event(
                    date, entity, event, details, related_entity_id
                )
                return entity, history_event, warnings
            else:
                # Invalid initial event - might be referencing past context
                # Find terminal entity and see if we should reuse or create
                terminal = self.find_entity(name, entity_type, include_terminal=True)
                if terminal and terminal.is_terminal():
                    # There was a past entity that ended - check if this continues it
                    if event in VALID_TRANSITIONS.get(entity_type, {}).get(terminal.current_state, set()):
                        # Special case: resolved condition can have flare
                        terminal.current_state = event
                        terminal.last_updated = date
                        history_event = self._record_event(
                            date, terminal, event, details, related_entity_id
                        )
                        return terminal, history_event, warnings

                # No valid path - create with warning
                warnings.append(
                    f"Creating entity '{name}' ({entity_type}) with non-initial event '{event}'. "
                    f"Valid initial events: {valid_initial}"
                )
                entity = self._create_entity(
                    entity_type, name, event, date, related_entity_id
                )
                history_event = self._record_event(
                    date, entity, event, details, related_entity_id
                )
                return entity, history_event, warnings

    def _create_entity(
        self,
        entity_type: str,
        name: str,
        initial_state: str,
        date: str,
        related_to: str = "",
    ) -> Entity:
        """Create a new entity and add to registry."""
        entity_id = self._generate_id()
        entity = Entity(
            id=entity_id,
            entity_type=entity_type,
            canonical_name=name,
            current_state=initial_state,
            first_seen=date,
            last_updated=date,
            related_to=related_to or None,
        )
        self.entities[entity_id] = entity

        # Update name index
        normalized = self._normalize_name(name)
        if normalized not in self._name_index:
            self._name_index[normalized] = []
        self._name_index[normalized].append(entity_id)

        return entity

    def _record_event(
        self,
        date: str,
        entity: Entity,
        event: str,
        details: str,
        related_entity_id: str,
    ) -> HistoryEvent:
        """Record an event in history."""
        history_event = HistoryEvent(
            date=date,
            entity_id=entity.id,
            name=entity.canonical_name,
            entity_type=entity.entity_type,
            event=event,
            details=details,
            related_entity=related_entity_id,
        )
        self.history.append(history_event)
        return history_event

    def process_stack_update(
        self,
        date: str,
        categories: list[str],
        items_mentioned: list[str],
    ) -> list[HistoryEvent]:
        """Process a comprehensive stack update - stop everything not mentioned.

        Args:
            date: Date of the stack update
            categories: Which categories this update covers (e.g., ["supplement"])
            items_mentioned: Names of items that ARE in the current stack

        Returns:
            List of history events for implicit stops
        """
        events = []
        mentioned_normalized = {self._normalize_name(n) for n in items_mentioned}

        for entity in self.entities.values():
            if entity.entity_type not in categories:
                continue
            if entity.is_terminal():
                continue

            # Check if this entity is in the mentioned items
            entity_normalized = self._normalize_name(entity.canonical_name)
            if entity_normalized in mentioned_normalized:
                continue

            # Not mentioned - implicitly stopped
            stop_event = "stopped" if entity.entity_type in ("medication", "supplement") else "ended"
            entity.current_state = stop_event
            entity.last_updated = date

            history_event = HistoryEvent(
                date=date,
                entity_id=entity.id,
                name=entity.canonical_name,
                entity_type=entity.entity_type,
                event=stop_event,
                details="[STACK_UPDATE] Not in current stack",
                related_entity="",
            )
            self.history.append(history_event)
            events.append(history_event)

        return events

    def get_active_entities(self, entity_type: str | None = None) -> list[Entity]:
        """Get all non-terminal entities, optionally filtered by type."""
        result = []
        for entity in self.entities.values():
            if entity.is_terminal():
                continue
            if entity_type and entity.entity_type != entity_type:
                continue
            result.append(entity)
        return result

    def apply_time_based_decay(self, cutoff_years: int = 5) -> list[HistoryEvent]:
        """Auto-resolve conditions not mentioned for N years.

        Args:
            cutoff_years: Number of years without events before auto-resolution

        Returns:
            List of synthetic resolved events added
        """
        from datetime import timedelta

        cutoff_date = datetime.now() - timedelta(days=cutoff_years * 365)
        events_added = []

        for entity in self.entities.values():
            if entity.entity_type != "condition":
                continue
            if entity.is_terminal():
                continue

            # Parse last event date
            last_event_date = datetime.strptime(entity.last_updated, "%Y-%m-%d")

            if last_event_date < cutoff_date:
                # Auto-resolve stale condition
                entity.current_state = "resolved"

                event = HistoryEvent(
                    date=datetime.now().strftime("%Y-%m-%d"),
                    entity_id=entity.id,
                    name=entity.canonical_name,
                    entity_type="condition",
                    event="resolved",
                    details=f"Auto-resolved: no events since {entity.last_updated} ({cutoff_years}+ years)",
                    related_entity="",
                )
                self.history.append(event)
                events_added.append(event)

        return events_added

    # -------------------------------------------------------------------------
    # Output generation
    # -------------------------------------------------------------------------

    def generate_current_yaml(self) -> str:
        """Generate current.yaml showing active state."""
        data = {
            "last_updated": datetime.now().strftime("%Y-%m-%d"),
            "active_conditions": [],
            "active_medications": [],
            "active_supplements": [],
            "active_experiments": [],
            "pending_todos": [],
        }

        for entity in self.entities.values():
            if entity.is_terminal():
                # Check for pending todos (added but not completed)
                if entity.entity_type == "todo" and entity.current_state == "added":
                    data["pending_todos"].append({
                        "id": entity.id,
                        "description": entity.canonical_name,
                        "since": entity.first_seen,
                        "related_to": entity.related_to,
                    })
                continue

            entry = {
                "id": entity.id,
                "name": entity.canonical_name,
                "status": entity.current_state,
                "since": entity.first_seen,
            }
            if entity.related_to:
                entry["related_to"] = entity.related_to

            if entity.entity_type == "condition":
                # Find treatments for this condition
                treatments = [
                    e.id for e in self.entities.values()
                    if e.related_to == entity.id
                    and e.entity_type in ("medication", "supplement")
                    and not e.is_terminal()
                ]
                if treatments:
                    entry["treatments"] = treatments
                data["active_conditions"].append(entry)
            elif entity.entity_type == "medication":
                data["active_medications"].append(entry)
            elif entity.entity_type == "supplement":
                data["active_supplements"].append(entry)
            elif entity.entity_type == "experiment":
                data["active_experiments"].append(entry)
            elif entity.entity_type == "todo":
                data["pending_todos"].append({
                    "id": entity.id,
                    "description": entity.canonical_name,
                    "since": entity.first_seen,
                    "related_to": entity.related_to,
                })

        # Remove empty sections
        data = {k: v for k, v in data.items() if v or k == "last_updated"}

        return yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)

    def generate_history_csv(self) -> str:
        """Generate history.csv - flat event log."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Date", "EntityID", "Name", "Type", "Event", "Details", "RelatedEntity"])

        # Sort history by date
        sorted_history = sorted(self.history, key=lambda e: e.date)

        for event in sorted_history:
            writer.writerow([
                event.date,
                event.entity_id,
                event.name,
                event.entity_type,
                event.event,
                event.details,
                event.related_entity,
            ])

        return output.getvalue()

    def generate_entities_json(self) -> str:
        """Generate entities.json - single source of truth."""
        data = {
            "entities": {},
            "next_id": self._next_id,
        }

        for eid, entity in self.entities.items():
            data["entities"][eid] = {
                "type": entity.entity_type,
                "canonical_name": entity.canonical_name,
                "current_state": entity.current_state,
                "first_seen": entity.first_seen,
                "last_updated": entity.last_updated,
            }
            if entity.related_to:
                data["entities"][eid]["related_to"] = entity.related_to

        return json.dumps(data, indent=2)

    def save_outputs(self, output_dir: Path) -> None:
        """Save all output files to the specified directory."""
        output_dir.mkdir(parents=True, exist_ok=True)

        (output_dir / "current.yaml").write_text(
            self.generate_current_yaml(), encoding="utf-8"
        )
        (output_dir / "history.csv").write_text(
            self.generate_history_csv(), encoding="utf-8"
        )
        (output_dir / "entities.json").write_text(
            self.generate_entities_json(), encoding="utf-8"
        )

    # -------------------------------------------------------------------------
    # Serialization for caching
    # -------------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialize registry to dict for JSON storage."""
        return {
            "next_id": self._next_id,
            "entities": {
                eid: {
                    "id": e.id,
                    "entity_type": e.entity_type,
                    "canonical_name": e.canonical_name,
                    "current_state": e.current_state,
                    "first_seen": e.first_seen,
                    "last_updated": e.last_updated,
                    "related_to": e.related_to,
                }
                for eid, e in self.entities.items()
            },
            "history": [
                {
                    "date": h.date,
                    "entity_id": h.entity_id,
                    "name": h.name,
                    "entity_type": h.entity_type,
                    "event": h.event,
                    "details": h.details,
                    "related_entity": h.related_entity,
                }
                for h in self.history
            ],
            "name_index": dict(self._name_index),
        }

    @classmethod
    def from_dict(cls, data: dict) -> EntityRegistry:
        """Deserialize registry from dict."""
        registry = cls()
        registry._next_id = data.get("next_id", 1)

        for eid, edata in data.get("entities", {}).items():
            entity = Entity(
                id=edata["id"],
                entity_type=edata["entity_type"],
                canonical_name=edata["canonical_name"],
                current_state=edata["current_state"],
                first_seen=edata["first_seen"],
                last_updated=edata["last_updated"],
                related_to=edata.get("related_to"),
            )
            registry.entities[eid] = entity

        for hdata in data.get("history", []):
            event = HistoryEvent(
                date=hdata["date"],
                entity_id=hdata["entity_id"],
                name=hdata["name"],
                entity_type=hdata["entity_type"],
                event=hdata["event"],
                details=hdata["details"],
                related_entity=hdata["related_entity"],
            )
            registry.history.append(event)

        registry._name_index = {k: list(v) for k, v in data.get("name_index", {}).items()}

        return registry
