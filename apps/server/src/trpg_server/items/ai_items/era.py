"""Validated campaign-era context for AI-assisted item recipes."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping


class EraProfileError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EraTechnology:
    technology_id: str
    name: str
    status: str
    source_status: str
    basis: str

    def to_mapping(self) -> dict[str, str]:
        return {
            "technologyId": self.technology_id,
            "name": self.name,
            "status": self.status,
            "sourceStatus": self.source_status,
            "basis": self.basis,
        }


@dataclass(frozen=True, slots=True)
class EraTechnologyProfile:
    profile_id: str
    campaign_id: str
    analogue_period: str
    source_refs: tuple[Mapping[str, str], ...]
    technologies: tuple[EraTechnology, ...]
    assessment_rules: tuple[str, ...]

    @classmethod
    def load(cls, path: str | Path) -> "EraTechnologyProfile":
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_document(document)

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "EraTechnologyProfile":
        expected = {
            "schemaVersion",
            "profileId",
            "campaignId",
            "analoguePeriod",
            "sourceStatus",
            "sourceRefs",
            "technologies",
            "assessmentRules",
        }
        if not isinstance(document, Mapping) or set(document) != expected:
            raise EraProfileError("era profile fields are invalid")
        if document["schemaVersion"] != 1:
            raise EraProfileError("unsupported era profile schemaVersion")
        if document["sourceStatus"] != "canon":
            raise EraProfileError("era profile must be rooted in canon sources")
        profile_id = _text(document["profileId"], "profileId", 100)
        campaign_id = _text(document["campaignId"], "campaignId", 100)
        analogue = _text(document["analoguePeriod"], "analoguePeriod", 200)
        refs_raw = document["sourceRefs"]
        if not isinstance(refs_raw, list) or not refs_raw:
            raise EraProfileError("sourceRefs must be a non-empty array")
        refs: list[Mapping[str, str]] = []
        for index, raw in enumerate(refs_raw):
            if not isinstance(raw, Mapping) or set(raw) != {"path", "section", "lines"}:
                raise EraProfileError(f"sourceRefs[{index}] fields are invalid")
            refs.append(
                {
                    "path": _text(raw["path"], f"sourceRefs[{index}].path", 300),
                    "section": _text(raw["section"], f"sourceRefs[{index}].section", 120),
                    "lines": _text(raw["lines"], f"sourceRefs[{index}].lines", 40),
                }
            )
        technologies_raw = document["technologies"]
        if not isinstance(technologies_raw, list) or not technologies_raw:
            raise EraProfileError("technologies must be a non-empty array")
        technologies: list[EraTechnology] = []
        for index, raw in enumerate(technologies_raw):
            if not isinstance(raw, Mapping) or set(raw) != {
                "technologyId",
                "name",
                "status",
                "sourceStatus",
                "basis",
            }:
                raise EraProfileError(f"technologies[{index}] fields are invalid")
            status = raw["status"]
            if status not in {"mature", "common", "emerging", "limited", "unavailable"}:
                raise EraProfileError(f"technologies[{index}].status is invalid")
            source_status = raw["sourceStatus"]
            if source_status not in {"canon", "inferred_constraint"}:
                raise EraProfileError(
                    f"technologies[{index}].sourceStatus is invalid"
                )
            technologies.append(
                EraTechnology(
                    technology_id=_text(
                        raw["technologyId"],
                        f"technologies[{index}].technologyId",
                        100,
                    ),
                    name=_text(raw["name"], f"technologies[{index}].name", 100),
                    status=str(status),
                    source_status=str(source_status),
                    basis=_text(raw["basis"], f"technologies[{index}].basis", 300),
                )
            )
        ids = [value.technology_id for value in technologies]
        if len(ids) != len(set(ids)):
            raise EraProfileError("technologyId values must be unique")
        rules = _string_tuple(document["assessmentRules"], "assessmentRules", 20)
        if not rules:
            raise EraProfileError("assessmentRules cannot be empty")
        return cls(
            profile_id=profile_id,
            campaign_id=campaign_id,
            analogue_period=analogue,
            source_refs=tuple(refs),
            technologies=tuple(technologies),
            assessment_rules=rules,
        )

    @property
    def technology_ids(self) -> frozenset[str]:
        return frozenset(value.technology_id for value in self.technologies)

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(
            self.to_document(), ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("ascii")
        return f"sha256:{sha256(encoded).hexdigest()}"

    def to_prompt_mapping(self) -> dict[str, Any]:
        return {
            "profileId": self.profile_id,
            "analoguePeriod": self.analogue_period,
            "technologies": [value.to_mapping() for value in self.technologies],
            "assessmentRules": list(self.assessment_rules),
        }

    def to_document(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "profileId": self.profile_id,
            "campaignId": self.campaign_id,
            "analoguePeriod": self.analogue_period,
            "sourceStatus": "canon",
            "sourceRefs": [dict(value) for value in self.source_refs],
            "technologies": [value.to_mapping() for value in self.technologies],
            "assessmentRules": list(self.assessment_rules),
        }


def _text(value: object, path: str, maximum: int) -> str:
    if type(value) is not str or not value.strip() or len(value.strip()) > maximum:
        raise EraProfileError(f"{path} must be a non-empty string")
    return value.strip()


def _string_tuple(value: object, path: str, maximum: int) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise EraProfileError(f"{path} must be an array")
    result = tuple(_text(item, path, 300) for item in value)
    if len(result) != len(set(result)):
        raise EraProfileError(f"{path} must not contain duplicates")
    return result


__all__ = [
    "EraProfileError",
    "EraTechnology",
    "EraTechnologyProfile",
]
