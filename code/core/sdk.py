"""The SDK seam — the single module in this repo allowed to import the official SDKs.

THE SEAM RULE: `microsoft_fabric_api` is imported HERE and nowhere else, and
`azure.mgmt.fabric`'s client is constructed here and nowhere else
(`tests/test_sdk_seam.py` enforces both). Wrapper clients under `clients/` and
`builders/` get their SDK client from `fabric_client()` / `arm_fabric_client()`; gym
tasks, skills and wiki pages cite the wrappers, never a raw SDK call. The version pins
live in `requirements.txt` — exact by design (the SDK is beta, regenerated monthly) and
bumped monthly behind the CI gates.

Both factories take `transport=`, handed straight to azure-core, so a test can inject a
fake `HttpTransport` and drive the full pipeline offline.
"""
from microsoft_fabric_api import FabricClient

from auth import Credential
from config import AZURE_SUBSCRIPTION_ID


class SdkTokenCredential(Credential):
    """azure-core's TokenCredential protocol, delegating to `auth.get_token`.

    `auth.Credential` already implements the shape (`get_token(*scopes, **kwargs) ->
    AccessToken`, minted through the per-scope MSAL cache with the expiry read off the
    token's own `exp` claim); this subclass only gives the seam a named credential type —
    no auth logic is duplicated. The scope mapping is 1:1: the Fabric SDK asks for
    `https://api.fabric.microsoft.com/.default` (= `auth.SCOPE`) and azure-mgmt-fabric
    for `https://management.azure.com/.default` (= `auth.ARM_SCOPE`), both audiences
    `auth.py` already carries, and `scopes[0]` is the audience passed through.
    """


def fabric_client(credential=None, *, transport=None, **kwargs) -> FabricClient:
    """The configured `FabricClient` — every Fabric-plane SDK call starts here.

    One workload client per item type hangs off it (`.core`, `.lakehouse`, `.ontology`,
    ...), each an azure-core pipeline against `https://api.fabric.microsoft.com/v1/`.
    `credential` defaults to `SdkTokenCredential()` (one identity, one cache — whatever
    `FABRIC_AUTH_MODE` says); `transport=` is the offline test hook, forwarded through
    `**kwargs` to every sub-client's pipeline.
    """
    if transport is not None:
        kwargs["transport"] = transport
    return FabricClient(credential or SdkTokenCredential(), **kwargs)


def to_wire(obj):
    """An SDK return value -> the wire-format plain type the wrappers promise.

    Generated (msrest) models serialize back to their REST JSON shape —
    ``serialize(keep_readonly=True)`` restores camelCase keys AND server-assigned
    read-only fields like ``id`` (``as_dict()`` would snake_case every key and is
    never what a wrapper wants). Pagers and lists convert element-wise, draining the
    pager fully so no listing is ever silently truncated; None and plain JSON types
    pass through untouched.
    """
    if obj is None or isinstance(obj, (str, int, float, bool, dict)):
        return obj
    if hasattr(obj, "serialize"):
        return obj.serialize(keep_readonly=True)
    if hasattr(obj, "__iter__"):
        return [to_wire(x) for x in obj]
    return obj


def lro_result(extractor, *, timeout: int, label: str):
    """Bounded wait on the SDK's ``_LROResultExtractor``, failing loudly.

    The SDK's own sync wrappers around result-bearing LROs sleep unboundedly and, on a
    ``Failed`` operation, hand back the raw operation state as if it were the result
    (verified empirically against a faked LRO). This keeps the toolkit's old
    ``handle_lro`` contract instead: a timeout raises TimeoutError, a Failed or
    Cancelled operation raises HttpResponseError. Wrappers call ``begin_*`` ops and
    pass the extractor here rather than using the SDK's sync wrappers.
    """
    import time
    from azure.core.exceptions import HttpResponseError
    deadline = time.time() + timeout
    while extractor.result is None:
        if time.time() >= deadline:
            raise TimeoutError(f"{label} did not complete within {timeout}s")
        time.sleep(1)
    result = extractor.result
    # The terminal status lands in additional_properties when the result model has no
    # `status` field of its own, and in the model field when it does (a deploy's
    # operation-info result, for example) — read both before trusting the payload.
    extra = getattr(result, "additional_properties", None) or {}
    status = extra.get("status") or getattr(result, "status", None)
    if status in ("Failed", "Cancelled"):
        error = extra.get("error") or getattr(result, "error", None)
        raise HttpResponseError(message=f"{label} {status}: {error}")
    return result


def sdk_models(group: str):
    """The generated models module for one workload group, imported seam-side.

    Wrappers build request bodies as ``sdk_models("lakehouse").CreateLakehouseRequest
    .from_dict({wire json})`` — ``from_dict`` takes wire-format (camelCase) keys, so a
    wrapper's payload reads exactly like the REST docs while the SDK import stays in
    this module.
    """
    import importlib
    return importlib.import_module(f"microsoft_fabric_api.generated.{group}.models")


def arm_fabric_client(subscription_id: str = None, credential=None, *,
                      transport=None, **kwargs):
    """`azure.mgmt.fabric.FabricMgmtClient` — the ARM control plane (capacities).

    Fabric capacities are ARM resources, not Fabric items, so they need the management
    client and a subscription. The subscription resolves argument-over-environment like
    `config.require_arm_scope` (no resource group here — ARM operations that need one
    take it per call), and the failure lands at the call, not at import.
    """
    from azure.mgmt.fabric import FabricMgmtClient          # constructed only here
    sub = subscription_id or AZURE_SUBSCRIPTION_ID
    if not sub:
        raise RuntimeError("AZURE_SUBSCRIPTION_ID is not set (set it in the process env "
                           "or the repo-root .env) — needed by sdk.arm_fabric_client()")
    if transport is not None:
        kwargs["transport"] = transport
    return FabricMgmtClient(credential or SdkTokenCredential(), sub, **kwargs)
