"""Entity registry with simplified active/inactive model for health timeline management.

This module handles:
- Entity matching (fuzzy name matching for medications/supplements)
- Active/inactive state tracking (no complex state machine)
- Sequential ID assignment (no gaps)
- Relationship validation (no orphan references)
- Output generation (current.yaml, history.csv, entities.json)
- Audit template generation for periodic state review
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


# Start events: create or reactivate an entity
START_EVENTS: Final[set[str]] = {
    "diagnosed",
    "suspected",
    "noted",
    "started",
    "added",
    "visit",
}

# Stop events: mark an entity as inactive
STOP_EVENTS: Final[set[str]] = {"resolved", "stopped", "ended", "completed"}

# Valid entity types (from extraction prompt)
VALID_ENTITY_TYPES: Final[set[str]] = {
    "condition",
    "symptom",
    "medication",
    "supplement",
    "experiment",
    "provider",
    "todo",
}

# Valid events per entity type (from extraction prompt Events table)
VALID_EVENTS: Final[dict[str, set[str]]] = {
    "condition": {"diagnosed", "suspected", "noted", "resolved"},
    "symptom": {"noted", "resolved"},
    "medication": {"started", "stopped"},
    "supplement": {"started", "stopped"},
    "experiment": {"started", "ended"},
    "provider": {"visit"},
    "todo": {"added", "completed"},
}

# Schema version for cached extraction files — bump to invalidate old caches
EXTRACTION_SCHEMA_VERSION: Final[int] = 1


def validate_extracted_facts(facts: object) -> list[str]:
    """Validate the structure of extracted facts from LLM output.

    Args:
        facts: Parsed JSON output from the extraction LLM.

    Returns:
        List of error strings. Empty list means valid.
    """
    errors: list[str] = []

    if not isinstance(facts, dict):
        errors.append(f"Expected dict, got {type(facts).__name__}")
        return errors

    if "items" not in facts:
        errors.append("Missing required key 'items'")
        return errors

    items = facts["items"]
    if not isinstance(items, list):
        errors.append(f"'items' must be a list, got {type(items).__name__}")
        return errors

    for i, item in enumerate(items):
        prefix = f"items[{i}]"

        if not isinstance(item, dict):
            errors.append(f"{prefix}: expected dict, got {type(item).__name__}")
            continue

        # Check required fields
        for field in ("type", "name", "event"):
            val = item.get(field)
            if not isinstance(val, str) or not val.strip():
                errors.append(f"{prefix}: '{field}' must be a non-empty string")

        entity_type = item.get("type", "")
        event = item.get("event", "")

        # Validate type
        if (
            isinstance(entity_type, str)
            and entity_type
            and entity_type not in VALID_ENTITY_TYPES
        ):
            errors.append(
                f"{prefix}: invalid type '{entity_type}', "
                f"must be one of {sorted(VALID_ENTITY_TYPES)}"
            )

        # Validate event for type
        if (
            isinstance(entity_type, str)
            and entity_type in VALID_EVENTS
            and isinstance(event, str)
            and event
            and event not in VALID_EVENTS[entity_type]
        ):
            errors.append(
                f"{prefix}: invalid event '{event}' for type '{entity_type}', "
                f"must be one of {sorted(VALID_EVENTS[entity_type])}"
            )

        # Validate optional fields
        for opt_field in ("details", "for_condition"):
            if opt_field in item:
                val = item[opt_field]
                if val is not None and not isinstance(val, str):
                    errors.append(
                        f"{prefix}: '{opt_field}' must be a string or null, "
                        f"got {type(val).__name__}"
                    )

    return errors


@dataclass
class Entity:
    """Represents a tracked health entity (condition, medication, etc.)."""

    id: str
    entity_type: str
    canonical_name: str
    active: bool  # Simple: active or inactive
    origin: str  # How it started: diagnosed, suspected, noted, started, added, visit
    first_seen: str  # YYYY-MM-DD
    last_updated: str  # YYYY-MM-DD
    related_to: str | None = (
        None  # Entity ID this relates to (e.g., medication for condition)
    )


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
    """Registry of all health entities with simplified active/inactive model."""

    entities: dict[str, Entity] = field(default_factory=dict)
    history: list[HistoryEvent] = field(default_factory=list)
    _next_id: int = 1
    _name_index: dict[str, list[str]] = field(
        default_factory=dict
    )  # normalized_name -> [entity_ids]

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
        name = re.sub(
            r"\s*\d+\s*(mg|mcg|iu|g|ml|units?)\b", "", name, flags=re.IGNORECASE
        )
        # Remove PRN, daily, etc.
        name = re.sub(
            r"\s*(prn|daily|weekly|monthly|as needed)\b", "", name, flags=re.IGNORECASE
        )

        # Collapse multiple spaces
        name = re.sub(r"\s+", " ", name)

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
        include_inactive: bool = False,
    ) -> Entity | None:
        """Find an existing entity by name and type.

        Args:
            name: Entity name to search for (fuzzy matched)
            entity_type: Type of entity (condition, medication, etc.)
            include_inactive: If False, skip inactive entities

        Returns:
            Matching entity or None if not found
        """
        normalized = self._normalize_name(name)
        entity_ids = self._name_index.get(normalized, [])

        for eid in reversed(entity_ids):  # Most recent first
            entity = self.entities.get(eid)
            if entity and entity.entity_type == entity_type:
                if include_inactive or entity.active:
                    return entity

        return None

    def find_entity_by_id(self, entity_id: str) -> Entity | None:
        """Find entity by exact ID."""
        return self.entities.get(entity_id)

    def find_condition_by_name(self, condition_name: str) -> Entity | None:
        """Find a condition entity by name for linking purposes."""
        return self.find_entity(condition_name, "condition", include_inactive=True)

    def apply_event(
        self,
        date: str,
        entity_type: str,
        name: str,
        event: str,
        details: str = "",
        for_condition: str | None = None,
    ) -> tuple[Entity, HistoryEvent, list[str]]:
        """Apply an event to an entity using simplified active/inactive model.

        Args:
            date: Event date (YYYY-MM-DD)
            entity_type: Type of entity
            name: Entity name
            event: Event type (started, stopped, diagnosed, resolved, etc.)
            details: Optional clinical details
            for_condition: Optional condition name this entity relates to

        Returns:
            Tuple of (entity, history_event, warnings)
            - entity: The affected entity (new or existing)
            - history_event: The recorded event
            - warnings: List of warning messages (empty if all OK)
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

        # Find existing entity
        existing = self.find_entity(name, entity_type, include_inactive=False)

        if event in START_EVENTS:
            if existing and existing.active:
                # Update existing active entity (details change, name change, etc.)
                existing.last_updated = date
                # Update canonical_name if it changed (e.g., dosage adjustment)
                if name != existing.canonical_name:
                    existing.canonical_name = name
                history_event = self._record_event(
                    date, existing, event, details, related_entity_id
                )
                return existing, history_event, warnings
            else:
                # Check for inactive entity to reactivate
                inactive = self.find_entity(name, entity_type, include_inactive=True)
                if inactive and not inactive.active:
                    inactive.active = True
                    inactive.last_updated = date
                    if name != inactive.canonical_name:
                        inactive.canonical_name = name
                    if related_entity_id:
                        inactive.related_to = related_entity_id
                    history_event = self._record_event(
                        date, inactive, event, details, related_entity_id
                    )
                    return inactive, history_event, warnings
                else:
                    # Truly new entity
                    entity = self._create_entity(
                        entity_type, name, event, date, related_entity_id
                    )
                    history_event = self._record_event(
                        date, entity, event, details, related_entity_id
                    )
                    return entity, history_event, warnings

        elif event in STOP_EVENTS:
            if existing and existing.active:
                existing.active = False
                existing.last_updated = date
                history_event = self._record_event(
                    date, existing, event, details, related_entity_id
                )
                return existing, history_event, warnings
            else:
                # Stop event for entity that doesn't exist or is already inactive
                # Find any matching entity (including inactive) for history
                any_match = self.find_entity(name, entity_type, include_inactive=True)
                if any_match:
                    warnings.append(
                        f"Stop event '{event}' for already inactive entity '{name}' ({entity_type})"
                    )
                    history_event = self._record_event(
                        date, any_match, event, details, related_entity_id
                    )
                    return any_match, history_event, warnings
                else:
                    warnings.append(
                        f"Stop event '{event}' for unknown entity '{name}' ({entity_type})"
                    )
                    # Create entity in inactive state for history completeness
                    entity = self._create_entity(
                        entity_type, name, event, date, related_entity_id, active=False
                    )
                    history_event = self._record_event(
                        date, entity, event, details, related_entity_id
                    )
                    return entity, history_event, warnings

        else:
            # Unknown event - treat as update detail on existing or create new
            warnings.append(
                f"Unknown event '{event}' for '{name}' ({entity_type}), treating as detail update"
            )
            if existing and existing.active:
                existing.last_updated = date
                history_event = self._record_event(
                    date, existing, event, details, related_entity_id
                )
                return existing, history_event, warnings
            else:
                # Create new entity with this event as origin
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
        origin: str,
        date: str,
        related_to: str = "",
        active: bool = True,
    ) -> Entity:
        """Create a new entity and add to registry."""
        entity_id = self._generate_id()
        entity = Entity(
            id=entity_id,
            entity_type=entity_type,
            canonical_name=name,
            active=active,
            origin=origin,
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

    def get_active_entities(self, entity_type: str | None = None) -> list[Entity]:
        """Get all active entities, optionally filtered by type."""
        result = []
        for entity in self.entities.values():
            if not entity.active:
                continue
            if entity_type and entity.entity_type != entity_type:
                continue
            result.append(entity)
        return result

    def reset_active_entities(
        self, date: str, categories: set[str] | None = None
    ) -> list[HistoryEvent]:
        """Mark all active entities as inactive (state reset).

        Used when an entry contains a RESET_STATE marker, indicating that the
        entry represents a complete snapshot of current state. All previously
        active entities not mentioned in the entry should be considered stopped.

        Args:
            date: Date of the reset event
            categories: Optional set of entity types to reset.
                       If None, resets: condition, symptom, medication, supplement, experiment

        Returns:
            List of history events for the stopped entities
        """
        if categories is None:
            categories = {
                "condition",
                "symptom",
                "medication",
                "supplement",
                "experiment",
            }

        stop_events = {
            "condition": "resolved",
            "symptom": "resolved",
            "medication": "stopped",
            "supplement": "stopped",
            "experiment": "ended",
        }

        events = []
        for entity in self.entities.values():
            if entity.active and entity.entity_type in categories:
                entity.active = False
                entity.last_updated = date

                event = HistoryEvent(
                    date=date,
                    entity_id=entity.id,
                    name=entity.canonical_name,
                    entity_type=entity.entity_type,
                    event=stop_events.get(entity.entity_type, "stopped"),
                    details="State reset",
                    related_entity="",
                )
                self.history.append(event)
                events.append(event)

        return events

    # -------------------------------------------------------------------------
    # Output generation
    # -------------------------------------------------------------------------

    def generate_current_yaml(self) -> str:
        """Generate current.yaml showing active state."""
        data = {
            "last_updated": datetime.now().strftime("%Y-%m-%d"),
            "active_conditions": [],
            "active_treatments": [],
        }

        for entity in self.entities.values():
            if not entity.active:
                continue

            entry = {
                "id": entity.id,
                "name": entity.canonical_name,
                "origin": entity.origin,
                "since": entity.first_seen,
            }
            if entity.related_to:
                entry["related_to"] = entity.related_to

            if entity.entity_type == "condition":
                # Find treatments for this condition
                treatments = [
                    e.id
                    for e in self.entities.values()
                    if e.related_to == entity.id
                    and e.entity_type in ("medication", "supplement")
                    and e.active
                ]
                if treatments:
                    entry["treatments"] = treatments
                data["active_conditions"].append(entry)
            elif entity.entity_type in ("medication", "supplement"):
                data["active_treatments"].append(entry)

        # Remove empty sections
        data = {k: v for k, v in data.items() if v or k == "last_updated"}

        return yaml.dump(
            data, default_flow_style=False, sort_keys=False, allow_unicode=True
        )

    def generate_history_csv(self) -> str:
        """Generate history.csv - flat event log."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            ["Date", "EntityID", "Name", "Type", "Event", "Details", "RelatedEntity"]
        )

        # Sort history by date
        sorted_history = sorted(self.history, key=lambda e: e.date)

        for event in sorted_history:
            writer.writerow(
                [
                    event.date,
                    event.entity_id,
                    event.name,
                    event.entity_type,
                    event.event,
                    (event.details or "").replace("\n", " ").replace("\r", ""),
                    event.related_entity,
                ]
            )

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
                "active": entity.active,
                "origin": entity.origin,
                "first_seen": entity.first_seen,
                "last_updated": entity.last_updated,
            }
            if entity.related_to:
                data["entities"][eid]["related_to"] = entity.related_to

        return json.dumps(data, indent=2)

    def generate_audit_template(self) -> str:
        """Generate audit template for reviewing active entities.

        Returns markdown content with all active entities grouped by type,
        pre-filled with appropriate stop events for the user to edit.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        lines = [
            f"### {today} - State Audit",
            "",
            "<!--",
            "Instructions:",
            "1. DELETE entries for items that are still active",
            "2. KEEP entries for items you want to stop/resolve",
            "3. Add this file content to your health log",
            "4. Re-run processing to apply changes",
            "-->",
            "",
        ]

        # Group active entities by type
        by_type: dict[str, list[Entity]] = {}
        for entity in self.entities.values():
            if not entity.active:
                continue
            if entity.entity_type not in by_type:
                by_type[entity.entity_type] = []
            by_type[entity.entity_type].append(entity)

        # Define stop events and section order
        stop_events = {
            "condition": "resolved",
            "symptom": "resolved",
            "medication": "stopped",
            "supplement": "stopped",
            "experiment": "ended",
            "todo": "completed",
        }

        section_order = [
            "condition",
            "symptom",
            "medication",
            "supplement",
            "experiment",
            "todo",
        ]

        for entity_type in section_order:
            entities = by_type.get(entity_type, [])
            if not entities:
                continue

            # Section header
            section_name = entity_type.title() + (
                "s" if not entity_type.endswith("s") else "es"
            )
            lines.append(f"## {section_name}")
            lines.append("")

            # Sort by last_updated (oldest first - most likely to be stale)
            entities_sorted = sorted(entities, key=lambda e: e.last_updated)

            for entity in entities_sorted:
                stop_event = stop_events.get(entity_type, "stopped")
                lines.append(f"- {entity.canonical_name}: {stop_event}")

                # Add metadata as comment
                metadata_parts = [
                    f"Last: {entity.last_updated}",
                    f"Origin: {entity.origin}",
                ]
                if entity.related_to:
                    related = self.find_entity_by_id(entity.related_to)
                    if related:
                        metadata_parts.append(f"For: {related.canonical_name}")
                lines.append(f"  <!-- {' | '.join(metadata_parts)} -->")

            lines.append("")

        if len(by_type) == 0:
            lines.append("No active entities found.")
            lines.append("")

        return "\n".join(lines)

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
                    "active": e.active,
                    "origin": e.origin,
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
        """Deserialize registry from dict.

        Handles migration from old format (current_state) to new format (active + origin).
        """
        registry = cls()
        registry._next_id = data.get("next_id", 1)

        # Terminal states from old format
        old_terminal_states = {"stopped", "resolved", "ended", "completed"}

        for eid, edata in data.get("entities", {}).items():
            # Migration: convert old current_state to active + origin
            if "current_state" in edata and "active" not in edata:
                current_state = edata["current_state"]
                active = current_state not in old_terminal_states
                # Origin is the first event - we don't have it, so use current_state as best guess
                origin = current_state
            else:
                active = edata.get("active", True)
                origin = edata.get("origin", "noted")

            entity = Entity(
                id=edata["id"],
                entity_type=edata["entity_type"],
                canonical_name=edata["canonical_name"],
                active=active,
                origin=origin,
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

        registry._name_index = {
            k: list(v) for k, v in data.get("name_index", {}).items()
        }

        return registry
