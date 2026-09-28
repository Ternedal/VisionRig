"""HTTP surface for the standalone VisionRig service."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Callable, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from . import __version__
from .capabilities import probe_capabilities
from .contracts import PerceptionEvent, SourceDescriptor, WorldSnapshot
from .http_io import read_bounded_body
from .journal import EventBatch
from .modelrig_bridge import ModelRigPerceptionPublisher
from .pipeline import Frame, PassthroughStage, PerceptionPipeline
from .runtime import VisionRuntime
from .sensor_events import SensorChangeBatch, SensorChangeJournal
from .sensor_ingress import (
    PACKET_TARGET_ATTENTION_STREAK_THRESHOLD,
    SensorDecodeError,
    SensorFrameReceipt,
    SensorHeartbeat,
    SensorHeartbeatReceipt,
    SensorIngress,
    SensorIngressBusy,
    SensorMediaTypeError,
    SensorPayloadTooLarge,
    SensorSequenceError,
)
from .sensor_packet import (
    SENSOR_PACKET_MEDIA_TYPE,
    SENSOR_PACKET_PAYLOAD_CRITICAL_UTILIZATION,
    SENSOR_PACKET_PAYLOAD_WARNING_UTILIZATION,
    SensorPacketError,
    inspect_sensor_packet,
)
from .sensor_registry import (
    SensorDesiredState,
    SensorIdentityConflict,
    SensorMetadataPatch,
    SensorRegistry,
    SensorStateRevisionConflict,
)


class IngestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: SourceDescriptor
    frame_sequence: int = Field(ge=0)
    payload: dict
    dropped_frames: int = Field(default=0, ge=0)


def create_app(
    pipeline: PerceptionPipeline | None = None,
    *,
    max_sensor_frame_bytes: int = 8 * 1024 * 1024,
    sensor_stale_after_seconds: float = 15.0,
    sensor_offline_after_seconds: float = 60.0,
    packet_target_flap_window_seconds: float = 120.0,
    modelrig_publisher: ModelRigPerceptionPublisher | None = None,
    sensor_registry: SensorRegistry | None = None,
    sensor_change_journal: SensorChangeJournal | None = None,
    sensor_clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    if packet_target_flap_window_seconds <= 0:
        raise ValueError("packet_target_flap_window_seconds must be > 0")
    packet_target_flap_window_seconds = float(packet_target_flap_window_seconds)
    app = FastAPI(title="VisionRig", version=__version__)
    selected_pipeline = pipeline or PerceptionPipeline((PassthroughStage(),))
    runtime = VisionRuntime(
        selected_pipeline,
        event_sinks=((modelrig_publisher,) if modelrig_publisher is not None else ()),
    )
    effective_sensor_clock = sensor_clock or (lambda: datetime.now(timezone.utc))
    sensor_ingress_kwargs: dict[str, object] = {
        "max_payload_bytes": max_sensor_frame_bytes,
        "stale_after_seconds": sensor_stale_after_seconds,
        "offline_after_seconds": sensor_offline_after_seconds,
        "clock": effective_sensor_clock,
    }
    sensor_ingress = SensorIngress(
        runtime,
        **sensor_ingress_kwargs,
    )
    registry = sensor_registry or SensorRegistry()
    sensor_changes = sensor_change_journal or SensorChangeJournal()
    producer_readiness_transition_state: dict[str, object] = {
        "current": None,
        "previous": None,
        "changed_utc": None,
    }

    def raise_state_revision_conflict(
        exc: SensorStateRevisionConflict,
    ) -> None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "sensor_state_revision_conflict",
                "expected": exc.expected,
                "current": exc.current,
            },
        ) from exc

    def append_sensor_change(
        *,
        kind,
        source_id: str,
        payload: dict[str, object] | None = None,
    ):
        return sensor_changes.append(
            kind=kind,
            source_id=source_id,
            payload=payload,
            state_revision=registry.state_revision,
        )

    def runtime_source_for(source_id: str):
        return next(
            (
                source
                for source in sensor_ingress.stats().sources
                if source.source_id == source_id
            ),
            None,
        )

    def emit_registration_changes(
        source_id: str,
        *,
        was_registered: bool,
        previous_discovery,
    ) -> None:
        current_discovery = registry.get_discovery(source_id)
        if not was_registered and registry.contains(source_id):
            append_sensor_change(
                kind="registered",
                source_id=source_id,
                payload={
                    "discovery": (
                        asdict(current_discovery)
                        if current_discovery is not None
                        else None
                    )
                },
            )
        elif (
            previous_discovery is not None
            and current_discovery is not None
            and (
                previous_discovery.source_type != current_discovery.source_type
                or previous_discovery.device != current_discovery.device
                or previous_discovery.capabilities != current_discovery.capabilities
            )
        ):
            append_sensor_change(
                kind="discovery_changed",
                source_id=source_id,
                payload={
                    "discovery": asdict(current_discovery),
                },
            )

    def emit_runtime_change(source_id: str, previous_runtime) -> None:
        current_runtime = runtime_source_for(source_id)
        if current_runtime is None:
            return
        if (
            previous_runtime is None
            or previous_runtime.capture_active != current_runtime.capture_active
            or previous_runtime.applied_revision != current_runtime.applied_revision
            or (
                previous_runtime.negotiated_max_payload_bytes
                != current_runtime.negotiated_max_payload_bytes
            )
            or (
                previous_runtime.negotiated_packet_compression
                != current_runtime.negotiated_packet_compression
            )
            or (
                previous_runtime.negotiated_packet_target_utilization
                != current_runtime.negotiated_packet_target_utilization
            )
        ):
            append_sensor_change(
                kind="runtime_changed",
                source_id=source_id,
                payload={
                    "capture_active": current_runtime.capture_active,
                    "applied_revision": current_runtime.applied_revision,
                    "negotiated_max_payload_bytes": (
                        current_runtime.negotiated_max_payload_bytes
                    ),
                    "negotiated_packet_compression": (
                        current_runtime.negotiated_packet_compression
                    ),
                    "negotiated_packet_target_utilization": (
                        current_runtime.negotiated_packet_target_utilization
                    ),
                    "presence": current_runtime.presence,
                },
            )

    def sensor_change_consistency_payload() -> dict[str, object]:
        state_revision = registry.state_revision
        journal_revision = sensor_changes.state_revision_high_water
        if state_revision == journal_revision:
            status = "synced"
        elif state_revision > journal_revision:
            status = "registry_ahead"
        else:
            status = "journal_ahead"
        return {
            "schema": "visionrig/sensor-change-consistency/v1",
            "status": status,
            "state_revision": state_revision,
            "journal_state_revision": journal_revision,
        }

    def packet_target_overshoot(runtime_source) -> tuple[float | None, float | None]:
        if runtime_source is None:
            return None, None
        target = runtime_source.negotiated_packet_target_utilization
        observed = runtime_source.observed_packet_utilization
        if target is None or observed is None:
            return None, None
        delta = max(0.0, observed - target)
        ratio = delta / target
        return round(delta, 6), round(ratio, 6)

    def packet_target_measurement_status(runtime_source) -> str:
        if runtime_source is None:
            return "unavailable"
        target = runtime_source.negotiated_packet_target_utilization
        observed = runtime_source.observed_packet_utilization
        if target is None:
            return "unavailable"
        if observed is None:
            return "target_only"
        return "complete"

    def producer_source_readiness_payload(runtime_source) -> dict[str, object]:
        if runtime_source is None:
            return {
                "runtime_available": False,
                "heartbeat_v6": None,
                "heartbeat_upgrade_required": None,
                "heartbeat_schema_id": None,
                "required_heartbeat_schema_id": None,
                "heartbeat_versions_behind": None,
                "heartbeat_upgrade_stage": None,
                "packet_measurement_complete": None,
                "packet_measurement": None,
            }
        heartbeat_schema_id = runtime_source.heartbeat_schema_id
        heartbeat_version = (
            heartbeat_schema_id.rsplit("/", 1)[-1]
            if heartbeat_schema_id is not None
            else None
        )
        heartbeat_versions_behind = (
            6 - int(heartbeat_version[1:])
            if heartbeat_version in {"v2", "v3", "v4", "v5"}
            else None
        )
        heartbeat_v6 = heartbeat_schema_id == "visionrig/sensor-heartbeat/v6"
        packet_measurement = packet_target_measurement_status(runtime_source)
        return {
            "runtime_available": True,
            "heartbeat_v6": heartbeat_v6,
            "heartbeat_upgrade_required": not heartbeat_v6,
            "heartbeat_schema_id": heartbeat_schema_id,
            "required_heartbeat_schema_id": "visionrig/sensor-heartbeat/v6",
            "heartbeat_versions_behind": heartbeat_versions_behind,
            "heartbeat_upgrade_stage": (
                None
                if heartbeat_v6
                else (
                    "contract_upgrade"
                    if heartbeat_versions_behind is not None
                    else "establish_heartbeat"
                )
            ),
            "packet_measurement_complete": packet_measurement == "complete",
            "packet_measurement": packet_measurement,
        }

    def packet_target_compliance_status(runtime_source) -> str:
        if runtime_source is None:
            return "unknown"
        target = runtime_source.negotiated_packet_target_utilization
        observed = runtime_source.observed_packet_utilization
        if target is None or observed is None:
            return "unknown"
        return "within_target" if observed <= target else "above_target"

    def packet_target_pressure_status(runtime_source, compliance_status: str) -> str:
        if compliance_status == "unknown":
            return "unknown"
        if compliance_status == "within_target":
            return "clear"
        if (
            runtime_source is not None
            and runtime_source.packet_target_above_streak
            >= PACKET_TARGET_ATTENTION_STREAK_THRESHOLD
        ):
            return "sustained"
        return "transient"

    def packet_target_stability_status(runtime_source) -> str:
        if runtime_source is None:
            return "unknown"
        if (
            runtime_source.negotiated_packet_target_utilization is None
            or runtime_source.observed_packet_utilization is None
        ):
            return "unknown"
        if runtime_source.packet_target_recurrence_count == 0:
            return "stable"
        if (
            runtime_source.packet_target_last_recurrence_seconds is not None
            and runtime_source.packet_target_last_recurrence_seconds
            <= packet_target_flap_window_seconds
        ):
            return "flapping"
        return "recurring"

    def readiness_projection(runtime_sources) -> dict[str, object]:
        runtime_sources = tuple(runtime_sources)
        runtime_source_count = len(runtime_sources)
        heartbeat_v6_sources = sum(
            source.heartbeat_schema_id == "visionrig/sensor-heartbeat/v6"
            for source in runtime_sources
        )
        packet_measurement_gap_sources = sum(
            packet_target_measurement_status(source) != "complete"
            for source in runtime_sources
        )
        packet_measurement_complete_sources = (
            runtime_source_count - packet_measurement_gap_sources
        )
        return {
            "runtime_sources": runtime_source_count,
            "heartbeat_v6_sources": heartbeat_v6_sources,
            "heartbeat_upgrade_required": (
                runtime_source_count - heartbeat_v6_sources
            ),
            "heartbeat_v6_ratio": (
                round(heartbeat_v6_sources / runtime_source_count, 6)
                if runtime_source_count
                else None
            ),
            "packet_measurement_complete_sources": (
                packet_measurement_complete_sources
            ),
            "packet_measurement_gap_sources": packet_measurement_gap_sources,
            "packet_measurement_complete_ratio": (
                round(
                    packet_measurement_complete_sources / runtime_source_count,
                    6,
                )
                if runtime_source_count
                else None
            ),
        }

    def producer_readiness_payload() -> dict[str, object]:
        return readiness_projection(sensor_ingress.stats().sources)

    def online_producer_readiness_payload() -> dict[str, object]:
        return readiness_projection(
            source
            for source in sensor_ingress.stats().sources
            if source.presence == "online"
        )

    def refresh_producer_readiness_transition() -> None:
        current_readiness = producer_readiness_payload()
        stored_readiness = producer_readiness_transition_state["current"]
        if (
            stored_readiness is None
            or current_readiness["runtime_sources"] == 0
            or stored_readiness["runtime_sources"] == 0
        ):
            producer_readiness_transition_state["current"] = current_readiness
            producer_readiness_transition_state["previous"] = None
            producer_readiness_transition_state["changed_utc"] = None
        elif current_readiness != stored_readiness:
            producer_readiness_transition_state["previous"] = stored_readiness
            producer_readiness_transition_state["current"] = current_readiness
            producer_readiness_transition_state["changed_utc"] = (
                effective_sensor_clock().isoformat()
            )

    def producer_readiness_transition_payload(
        current_readiness: dict[str, object],
        transition_state: dict[str, object] | None = None,
    ) -> dict[str, object]:
        state = transition_state or producer_readiness_transition_state
        if state.get("current") != current_readiness:
            state = {
                "current": current_readiness,
                "previous": None,
                "changed_utc": None,
            }
        previous_readiness = state["previous"]
        return {
            "previous": previous_readiness,
            "changed_utc": state["changed_utc"],
            "heartbeat_v6_sources_delta": (
                current_readiness["heartbeat_v6_sources"]
                - previous_readiness["heartbeat_v6_sources"]
                if previous_readiness is not None
                else None
            ),
            "heartbeat_v6_ratio_delta": (
                round(
                    current_readiness["heartbeat_v6_ratio"]
                    - previous_readiness["heartbeat_v6_ratio"],
                    6,
                )
                if (
                    previous_readiness is not None
                    and current_readiness["heartbeat_v6_ratio"] is not None
                    and previous_readiness["heartbeat_v6_ratio"] is not None
                )
                else None
            ),
            "packet_measurement_complete_sources_delta": (
                current_readiness["packet_measurement_complete_sources"]
                - previous_readiness["packet_measurement_complete_sources"]
                if previous_readiness is not None
                else None
            ),
            "packet_measurement_complete_ratio_delta": (
                round(
                    current_readiness["packet_measurement_complete_ratio"]
                    - previous_readiness["packet_measurement_complete_ratio"],
                    6,
                )
                if (
                    previous_readiness is not None
                    and current_readiness["packet_measurement_complete_ratio"]
                    is not None
                    and previous_readiness[
                        "packet_measurement_complete_ratio"
                    ] is not None
                )
                else None
            ),
        }

    def sensor_fleet_summary_payload(
        runtime_status=None,
        transition_state: dict[str, object] | None = None,
    ) -> dict[str, object]:
        if runtime_status is None:
            runtime_status = sensor_ingress.stats()
        runtime_by_id = {
            source.source_id: source
            for source in runtime_status.sources
        }
        metadata_by_id = {
            entry.source_id: entry
            for entry in registry.list()
        }
        source_ids = sorted(set(runtime_by_id) | set(metadata_by_id))

        lifecycle_counts = {"active": 0, "retired": 0}
        presence_counts = {
            "online": 0,
            "stale": 0,
            "offline": 0,
            "unknown": 0,
        }
        control_counts = {
            "converged": 0,
            "pending": 0,
            "unknown": 0,
        }
        transport_counts = {
            "normal": 0,
            "warning": 0,
            "critical": 0,
            "unknown": 0,
        }
        capability_refresh_counts = {
            "current": 0,
            "stale": 0,
            "unknown": 0,
        }
        negotiated_compression_counts = {
            "none": 0,
            "zlib": 0,
            "auto": 0,
            "unknown": 0,
        }
        packet_target_counts = {
            "within_target": 0,
            "above_target": 0,
            "unknown": 0,
        }
        packet_target_pressure_counts = {
            "clear": 0,
            "transient": 0,
            "sustained": 0,
            "unknown": 0,
        }
        packet_target_attention_streak_threshold = (
            PACKET_TARGET_ATTENTION_STREAK_THRESHOLD
        )
        packet_target_sustained_episode_total = 0
        packet_target_recovered_sources = 0
        packet_target_recurrence_total = 0
        packet_target_recurring_sources = 0
        packet_target_stability_counts = {
            "stable": 0,
            "recurring": 0,
            "flapping": 0,
            "unknown": 0,
        }
        packet_target_measurement_coverage = {
            "complete": 0,
            "target_only": 0,
            "unavailable": 0,
        }
        packet_target_overshoot_measured_sources = 0
        packet_target_max_overshoot_delta = None
        packet_target_max_overshoot_ratio = None
        packet_target_worst_source_id = None
        packet_target_worst_target_utilization = None
        packet_target_worst_observed_utilization = None
        packet_target_sustained_sources = 0
        packet_target_longest_sustained_seconds = None
        packet_target_longest_sustained_source_id = None
        packet_target_longest_sustained_since_utc = None
        packet_target_max_recurrence_count = 0
        packet_target_most_recurrent_source_id = None
        packet_target_most_recurrent_last_interval_seconds = None
        packet_target_latest_recovery_utc = None
        packet_target_latest_recovery_source_id = None
        packet_target_latest_recovery_age_seconds = None
        packet_target_measurement_gaps = []
        packet_target_measurement_gap_total = 0
        heartbeat_schema_coverage = {
            "runtime_sources": 0,
            "v2": 0,
            "v3": 0,
            "v4": 0,
            "v5": 0,
            "v6": 0,
            "no_heartbeat": 0,
            "upgrade_required": 0,
        }
        heartbeat_upgrade_candidates = []
        attention = []
        attention_total = 0

        for source_id in source_ids:
            runtime_source = runtime_by_id.get(source_id)
            metadata = registry.get(source_id)
            lifecycle = (
                "retired"
                if metadata.retired_utc is not None
                else "active"
            )
            lifecycle_counts[lifecycle] += 1

            presence = (
                runtime_source.presence
                if runtime_source is not None
                else "unknown"
            )
            presence_counts[presence] += 1

            effective = (
                runtime_source.capture_active
                if runtime_source is not None
                else None
            )
            applied_revision = (
                runtime_source.applied_revision
                if runtime_source is not None
                else None
            )
            control_state = registry.control_state(source_id)
            if effective is None or applied_revision is None:
                control_status = "unknown"
            elif (
                applied_revision == control_state.revision
                and effective == metadata.enabled
            ):
                control_status = "converged"
            else:
                control_status = "pending"
            control_counts[control_status] += 1

            packet_transport = (
                runtime_source.packet_transport
                if runtime_source is not None
                else None
            )
            transport_status = (
                packet_transport.get("payload_status")
                if packet_transport is not None
                else None
            )
            if transport_status not in {"normal", "warning", "critical"}:
                transport_status = "unknown"
            transport_counts[transport_status] += 1

            capability_refresh_status = (
                runtime_source.capability_refresh_status
                if runtime_source is not None
                else "unknown"
            )
            if capability_refresh_status not in {"current", "stale"}:
                capability_refresh_status = "unknown"
            capability_refresh_counts[capability_refresh_status] += 1

            negotiated_compression = (
                runtime_source.negotiated_packet_compression
                if runtime_source is not None
                else None
            )
            if negotiated_compression not in {"none", "zlib", "auto"}:
                negotiated_compression = "unknown"
            negotiated_compression_counts[negotiated_compression] += 1

            target_utilization = (
                runtime_source.negotiated_packet_target_utilization
                if runtime_source is not None
                else None
            )
            observed_utilization = (
                runtime_source.observed_packet_utilization
                if runtime_source is not None
                else None
            )
            producer_source_readiness = producer_source_readiness_payload(
                runtime_source
            )
            packet_target_measurement = producer_source_readiness[
                "packet_measurement"
            ]
            if packet_target_measurement is None:
                packet_target_measurement = "unavailable"
            packet_target_measurement_coverage[packet_target_measurement] += 1
            if runtime_source is not None:
                heartbeat_schema_coverage["runtime_sources"] += 1
                heartbeat_schema_id = producer_source_readiness[
                    "heartbeat_schema_id"
                ]
                heartbeat_version = (
                    heartbeat_schema_id.rsplit("/", 1)[-1]
                    if heartbeat_schema_id is not None
                    else None
                )
                if heartbeat_version in {"v2", "v3", "v4", "v5", "v6"}:
                    heartbeat_schema_coverage[heartbeat_version] += 1
                else:
                    heartbeat_schema_coverage["no_heartbeat"] += 1
                if producer_source_readiness["heartbeat_upgrade_required"]:
                    heartbeat_schema_coverage["upgrade_required"] += 1
                    heartbeat_upgrade_candidates.append(
                        {
                            "source_id": source_id,
                            "presence": runtime_source.presence,
                            "current_schema_id": heartbeat_schema_id,
                            "required_schema_id": producer_source_readiness[
                                "required_heartbeat_schema_id"
                            ],
                            "measurement": packet_target_measurement,
                            "upgrade_stage": producer_source_readiness[
                                "heartbeat_upgrade_stage"
                            ],
                            "versions_behind": producer_source_readiness[
                                "heartbeat_versions_behind"
                            ],
                        }
                    )
            if (
                runtime_source is not None
                and packet_target_measurement != "complete"
            ):
                packet_target_measurement_gap_total += 1
                if len(packet_target_measurement_gaps) < 32:
                    packet_target_measurement_gaps.append(
                        {
                            "source_id": source_id,
                            "measurement": packet_target_measurement,
                            "presence": runtime_source.presence,
                            "heartbeat_schema_id": (
                                runtime_source.heartbeat_schema_id
                            ),
                            "required_schema_id": (
                                "visionrig/sensor-heartbeat/v6"
                            ),
                            "negotiated_packet_target_utilization": (
                                target_utilization
                            ),
                            "observed_packet_utilization": (
                                observed_utilization
                            ),
                        }
                    )
            packet_target_status = packet_target_compliance_status(runtime_source)
            packet_target_counts[packet_target_status] += 1

            packet_target_above_streak = (
                runtime_source.packet_target_above_streak
                if runtime_source is not None
                else 0
            )
            packet_target_pressure = packet_target_pressure_status(
                runtime_source,
                packet_target_status,
            )
            packet_target_pressure_counts[packet_target_pressure] += 1
            if (
                packet_target_pressure == "sustained"
                and runtime_source is not None
            ):
                packet_target_sustained_sources += 1
                sustained_seconds = runtime_source.packet_target_above_seconds
                if sustained_seconds is not None and (
                    packet_target_longest_sustained_seconds is None
                    or sustained_seconds
                    > packet_target_longest_sustained_seconds
                ):
                    packet_target_longest_sustained_seconds = sustained_seconds
                    packet_target_longest_sustained_source_id = source_id
                    packet_target_longest_sustained_since_utc = (
                        runtime_source.packet_target_above_since_utc
                    )
            if runtime_source is not None:
                packet_target_sustained_episode_total += (
                    runtime_source.packet_target_sustained_episode_count
                )
                if runtime_source.packet_target_last_recovered_utc is not None:
                    packet_target_recovered_sources += 1
                    if (
                        packet_target_latest_recovery_utc is None
                        or runtime_source.packet_target_last_recovered_utc
                        > packet_target_latest_recovery_utc
                    ):
                        packet_target_latest_recovery_utc = (
                            runtime_source.packet_target_last_recovered_utc
                        )
                        packet_target_latest_recovery_source_id = source_id
                        recovered_at = datetime.fromisoformat(
                            runtime_source.packet_target_last_recovered_utc
                        )
                        packet_target_latest_recovery_age_seconds = round(
                            max(
                                0.0,
                                (
                                    effective_sensor_clock() - recovered_at
                                ).total_seconds(),
                            ),
                            3,
                        )
                packet_target_recurrence_total += (
                    runtime_source.packet_target_recurrence_count
                )
                if runtime_source.packet_target_recurrence_count > 0:
                    packet_target_recurring_sources += 1
                    if (
                        runtime_source.packet_target_recurrence_count
                        > packet_target_max_recurrence_count
                    ):
                        packet_target_max_recurrence_count = (
                            runtime_source.packet_target_recurrence_count
                        )
                        packet_target_most_recurrent_source_id = source_id
                        packet_target_most_recurrent_last_interval_seconds = (
                            runtime_source.packet_target_last_recurrence_seconds
                        )

            packet_target_stability = packet_target_stability_status(runtime_source)
            packet_target_stability_counts[packet_target_stability] += 1
            packet_target_overshoot_delta, packet_target_overshoot_ratio = (
                packet_target_overshoot(runtime_source)
            )
            if packet_target_overshoot_delta is not None:
                packet_target_overshoot_measured_sources += 1
                if (
                    packet_target_max_overshoot_delta is None
                    or packet_target_overshoot_delta
                    > packet_target_max_overshoot_delta
                ):
                    packet_target_max_overshoot_delta = packet_target_overshoot_delta
                    packet_target_max_overshoot_ratio = (
                        packet_target_overshoot_ratio
                    )
                    packet_target_worst_source_id = source_id
                    packet_target_worst_target_utilization = target_utilization
                    packet_target_worst_observed_utilization = (
                        observed_utilization
                    )

            attention_reasons = []
            if control_status == "pending":
                attention_reasons.append("control_pending")
            if lifecycle == "active" and presence != "online":
                attention_reasons.append("presence")
            if transport_status in {"warning", "critical"}:
                attention_reasons.append("packet_transport")
            if capability_refresh_status == "stale":
                attention_reasons.append("capability_refresh")
            if packet_target_pressure == "sustained":
                attention_reasons.append("packet_target")

            needs_attention = bool(attention_reasons)
            if needs_attention:
                attention_total += 1
            if needs_attention and len(attention) < 32:
                attention.append(
                    {
                        "source_id": source_id,
                        "lifecycle": lifecycle,
                        "presence": presence,
                        "control_status": control_status,
                        "transport_status": transport_status,
                        "capability_refresh_status": capability_refresh_status,
                        "negotiated_max_payload_bytes": (
                            runtime_source.negotiated_max_payload_bytes
                            if runtime_source is not None
                            else None
                        ),
                        "negotiated_packet_compression": (
                            runtime_source.negotiated_packet_compression
                            if runtime_source is not None
                            else None
                        ),
                        "negotiated_packet_target_utilization": target_utilization,
                        "observed_packet_utilization": observed_utilization,
                        "packet_target_status": packet_target_status,
                        "packet_target_pressure_status": packet_target_pressure,
                        "packet_target_overshoot_delta": (
                            packet_target_overshoot_delta
                        ),
                        "packet_target_overshoot_ratio": (
                            packet_target_overshoot_ratio
                        ),
                        "packet_target_above_streak": packet_target_above_streak,
                        "packet_target_above_since_utc": (
                            runtime_source.packet_target_above_since_utc
                            if runtime_source is not None
                            else None
                        ),
                        "packet_target_above_seconds": (
                            runtime_source.packet_target_above_seconds
                            if runtime_source is not None
                            else None
                        ),
                        "packet_target_last_above_utc": (
                            runtime_source.packet_target_last_above_utc
                            if runtime_source is not None
                            else None
                        ),
                        "packet_target_sustained_episode_count": (
                            runtime_source.packet_target_sustained_episode_count
                            if runtime_source is not None
                            else 0
                        ),
                        "packet_target_last_recovered_utc": (
                            runtime_source.packet_target_last_recovered_utc
                            if runtime_source is not None
                            else None
                        ),
                        "packet_target_recurrence_count": (
                            runtime_source.packet_target_recurrence_count
                            if runtime_source is not None
                            else 0
                        ),
                        "packet_target_last_recurrence_seconds": (
                            runtime_source.packet_target_last_recurrence_seconds
                            if runtime_source is not None
                            else None
                        ),
                        "packet_target_stability_status": packet_target_stability,
                        "capability_refresh_age_seconds": (
                            runtime_source.capability_refresh_age_seconds
                            if runtime_source is not None
                            else None
                        ),
                        "packet_transport": packet_transport,
                        "reasons": attention_reasons,
                        "pending_seconds": (
                            registry.control_pending_seconds(source_id)
                            if control_status == "pending"
                            else None
                        ),
                    }
                )

        heartbeat_upgrade_candidates.sort(
            key=lambda item: (
                item["versions_behind"] is None,
                -(item["versions_behind"] or 0),
                item["source_id"],
            )
        )
        heartbeat_upgrade_candidate_total = len(heartbeat_upgrade_candidates)
        bounded_heartbeat_upgrade_candidates = heartbeat_upgrade_candidates[:32]
        producer_readiness = readiness_projection(runtime_status.sources)
        online_producer_readiness = readiness_projection(
            source
            for source in runtime_status.sources
            if source.presence == "online"
        )
        producer_readiness_transition = producer_readiness_transition_payload(
            producer_readiness,
            transition_state=transition_state,
        )

        return {
            "schema": "visionrig/sensor-fleet-summary/v29",
            "state_revision": registry.state_revision,
            "change_consistency": sensor_change_consistency_payload(),
            "total": len(source_ids),
            "lifecycle": lifecycle_counts,
            "presence": presence_counts,
            "control": control_counts,
            "transport": transport_counts,
            "capability_refresh": capability_refresh_counts,
            "negotiated_compression": negotiated_compression_counts,
            "packet_target": packet_target_counts,
            "packet_target_pressure": packet_target_pressure_counts,
            "packet_target_attention_streak_threshold": (
                packet_target_attention_streak_threshold
            ),
            "packet_target_sustained_episode_total": (
                packet_target_sustained_episode_total
            ),
            "packet_target_recovered_sources": packet_target_recovered_sources,
            "packet_target_recurrence_total": packet_target_recurrence_total,
            "packet_target_recurring_sources": packet_target_recurring_sources,
            "packet_target_stability": packet_target_stability_counts,
            "packet_target_measurement_coverage": packet_target_measurement_coverage,
            "heartbeat_schema_coverage": heartbeat_schema_coverage,
            "heartbeat_upgrade_candidates": bounded_heartbeat_upgrade_candidates,
            "heartbeat_upgrade_candidate_total": (
                heartbeat_upgrade_candidate_total
            ),
            "heartbeat_upgrade_candidates_truncated": (
                heartbeat_upgrade_candidate_total
                > len(bounded_heartbeat_upgrade_candidates)
            ),
            "producer_readiness": producer_readiness,
            "online_producer_readiness": online_producer_readiness,
            "producer_readiness_transition": producer_readiness_transition,
            "packet_target_flap_window_seconds": packet_target_flap_window_seconds,
            "packet_target_overshoot": {
                "measured_sources": packet_target_overshoot_measured_sources,
                "max_delta": packet_target_max_overshoot_delta,
                "max_ratio": packet_target_max_overshoot_ratio,
                "worst_source_id": packet_target_worst_source_id,
                "worst_target_utilization": (
                    packet_target_worst_target_utilization
                ),
                "worst_observed_utilization": (
                    packet_target_worst_observed_utilization
                ),
            },
            "packet_target_sustained_pressure": {
                "sources": packet_target_sustained_sources,
                "longest_seconds": packet_target_longest_sustained_seconds,
                "longest_source_id": packet_target_longest_sustained_source_id,
                "longest_since_utc": packet_target_longest_sustained_since_utc,
            },
            "packet_target_recurrence_hotspot": {
                "max_recurrence_count": packet_target_max_recurrence_count,
                "source_id": packet_target_most_recurrent_source_id,
                "last_recurrence_seconds": (
                    packet_target_most_recurrent_last_interval_seconds
                ),
            },
            "packet_target_latest_recovery": {
                "source_id": packet_target_latest_recovery_source_id,
                "recovered_utc": packet_target_latest_recovery_utc,
                "age_seconds": packet_target_latest_recovery_age_seconds,
            },
            "packet_target_measurement_gaps": packet_target_measurement_gaps,
            "packet_target_measurement_gap_total": (
                packet_target_measurement_gap_total
            ),
            "packet_target_measurement_gaps_truncated": (
                packet_target_measurement_gap_total
                > len(packet_target_measurement_gaps)
            ),
            "attention": attention,
            "attention_total": attention_total,
            "attention_truncated": attention_total > len(attention),
        }

    @app.get("/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "service": "visionrig",
            "schema": "visionrig/health/v61",
            "perception_schema": "visionrig/perception-event/v3",
            "stages": selected_pipeline.stages,
            "capture_queue": asdict(runtime.stats()),
            "event_sinks": asdict(runtime.sink_stats()),
            "modelrig_bridge": (
                {
                    "enabled": True,
                    "endpoint": modelrig_publisher.endpoint,
                    "stats": asdict(modelrig_publisher.stats()),
                    "last_status": (
                        modelrig_publisher.last_result.status
                        if modelrig_publisher.last_result is not None
                        else None
                    ),
                }
                if modelrig_publisher is not None
                else {"enabled": False}
            ),
            "sensor_ingress": {
                "schema": "visionrig/sensor-ingress/v9",
                "max_frame_bytes": sensor_ingress.max_payload_bytes,
                "media_types": [
                    "image/jpeg",
                    "image/png",
                    "image/webp",
                    SENSOR_PACKET_MEDIA_TYPE,
                ],
                "heartbeat_schemas": [
                    "visionrig/sensor-heartbeat/v2",
                    "visionrig/sensor-heartbeat/v3",
                    "visionrig/sensor-heartbeat/v4",
                    "visionrig/sensor-heartbeat/v5",
                    "visionrig/sensor-heartbeat/v6",
                ],
                "sensor_packet_schemas": [
                    "visionrig/sensor-packet/v1",
                    "visionrig/sensor-packet/v2",
                ],
                "sensor_packet_compressions": ["none", "zlib"],
                "sensor_packet_transport_schema": (
                    "visionrig/sensor-packet-transport/v2"
                ),
                "sensor_packet_payload_thresholds": {
                    "warning": SENSOR_PACKET_PAYLOAD_WARNING_UTILIZATION,
                    "critical": SENSOR_PACKET_PAYLOAD_CRITICAL_UTILIZATION,
                },
                "overload_policy": "reject",
                "runtime": asdict(sensor_ingress.stats()),
            },
            "sensor_registry": {
                "schema": "visionrig/sensor-registry/v7",
                "state_revision": registry.state_revision,
                "entries": len(registry.list()),
                "discovered": len(registry.list_discovery()),
                "desired_state_schema": "visionrig/sensor-desired-state/v2",
            },
            "sensor_fleet": sensor_fleet_summary_payload(),
            "sensor_changes": {
                "schema": "visionrig/sensor-change-batch/v2",
                "durability": (
                    "persistent" if sensor_changes.persistent else "process-local"
                ),
                "stream_id": sensor_changes.stream_id,
                "state_revision_high_water": sensor_changes.state_revision_high_water,
                "consistency": sensor_change_consistency_payload(),
                "max_wait_seconds": 30,
            },
            "sensor_bootstrap": {
                "schema": "visionrig/sensor-bootstrap-snapshot/v33",
            },
        }

    @app.get("/api/v1/capabilities")
    def capabilities() -> dict[str, object]:
        return probe_capabilities()

    @app.get("/api/v1/capture/stats")
    def capture_stats() -> dict[str, int]:
        return asdict(runtime.stats())

    @app.get("/api/v1/sensors/status")
    def sensor_status() -> dict[str, object]:
        return asdict(sensor_ingress.stats())

    @app.get("/api/v1/sensors/fleet")
    def sensor_fleet_summary() -> dict[str, object]:
        return sensor_fleet_summary_payload()

    @app.get(
        "/api/v1/sensors/changes",
        response_model=SensorChangeBatch,
    )
    def sensor_change_feed(
        after_cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=64, ge=1, le=256),
        stream_id: str | None = Query(default=None, min_length=1, max_length=128),
        wait_seconds: float = Query(default=0.0, ge=0.0, le=30.0),
    ) -> SensorChangeBatch:
        return sensor_changes.wait_for_changes(
            after_cursor=after_cursor,
            limit=limit,
            expected_stream_id=stream_id,
            wait_seconds=wait_seconds,
        )

    def sensor_catalog_payload(runtime_status=None) -> dict[str, object]:
        if runtime_status is None:
            runtime_status = sensor_ingress.stats()
        runtime_by_id = {source.source_id: source for source in runtime_status.sources}
        metadata_by_id = {entry.source_id: entry for entry in registry.list()}
        source_ids = sorted(set(runtime_by_id) | set(metadata_by_id))
        sources = []
        for source_id in source_ids:
            runtime_source = runtime_by_id.get(source_id)
            metadata = registry.get(source_id)
            effective = (
                runtime_source.capture_active
                if runtime_source is not None
                else None
            )
            applied_revision = (
                runtime_source.applied_revision
                if runtime_source is not None
                else None
            )
            control_state = registry.control_state(source_id)
            desired_revision = control_state.revision
            if effective is None or applied_revision is None:
                control_status = "unknown"
            elif (
                applied_revision == desired_revision
                and effective == metadata.enabled
            ):
                control_status = "converged"
            else:
                control_status = "pending"
            discovery = registry.get_discovery(source_id)
            packet_target_stability = packet_target_stability_status(runtime_source)
            packet_target_measurement = packet_target_measurement_status(
                runtime_source
            )
            packet_target_status = packet_target_compliance_status(runtime_source)
            packet_target_overshoot_delta, packet_target_overshoot_ratio = (
                packet_target_overshoot(runtime_source)
            )
            packet_target_pressure = packet_target_pressure_status(
                runtime_source,
                packet_target_status,
            )
            producer_source_readiness = producer_source_readiness_payload(
                runtime_source
            )
            sources.append(
                {
                    "source_id": source_id,
                    "runtime": (
                        asdict(runtime_source)
                        if runtime_source is not None
                        else None
                    ),
                    "metadata": asdict(metadata),
                    "lifecycle": {
                        "status": (
                            "retired"
                            if metadata.retired_utc is not None
                            else "active"
                        ),
                        "retired_utc": metadata.retired_utc,
                    },
                    "discovery": (
                        asdict(discovery)
                        if discovery is not None
                        else None
                    ),
                    "producer_readiness": producer_source_readiness,
                    "packet_target": {
                        "measurement": packet_target_measurement,
                        "status": packet_target_status,
                        "pressure": packet_target_pressure,
                        "stability": packet_target_stability,
                        "target_utilization": (
                            runtime_source.negotiated_packet_target_utilization
                            if runtime_source is not None
                            else None
                        ),
                        "observed_utilization": (
                            runtime_source.observed_packet_utilization
                            if runtime_source is not None
                            else None
                        ),
                        "overshoot_delta": packet_target_overshoot_delta,
                        "overshoot_ratio": packet_target_overshoot_ratio,
                        "attention_streak_threshold": (
                            PACKET_TARGET_ATTENTION_STREAK_THRESHOLD
                        ),
                        "above_streak": (
                            runtime_source.packet_target_above_streak
                            if runtime_source is not None
                            else 0
                        ),
                        "above_since_utc": (
                            runtime_source.packet_target_above_since_utc
                            if runtime_source is not None
                            else None
                        ),
                        "above_seconds": (
                            runtime_source.packet_target_above_seconds
                            if runtime_source is not None
                            else None
                        ),
                        "last_above_utc": (
                            runtime_source.packet_target_last_above_utc
                            if runtime_source is not None
                            else None
                        ),
                        "sustained_episode_count": (
                            runtime_source.packet_target_sustained_episode_count
                            if runtime_source is not None
                            else 0
                        ),
                        "last_recovered_utc": (
                            runtime_source.packet_target_last_recovered_utc
                            if runtime_source is not None
                            else None
                        ),
                        "flap_window_seconds": packet_target_flap_window_seconds,
                        "recurrence_count": (
                            runtime_source.packet_target_recurrence_count
                            if runtime_source is not None
                            else 0
                        ),
                        "last_recurrence_seconds": (
                            runtime_source.packet_target_last_recurrence_seconds
                            if runtime_source is not None
                            else None
                        ),
                    },
                    "control": {
                        "desired_enabled": metadata.enabled,
                        "desired_revision": desired_revision,
                        "desired_changed_utc": control_state.changed_utc,
                        "effective_capture_active": effective,
                        "applied_revision": applied_revision,
                        "pending_seconds": (
                            registry.control_pending_seconds(source_id)
                            if control_status != "converged"
                            else None
                        ),
                        "status": control_status,
                    },
                }
            )
        return {
            "schema": "visionrig/sensor-catalog/v15",
            "sources": sources,
        }

    @app.get("/api/v1/sensors/catalog")
    def sensor_catalog() -> dict[str, object]:
        return sensor_catalog_payload()

    @app.get("/api/v1/sensors/bootstrap")
    def sensor_bootstrap_snapshot() -> dict[str, object]:
        # Sample the change cursor before building state. Changes racing with
        # snapshot construction may be replayed, but cannot be missed.
        change_state = sensor_changes.read(after_cursor=0, limit=1)
        baseline_cursor = change_state.newest_available_cursor or 0
        runtime_status = sensor_ingress.stats()
        transition_state = {
            "current": (
                dict(producer_readiness_transition_state["current"])
                if producer_readiness_transition_state["current"] is not None
                else None
            ),
            "previous": (
                dict(producer_readiness_transition_state["previous"])
                if producer_readiness_transition_state["previous"] is not None
                else None
            ),
            "changed_utc": producer_readiness_transition_state["changed_utc"],
        }
        return {
            "schema": "visionrig/sensor-bootstrap-snapshot/v33",
            "sensor_state_revision": registry.state_revision,
            "change_consistency": sensor_change_consistency_payload(),
            "change_stream_id": sensor_changes.stream_id,
            "change_cursor": baseline_cursor,
            "catalog": sensor_catalog_payload(runtime_status),
            "fleet": sensor_fleet_summary_payload(
                runtime_status,
                transition_state=transition_state,
            ),
        }

    @app.get(
        "/api/v1/sensors/{source_id}/desired-state",
        response_model=SensorDesiredState,
    )
    def sensor_desired_state(source_id: str) -> SensorDesiredState:
        if not source_id or len(source_id) > 128:
            raise HTTPException(
                status_code=422,
                detail="source_id must contain 1..128 characters",
            )
        return registry.desired_state(source_id)

    @app.post("/api/v1/sensors/{source_id}/retire")
    def retire_sensor(
        source_id: str,
        expected_state_revision: int | None = Query(default=None, ge=0),
    ) -> dict[str, object]:
        previous = registry.get(source_id)
        try:
            metadata = registry.retire(
                source_id,
                expected_state_revision=expected_state_revision,
            )
        except SensorStateRevisionConflict as exc:
            raise_state_revision_conflict(exc)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if previous.retired_utc is None and metadata.retired_utc is not None:
            desired = registry.desired_state(source_id)
            append_sensor_change(
                kind="retired",
                source_id=source_id,
                payload={
                    "retired_utc": metadata.retired_utc,
                    "desired_enabled": desired.enabled,
                    "desired_revision": desired.revision,
                },
            )
        return {
            "schema": "visionrig/sensor-lifecycle/v2",
            "state_revision": registry.state_revision,
            "status": "retired",
            "metadata": asdict(metadata),
            "desired_state": registry.desired_state(source_id).model_dump(),
        }

    @app.post("/api/v1/sensors/{source_id}/restore")
    def restore_sensor(
        source_id: str,
        expected_state_revision: int | None = Query(default=None, ge=0),
    ) -> dict[str, object]:
        previous = registry.get(source_id)
        try:
            metadata = registry.restore(
                source_id,
                expected_state_revision=expected_state_revision,
            )
        except SensorStateRevisionConflict as exc:
            raise_state_revision_conflict(exc)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if previous.retired_utc is not None and metadata.retired_utc is None:
            append_sensor_change(
                kind="restored",
                source_id=source_id,
                payload={"enabled": metadata.enabled},
            )
        return {
            "schema": "visionrig/sensor-lifecycle/v2",
            "state_revision": registry.state_revision,
            "status": "active",
            "metadata": asdict(metadata),
            "desired_state": registry.desired_state(source_id).model_dump(),
        }

    @app.delete("/api/v1/sensors/{source_id}")
    def forget_sensor(
        source_id: str,
        expected_state_revision: int | None = Query(default=None, ge=0),
    ) -> dict[str, object]:
        try:
            registry.assert_state_revision(expected_state_revision)
        except SensorStateRevisionConflict as exc:
            raise_state_revision_conflict(exc)

        if not registry.contains(source_id):
            raise HTTPException(status_code=404, detail="sensor is not registered")

        metadata = registry.get(source_id)
        if metadata.retired_utc is None:
            raise HTTPException(
                status_code=409,
                detail="sensor must be retired before it can be forgotten",
            )

        runtime_source = next(
            (
                source
                for source in sensor_ingress.stats().sources
                if source.source_id == source_id
            ),
            None,
        )
        if runtime_source is not None and runtime_source.presence != "offline":
            raise HTTPException(
                status_code=409,
                detail="retired sensor must be offline before it can be forgotten",
            )

        try:
            registry.forget(
                source_id,
                expected_state_revision=expected_state_revision,
            )
        except SensorStateRevisionConflict as exc:
            raise_state_revision_conflict(exc)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="sensor is not registered") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        sensor_ingress.forget_source(source_id)
        refresh_producer_readiness_transition()
        append_sensor_change(
            kind="forgotten",
            source_id=source_id,
        )
        return {
            "schema": "visionrig/sensor-forget/v2",
            "state_revision": registry.state_revision,
            "status": "forgotten",
            "source_id": source_id,
        }

    @app.patch("/api/v1/sensors/{source_id}/metadata")
    def patch_sensor_metadata(
        source_id: str,
        body: SensorMetadataPatch,
        expected_state_revision: int | None = Query(default=None, ge=0),
    ) -> dict[str, object]:
        previous = registry.get(source_id)
        previous_revision = registry.control_revision(source_id)
        was_registered = registry.contains(source_id)
        try:
            metadata = registry.patch(
                source_id,
                body,
                expected_state_revision=expected_state_revision,
            )
        except SensorStateRevisionConflict as exc:
            raise_state_revision_conflict(exc)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        if not was_registered:
            append_sensor_change(
                kind="registered",
                source_id=source_id,
                payload={"metadata": asdict(metadata)},
            )

        metadata_fields_changed = (
            previous.display_name != metadata.display_name
            or previous.location != metadata.location
            or previous.role != metadata.role
        )
        if metadata_fields_changed:
            append_sensor_change(
                kind="metadata_changed",
                source_id=source_id,
                payload={
                    "display_name": metadata.display_name,
                    "location": metadata.location,
                    "role": metadata.role,
                },
            )

        current_revision = registry.control_revision(source_id)
        if (
            previous.enabled != metadata.enabled
            or previous_revision != current_revision
        ):
            append_sensor_change(
                kind="control_changed",
                source_id=source_id,
                payload={
                    "enabled": metadata.enabled,
                    "revision": current_revision,
                    "changed_utc": registry.control_state(source_id).changed_utc,
                },
            )
        return {
            "schema": "visionrig/sensor-metadata/v2",
            "state_revision": registry.state_revision,
            "metadata": asdict(metadata),
        }

    @app.post("/api/v1/sensors/heartbeat", response_model=SensorHeartbeatReceipt)
    def sensor_heartbeat(body: SensorHeartbeat) -> SensorHeartbeatReceipt:
        was_registered = registry.contains(body.source_id)
        previous_discovery = registry.get_discovery(body.source_id)
        previous_runtime = runtime_source_for(body.source_id)
        try:
            registry.observe(
                body.source_id,
                source_type=body.source_type,
                device=body.device,
                capabilities=body.capabilities,
            )
            receipt = sensor_ingress.heartbeat(body)
            emit_registration_changes(
                body.source_id,
                was_registered=was_registered,
                previous_discovery=previous_discovery,
            )
            emit_runtime_change(body.source_id, previous_runtime)
            refresh_producer_readiness_transition()
            return receipt
        except SensorIdentityConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/perception/ingest", response_model=PerceptionEvent)
    def ingest(body: IngestBody) -> PerceptionEvent:
        try:
            return runtime.process_direct(
                Frame(
                    source=body.source,
                    sequence=body.frame_sequence,
                    payload=body.payload,
                    dropped_frames=body.dropped_frames,
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/frames/ingest", response_model=SensorFrameReceipt)
    async def ingest_sensor_frame(
        request: Request,
        source_id: str = Query(min_length=1, max_length=128),
        source_type: Literal["camera", "screen", "vr", "image"] = Query(),
        frame_sequence: int = Query(ge=0),
        device: str | None = Query(default=None, max_length=256),
        dropped_frames: int = Query(default=0, ge=0),
    ) -> SensorFrameReceipt:
        content_type = request.headers.get("content-type", "")
        payload = await read_bounded_body(request, sensor_ingress.max_payload_bytes)
        was_registered = registry.contains(source_id)
        previous_discovery = registry.get_discovery(source_id)
        try:
            registry.observe(
                source_id,
                source_type=source_type,
                device=device,
            )
            receipt = await run_in_threadpool(
                sensor_ingress.process_encoded,
                source_id=source_id,
                source_type=source_type,
                frame_sequence=frame_sequence,
                payload=payload,
                content_type=content_type,
                device=device,
                dropped_frames=dropped_frames,
            )
            emit_registration_changes(
                source_id,
                was_registered=was_registered,
                previous_discovery=previous_discovery,
            )
            refresh_producer_readiness_transition()
            return receipt
        except SensorIdentityConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except SensorIngressBusy as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except SensorSequenceError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except SensorPayloadTooLarge as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except SensorMediaTypeError as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        except SensorDecodeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/sensor-packets/ingest", response_model=SensorFrameReceipt)
    async def ingest_sensor_packet(
        request: Request,
        source_id: str = Query(min_length=1, max_length=128),
        source_type: Literal["camera", "screen", "vr", "image"] = Query(),
        frame_sequence: int = Query(ge=0),
        device: str | None = Query(default=None, max_length=256),
        dropped_frames: int = Query(default=0, ge=0),
    ) -> SensorFrameReceipt:
        content_type = request.headers.get("content-type", "")
        payload = await read_bounded_body(request, sensor_ingress.max_payload_bytes)
        was_registered = registry.contains(source_id)
        previous_discovery = registry.get_discovery(source_id)
        try:
            packet_header = inspect_sensor_packet(payload)
            if (
                previous_discovery is not None
                and previous_discovery.source_type != source_type
            ):
                raise SensorIdentityConflict(
                    f"source_id {source_id!r} is already registered as "
                    f"{previous_discovery.source_type!r}, not {source_type!r}"
                )
            capabilities = ["rgb"]
            if packet_header.depth is not None:
                capabilities.append("depth")
            if packet_header.infrared is not None:
                capabilities.append("infrared")
            receipt = await run_in_threadpool(
                sensor_ingress.process_packet,
                source_id=source_id,
                source_type=source_type,
                frame_sequence=frame_sequence,
                payload=payload,
                content_type=content_type,
                device=device,
                dropped_frames=dropped_frames,
            )
            registry.observe(
                source_id,
                source_type=source_type,
                device=device,
                capabilities=tuple(capabilities),
            )
            emit_registration_changes(
                source_id,
                was_registered=was_registered,
                previous_discovery=previous_discovery,
            )
            refresh_producer_readiness_transition()
            return receipt
        except SensorIdentityConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except SensorPacketError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except SensorIngressBusy as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except SensorSequenceError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except SensorPayloadTooLarge as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except SensorMediaTypeError as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        except SensorDecodeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/perception/events", response_model=EventBatch)
    def events(
        after_cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=64, ge=1, le=256),
    ) -> EventBatch:
        return runtime.events(after_cursor=after_cursor, limit=limit)

    @app.get("/api/v1/world", response_model=WorldSnapshot)
    def get_world() -> WorldSnapshot:
        return runtime.snapshot()

    if modelrig_publisher is not None:
        app.add_event_handler("shutdown", modelrig_publisher.close)

    return app


app = create_app()
