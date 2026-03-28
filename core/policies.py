"""Cost policy management engine.

Policies define governance rules for cloud resource creation costs.
They are evaluated against every resource creation event to determine
whether alerts, approvals, or automated actions are required.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from .audit import AuditLogger
from .models import CloudProvider, CostPolicy, ResourceCreationEvent

__all__ = ["PolicyEngine"]


class PolicyEngine:
    """Manages cost governance policies with full audit trail."""

    def __init__(
        self,
        policy_dir: str | Path,
        audit_logger: AuditLogger,
        *,
        base_dir: Path | None = None,
    ) -> None:
        policy_path = Path(policy_dir).resolve()
        if ".." in str(policy_dir):
            raise ValueError("policy_dir: path traversal ('..') is not allowed")
        if base_dir is not None:
            base = base_dir.resolve()
            try:
                policy_path.relative_to(base)
            except ValueError as exc:
                raise ValueError(f"policy_dir must be within {base}, got {policy_path}") from exc
        self._policy_dir = policy_path
        self._policy_dir.mkdir(parents=True, exist_ok=True)
        self._policies: dict[str, CostPolicy] = {}
        self._audit = audit_logger

    def load_policies(self) -> int:
        """Load policies from disk. Returns count loaded.

        Skips corrupt or invalid policy files instead of crashing,
        logging each failure for investigation.
        """
        count = 0
        errors = 0
        for policy_file in sorted(self._policy_dir.glob("*.json")):
            try:
                with policy_file.open() as f:
                    data = json.loads(f.read())
                policy = CostPolicy(**data)
                self._policies[policy.policy_id] = policy
                count += 1
            except (json.JSONDecodeError, ValueError, TypeError, KeyError) as e:
                errors += 1
                self._audit.log(
                    action="policy.load_error",
                    actor="system",
                    target=str(policy_file),
                    outcome="failure",
                    details={"error": str(e), "file": policy_file.name},
                )
        self._audit.log(
            action="policies.loaded",
            actor="system",
            target=str(self._policy_dir),
            details={"count": count, "errors": errors},
        )
        return count

    def create_policy(self, policy: CostPolicy, actor: str = "system") -> CostPolicy:
        """Create a new cost policy."""
        self._policies[policy.policy_id] = policy
        self._persist_policy(policy)
        self._audit.log(
            action="policy.created",
            actor=actor,
            target=policy.policy_id,
            details={"name": policy.name, "description": policy.description},
            related_policy_id=policy.policy_id,
        )
        return policy

    def update_policy(
        self, policy_id: str, updates: dict, actor: str = "system"
    ) -> CostPolicy | None:
        """Update an existing policy."""
        policy = self._policies.get(policy_id)
        if not policy:
            return None

        old_values = {}
        for key, value in updates.items():
            if hasattr(policy, key):
                old_values[key] = getattr(policy, key)
                setattr(policy, key, value)

        policy.updated_at = datetime.now(UTC)
        self._policies[policy_id] = policy
        self._persist_policy(policy)

        self._audit.log(
            action="policy.updated",
            actor=actor,
            target=policy_id,
            details={"changes": updates, "previous_values": old_values},
            related_policy_id=policy_id,
        )
        return policy

    def delete_policy(self, policy_id: str, actor: str = "system") -> bool:
        """Delete a policy (soft-delete by disabling, hard-delete from storage)."""
        policy = self._policies.pop(policy_id, None)
        if not policy:
            return False

        policy_file = self._policy_dir / f"{policy_id}.json"
        if policy_file.exists():
            policy_file.unlink()

        self._audit.log(
            action="policy.deleted",
            actor=actor,
            target=policy_id,
            details={"name": policy.name},
            related_policy_id=policy_id,
        )
        return True

    def evaluate_event(self, event: ResourceCreationEvent) -> list[tuple[CostPolicy, list[str]]]:
        """Evaluate an event against all active policies.

        Returns a list of (policy, violations) tuples for matching policies.
        """
        results: list[tuple[CostPolicy, list[str]]] = []

        for policy in self._policies.values():
            if not policy.enabled:
                continue

            if policy.provider and policy.provider != event.provider:
                continue

            if policy.resource_types and event.resource_type not in policy.resource_types:
                continue

            violations = self._check_violations(event, policy)
            if violations:
                results.append((policy, violations))
                self._audit.log(
                    action="policy.violation_detected",
                    actor="system",
                    target=event.resource_id,
                    provider=event.provider,
                    details={
                        "policy_name": policy.name,
                        "violations": violations,
                        "resource_type": event.resource_type,
                        "creator": event.creator_identity,
                        "estimated_cost": event.estimated_monthly_cost_usd,
                    },
                    related_policy_id=policy.policy_id,
                )

        return results

    def get_policies(
        self,
        provider: CloudProvider | None = None,
        enabled_only: bool = True,
    ) -> list[CostPolicy]:
        """List policies with optional filters."""
        policies = list(self._policies.values())
        if provider:
            policies = [p for p in policies if p.provider is None or p.provider == provider]
        if enabled_only:
            policies = [p for p in policies if p.enabled]
        return policies

    def get_policy(self, policy_id: str) -> CostPolicy | None:
        return self._policies.get(policy_id)

    def _check_violations(self, event: ResourceCreationEvent, policy: CostPolicy) -> list[str]:
        violations = []

        if (
            policy.max_monthly_cost_usd is not None
            and event.estimated_monthly_cost_usd > policy.max_monthly_cost_usd
        ):
            violations.append(
                f"Estimated cost ${event.estimated_monthly_cost_usd:,.2f}/month "
                f"exceeds policy limit of ${policy.max_monthly_cost_usd:,.2f}/month"
            )

        if policy.require_tags:
            missing = [t for t in policy.require_tags if t not in event.tags]
            if missing:
                violations.append(f"Missing required tags: {', '.join(missing)}")

        if (
            policy.require_approval_above_usd is not None
            and event.estimated_monthly_cost_usd > policy.require_approval_above_usd
        ):
            violations.append(
                f"Cost ${event.estimated_monthly_cost_usd:,.2f}/month requires "
                f"manual approval (threshold: ${policy.require_approval_above_usd:,.2f})"
            )

        if policy.blocked_regions and event.region in policy.blocked_regions:
            violations.append(
                f"Resource created in blocked region '{event.region}'"
            )

        if policy.preferred_regions and event.region not in policy.preferred_regions:
            violations.append(
                f"Resource created in non-preferred region '{event.region}'; "
                f"preferred: {', '.join(policy.preferred_regions)}"
            )

        if (
            policy.required_purchase_type
            and event.purchase_type != policy.required_purchase_type
        ):
            violations.append(
                f"Resource uses '{event.purchase_type}' purchase type, "
                f"policy requires '{policy.required_purchase_type}'"
            )

        if policy.schedule:
            schedule_violation = self._check_schedule_violation(event, policy)
            if schedule_violation:
                violations.append(schedule_violation)

        return violations

    @staticmethod
    def _check_schedule_violation(
        event: ResourceCreationEvent, policy: CostPolicy
    ) -> str | None:
        """Check if a resource was created outside allowed schedule hours."""
        active_hours = policy.schedule.get("active_hours", "")
        if not active_hours or "-" not in active_hours:
            return None

        try:
            start_str, end_str = active_hours.split("-", 1)
            start_h, start_m = (int(x) for x in start_str.strip().split(":"))
            end_h, end_m = (int(x) for x in end_str.strip().split(":"))
        except (ValueError, IndexError):
            return None

        event_hour = event.timestamp.hour
        event_minute = event.timestamp.minute
        event_time = event_hour * 60 + event_minute
        start_time = start_h * 60 + start_m
        end_time = end_h * 60 + end_m

        outside_hours = (
            event_time < start_time or event_time >= end_time
            if start_time < end_time
            else event_time < start_time and event_time >= end_time
        )

        if outside_hours:
            active_days = policy.schedule.get("active_days", "")
            return (
                f"Resource created outside active hours ({active_hours}"
                f"{', ' + active_days if active_days else ''})"
            )

        return None

    def _persist_policy(self, policy: CostPolicy) -> None:
        policy_file = self._policy_dir / f"{policy.policy_id}.json"
        with policy_file.open("w") as f:
            f.write(json.dumps(policy.model_dump(), default=str, indent=2))
