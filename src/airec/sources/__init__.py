"""Clients for outside services. They know nothing about sections or reports.

A source function takes what it needs, returns plain records, and raises on
failure. Deciding what a failure means belongs to the section that called it.
"""
