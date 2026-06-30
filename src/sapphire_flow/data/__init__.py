"""Packaged static data assets (e.g. the ICON-CH2-EPS mesh grid coordinates).

A regular package (not a namespace package) so `importlib.resources.files(
"sapphire_flow.data")` resolves under zipimport / zipped-wheel deployments too.
"""
