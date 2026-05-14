# VPC-SC perimeter (GCP) — L2 graduation note

Framework §7.5 / G-G6 — production agents deploy inside a VPC Service
Controls perimeter. The Terraform module under
`providers/gcp/terraform/` does **not** yet provision the perimeter
because it requires Org Admin privileges that the per-project module
does not have. This file documents how to graduate.

## Required perimeter members

Include the following services in the perimeter:

- `aiplatform.googleapis.com`
- `discoveryengine.googleapis.com`
- `secretmanager.googleapis.com`
- `bigquery.googleapis.com`
- `modelarmor.googleapis.com`

Restrict access from outside the perimeter to a named set of trusted
sources (CI runner identity, on-call engineer's WIF principal, the
FinOps hub's WIF principal).

## Sample Terraform snippet

```hcl
resource "google_access_context_manager_service_perimeter" "finops" {
  parent = "accessPolicies/${var.access_policy_id}"
  name   = "accessPolicies/${var.access_policy_id}/servicePerimeters/finops"
  title  = "FinOps agent perimeter"

  status {
    restricted_services = [
      "aiplatform.googleapis.com",
      "discoveryengine.googleapis.com",
      "secretmanager.googleapis.com",
      "bigquery.googleapis.com",
      "modelarmor.googleapis.com",
    ]

    resources = ["projects/${var.project_number}"]

    ingress_policies {
      ingress_from {
        identity_type = "ANY_IDENTITY"
        sources {
          access_level = google_access_context_manager_access_level.trusted.name
        }
      }
      ingress_to {
        resources = ["*"]
        operations {
          service_name = "aiplatform.googleapis.com"
          method_selectors { method = "*" }
        }
      }
    }
  }
}
```

This block is **not** in the live Terraform — it is held here pending
an Org Admin who can land the `accessPolicies` resource.
