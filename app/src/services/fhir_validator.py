"""FHIR R5 validator — validates FHIR resource payloads using fhir.resources.

Integrated as a utility that can be called from routes or middleware.
Uses the fhir.resources library (R5) for structural validation.

NOTE: fhir.resources imports are deferred (lazy) to avoid ~200 MB memory
overhead at boot time. Models are loaded on first validation call only.
"""

# Lazy-loaded map — populated on first use
_FHIR_RESOURCE_MAP = None


def _get_resource_map():
    """Load fhir.resources models on first call. Caches for reuse."""
    global _FHIR_RESOURCE_MAP
    if _FHIR_RESOURCE_MAP is None:
        from fhir.resources.capabilitystatement import CapabilityStatement
        from fhir.resources.patient import Patient
        from fhir.resources.practitioner import Practitioner
        from fhir.resources.organization import Organization
        from fhir.resources.group import Group

        _FHIR_RESOURCE_MAP = {
            "CapabilityStatement": CapabilityStatement,
            "Patient": Patient,
            "Practitioner": Practitioner,
            "Organization": Organization,
            "Group": Group,
        }
    return _FHIR_RESOURCE_MAP


class FHIRValidationError(Exception):
    """Raised when FHIR resource validation fails."""
    def __init__(self, resource_type, errors):
        self.resource_type = resource_type
        self.errors = errors
        super().__init__(f"FHIR validation failed for {resource_type}: {errors}")


def validate_fhir_resource(data):
    """Validate a dict as a FHIR resource.

    Args:
        data: dict with at least a 'resourceType' key.

    Returns:
        The validated fhir.resources model instance.

    Raises:
        FHIRValidationError: if validation fails.
        ValueError: if resourceType is missing or unsupported.
    """
    resource_type = data.get("resourceType")
    if not resource_type:
        raise ValueError("Missing 'resourceType' in FHIR resource data")

    model_cls = _get_resource_map().get(resource_type)
    if model_cls is None:
        raise ValueError(f"Unsupported FHIR resource type: {resource_type}")

    try:
        resource = model_cls.model_validate(data)
        return resource
    except Exception as e:
        raise FHIRValidationError(resource_type, str(e))


def validate_capability_statement(data):
    """Validate specifically as a CapabilityStatement."""
    if data.get("resourceType") != "CapabilityStatement":
        raise ValueError("Expected resourceType 'CapabilityStatement'")
    return validate_fhir_resource(data)
