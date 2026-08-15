"""Sentinel-Z Live — a real assistant, real tools, real defense.

The arena in `arena/` measures the defense against AgentDojo's 629 injection
cases. This package is the other half: a chat assistant a person actually
drives, with the same gateway sitting under it.

Nothing here is a second defense. `sentinelz.gateway` and
`sentinelz.broker` are imported unchanged — this package only builds
`DecisionContext` objects and honours the decisions that come back.
"""
