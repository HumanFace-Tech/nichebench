"""Unit tests for framework data contracts.

These tests do NOT require Docker, DDEV, or a real Drupal installation.
They validate the shape of the quiz dataset: schema, option counts, gold
letter range, and uniqueness of options and question identities.

The real dataset lives in a private submodule. When it is not checked out,
the tests that need it are skipped rather than failed.
"""
