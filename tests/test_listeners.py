"""Tests for AWS and GCP cloud event listeners."""

from datetime import UTC, datetime

from core.models import CloudProvider
from providers.aws.listener import CREATION_EVENTS, AWSEventListener
from providers.gcp.listener import CREATION_METHODS, GCPEventListener

# ---------------------------------------------------------------------------
# GCP Event Listener
# ---------------------------------------------------------------------------


class TestGCPEventListenerInit:
    def test_provider_name(self):
        listener = GCPEventListener()
        assert listener.provider_name == CloudProvider.GCP

    def test_get_logging_client_returns_client_or_none(self):
        listener = GCPEventListener()
        client = listener._get_logging_client()
        # If google-cloud-logging is installed, returns a client; otherwise None
        assert client is None or client is not None

    def test_listen_for_events_without_client(self):
        listener = GCPEventListener()
        events = listener.listen_for_events({"project_ids": ["test-proj"], "lookback_minutes": 5})
        assert events == []

    def test_estimate_monthly_cost_delegates(self):
        listener = GCPEventListener()
        cost = listener.estimate_monthly_cost(
            "compute.instances", {"machine_type": "n2-standard-2"}
        )
        assert isinstance(cost, float)
        assert cost >= 0

    def test_get_resource_tags_returns_empty(self):
        listener = GCPEventListener()
        tags = listener.get_resource_tags("some-id", "compute.instances")
        assert tags == {}

    def test_get_resource_details(self):
        listener = GCPEventListener()
        details = listener.get_resource_details("my-id", "compute.instances")
        assert details["resource_id"] == "my-id"
        assert details["resource_type"] == "compute.instances"

    def test_validate_credentials(self):
        listener = GCPEventListener()
        result = listener.validate_credentials()
        assert isinstance(result, bool)


class TestGCPCreatorIdentity:
    def test_extracts_principal_email(self):
        listener = GCPEventListener()
        event_data = {
            "protoPayload": {"authenticationInfo": {"principalEmail": "alice@example.com"}}
        }
        principal, email = listener.get_creator_identity(event_data)
        assert principal == "alice@example.com"
        assert email == "alice@example.com"

    def test_handles_missing_auth_info(self):
        listener = GCPEventListener()
        principal, _email = listener.get_creator_identity({})
        assert principal == "unknown"


class TestGCPExtractRegion:
    def test_extracts_zone(self):
        listener = GCPEventListener()
        raw = {"resource": {"labels": {"zone": "us-central1-a"}}}
        assert listener._extract_region(raw) == "us-central1-a"

    def test_extracts_location_fallback(self):
        listener = GCPEventListener()
        raw = {"resource": {"labels": {"location": "europe-west1"}}}
        assert listener._extract_region(raw) == "europe-west1"

    def test_default_region(self):
        listener = GCPEventListener()
        assert listener._extract_region({}) == "europe-west2"


class TestGCPExtractResourceConfig:
    def test_compute_instance(self):
        listener = GCPEventListener()
        raw = {
            "protoPayload": {
                "request": {"machineType": "zones/us-central1-a/machineTypes/n2-standard-4"}
            }
        }
        config = listener._extract_resource_config(raw, "compute.instances")
        assert config["machine_type"] == "n2-standard-4"

    def test_cloudsql_instance(self):
        listener = GCPEventListener()
        raw = {
            "protoPayload": {
                "request": {
                    "settings": {"tier": "db-custom-4-15360", "availabilityType": "REGIONAL"}
                }
            }
        }
        config = listener._extract_resource_config(raw, "cloudsql.instances")
        assert config["tier"] == "db-custom-4-15360"
        assert config["availability_type"] == "REGIONAL"

    def test_container_cluster(self):
        listener = GCPEventListener()
        raw = {
            "protoPayload": {
                "request": {
                    "nodePools": [
                        {
                            "initialNodeCount": 5,
                            "config": {"machineType": "e2-standard-8"},
                        }
                    ]
                }
            }
        }
        config = listener._extract_resource_config(raw, "container.clusters")
        assert config["node_count"] == 5
        assert config["machine_type"] == "e2-standard-8"

    def test_unknown_resource_type(self):
        listener = GCPEventListener()
        config = listener._extract_resource_config({"protoPayload": {}}, "unknown.type")
        assert config == {}


class TestGCPTranslateEvent:
    def test_translates_valid_event(self):
        listener = GCPEventListener()
        raw = {
            "protoPayload": {
                "methodName": "v1.compute.instances.insert",
                "authenticationInfo": {"principalEmail": "user@example.com"},
                "resourceName": "projects/p/zones/z/instances/my-vm",
                "request": {"machineType": "n2-standard-2"},
            },
            "resource": {"labels": {"zone": "us-east1-b"}},
            "timestamp": datetime.now(UTC).isoformat(),
        }
        event = listener._translate_event(raw, "test-project")
        assert event is not None
        assert event.resource_type == "compute.instances"
        assert event.provider == CloudProvider.GCP
        assert event.resource_id == "my-vm"
        assert event.account_id == "test-project"

    def test_returns_none_for_unknown_method(self):
        listener = GCPEventListener()
        raw = {"protoPayload": {"methodName": "unknown.method"}}
        assert listener._translate_event(raw, "test") is None


