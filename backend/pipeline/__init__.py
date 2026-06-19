"""Ingestion pipeline scripts (host-agnostic).

These modules contain the core ingestion logic and can be run manually, via the
Makefile, or invoked by the Azure Functions entry points in ``pipeline-functions/``
(Azure Cloud Migration Spec v2.0 §8.2). Per the hosting-agnostic requirement they
carry no host-specific runtime dependencies. Full logic — detection, validation,
staging, idempotent promotion (RULE 10) — is implemented in Phase 2.
"""
