"""Weight engine — reuse-first, with the split/synthesis exceptions separated out.

Modules:
  common    shared mesh/vgroup/axis helpers
  transfer  REUSE path (default): unused/control weight → nearest valid deform bone,
            plus the deltoid position-route (still pure reuse)
  chain     SPLIT: inserted 上半身1 / 首1 + armpit smoothing
  twist     SPLIT: arm weight → twist bones by τ-curve (conserving)
  palm      SYNTH: palm → metacarpals + thumb de-bleed (XPS has no metacarpals)
"""