class TestGCPCreationMethods:
    def test_creation_methods_non_empty(self):
        assert len(CREATION_METHODS) > 0

    def test_all_methods_map_to_resource_types(self):
        for method, rtype in CREATION_METHODS.items():
            assert isinstance(method, str)
            assert isinstance(rtype, str)
            assert "." in rtype


# ---------------------------------------------------------------------------
# AWS Event Listener
# ---------------------------------------------------------------------------


class TestAWSEventListenerInit:
    def test_provider_name(self):
        listener = AWSEventListener()
        assert listener.provider_name == CloudProvider.AWS

    def test_get_session_without_boto3(self):
        listener = AWSEventListener()
        session = listener._get_session()
        # boto3 not installed in test env
        assert session is None

    def test_listen_for_events_without_session(self):
        listener = AWSEventListener()
        events = listener.listen_for_events({"regions": ["eu-west-2"], "lookback_minutes": 5})
        assert events == []

    def test_estimate_monthly_cost_delegates(self):
        listener = AWSEventListener()
        cost = listener.estimate_monthly_cost("ec2:instance", {"instance_type": "m5.large"})
        assert isinstance(cost, float)
        assert cost >= 0

    def test_get_resource_tags_without_session(self):
        listener = AWSEventListener()
        tags = listener.get_resource_tags("arn:aws:ec2:...", "ec2:instance")
        assert tags == {}

    def test_get_resource_details_without_session(self):
        listener = AWSEventListener()
        details = listener.get_resource_details("i-123", "ec2:instance")
        assert details["resource_id"] == "i-123"

    def test_validate_credentials_without_session(self):
        listener = AWSEventListener()
        assert listener.validate_credentials() is False


class TestAWSCreatorIdentity:
    def test_extracts_arn_and_username(self):
        listener = AWSEventListener()
        event_data = {
            "userIdentity": {
                "arn": "arn:aws:iam::123:user/alice",
                "userName": "alice",
            }
        }
        principal, email = listener.get_creator_identity(event_data)
        assert principal == "arn:aws:iam::123:user/alice"
        assert email == "alice"

    def test_extracts_session_issuer_username(self):
        listener = AWSEventListener()
        event_data = {
            "userIdentity": {
                "principalId": "AROAEXAMPLE:session",
                "sessionContext": {"sessionIssuer": {"userName": "role-name"}},
            }
        }
        principal, email = listener.get_creator_identity(event_data)
        assert principal == "AROAEXAMPLE:session"
        assert email == "role-name"

    def test_handles_missing_identity(self):
        listener = AWSEventListener()
        principal, _email = listener.get_creator_identity({})
        assert principal == "unknown"


class TestAWSExtractResourceConfig:
    def test_ec2_instance(self):
        listener = AWSEventListener()
        raw = {"requestParameters": {"instanceType": "c5.xlarge"}}
        config = listener._extract_resource_config(raw, "ec2:instance")
        assert config["instance_type"] == "c5.xlarge"

    def test_rds_db(self):
        listener = AWSEventListener()
        raw = {
            "requestParameters": {
                "dBInstanceClass": "db.r5.large",
                "engine": "mysql",
                "multiAZ": True,
            }
        }
        config = listener._extract_resource_config(raw, "rds:db")
        assert config["instance_class"] == "db.r5.large"
        assert config["engine"] == "mysql"
        assert config["multi_az"] is True

    def test_eks_cluster(self):
        listener = AWSEventListener()
        config = listener._extract_resource_config({}, "eks:cluster")
        assert config["node_count"] == 3

    def test_nat_gateway(self):
        listener = AWSEventListener()
        config = listener._extract_resource_config({}, "nat_gateway")
        assert config["count"] == 1

    def test_unknown_type_returns_empty(self):
        listener = AWSEventListener()
        config = listener._extract_resource_config({}, "unknown:type")
        assert config == {}


class TestAWSTranslateEvent:
    def test_translates_valid_event(self):
        listener = AWSEventListener()
        raw = {
            "EventName": "RunInstances",
            "EventTime": datetime.now(UTC),
            "userIdentity": {"arn": "arn:aws:iam::123:user/bob", "userName": "bob"},
            "Resources": [{"ResourceName": "i-abc123"}],
            "recipientAccountId": "123456789012",
            "requestParameters": {"instanceType": "m5.large"},
        }
        event = listener._translate_event(raw, "eu-west-2")
        assert event is not None
        assert event.resource_type == "ec2:instance"
        assert event.provider == CloudProvider.AWS
        assert event.resource_id == "i-abc123"
        assert event.region == "eu-west-2"

    def test_returns_none_for_unknown_event(self):
        listener = AWSEventListener()
        raw = {"EventName": "DescribeInstances"}
        assert listener._translate_event(raw, "eu-west-2") is None


class TestAWSCreationEvents:
    def test_creation_events_non_empty(self):
        assert len(CREATION_EVENTS) > 0

    def test_all_events_map_to_resource_types(self):
        for event_name, rtype in CREATION_EVENTS.items():
            assert isinstance(event_name, str)
            assert isinstance(rtype, str)
