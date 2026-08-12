"""Channel gateways — adapters that put Sevi's /chat pipeline on the
messaging surfaces students actually use, without touching the cascade.

Each gateway is a self-contained APIRouter that app.py mounts only when its
env switch is on, so a deployment that never sets the flags is byte-for-byte
unaffected. First (and so far only) gateway: Facebook Messenger — see
messenger.py and docs/MESSENGER_GATEWAY.md for the reach/equity rationale
(Messenger is used by ~90% of PH internet users and stays reachable on
zero-rated data plans that cannot open sevi.cvsu.edu.ph).
"""
