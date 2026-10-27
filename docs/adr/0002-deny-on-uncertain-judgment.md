# ADR 0002: deny on uncertain judgment

**Status:** Accepted

## Context

A gate that allows on missing, malformed, or unavailable judgment silently bypasses enforcement. A false block is disruptive, but a false allow may expose private data or enable an unsafe action.

## Decision

Only an explicit valid safe verdict allows. In chat yes/no mode, exact `no` allows and exact `yes` denies. In chat probability mode, a bare finite score below threshold allows; equality denies. Native System One uses a validated binary choice (only strictly winning `no` allows; ties deny) or Noul p(risk=true) strictly below threshold. See ADR 0006 for native schemas. Missing response, invalid data, timeout, transport failure, capacity exhaustion, and exceptions deny.

## Consequences

The gate can deny valid work when the judge is down or cannot parse a response. Operators must keep a healthy configured judge and may disable or remove the plugin deliberately if they choose availability over this gate. Hermes' checked runtime also fails closed on callback exceptions and timeouts, but compatibility must be rechecked for other versions.

Generic model-generated numeric scores are uncalibrated estimates. The threshold is a comparison rule, not a calibrated risk guarantee. TypeSafe System One Noul values have provider-specific semantics and are not equivalent to generic scores.
