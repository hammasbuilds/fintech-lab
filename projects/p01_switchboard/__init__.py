"""Switchboard - a payment-message workbench.

Decode raw ISO 8583 card traffic, generate transaction lifecycles from a scenario,
check a flow against the invariants a switch relies on, and measure how much of that
checking actually holds by injecting faults into valid flows.

Every generated message is packed and re-parsed before it leaves the engine, so the
scenario builder cannot emit something the codec would reject.
"""
