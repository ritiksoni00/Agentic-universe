# Grade report — public_01_dev

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.353 | +3.4% | 0.242 | -1.2% | 0.512 | +4.2% | 17% | 60 (57) |
| nw_kyc | 0.387 | +5.5% | 0.242 | +0.1% | 0.627 | +5.0% | 21% | 20 (21) |
| nw_renewal | 0.439 | +5.5% | 0.263 | -0.4% | 0.570 | +5.2% | 27% | 44 (54) |
| ALL | 0.389 | +4.6% | 0.249 | -0.7% | 0.555 | +4.8% | 22% | 124 (132) |

## Changes applied by the controller

16 applied — neutral: 8, beneficial: 7, harmful: 1

- ks_cancel v2 (controller_approved) dims=['cohorts'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_renewal v2 (controller_approved) dims=['cohorts'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_kyc v2 (controller_approved) dims=['cohorts'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_renewal v3 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.068 didΔ=+0.000 → **beneficial**
- ks_cancel v3 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=-0.002 didΔ=+0.000 → **neutral**
- nw_kyc v3 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.074 didΔ=+0.000 → **beneficial**
- nw_renewal v4 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.058 didΔ=+0.000 → **beneficial**
- nw_renewal v5 (controller_auto) dims=['schedule'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- ks_cancel v4 (controller_auto) dims=['schedule'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_kyc v4 (controller_approved) dims=['schedule'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_renewal v6 (controller_approved) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- ks_cancel v5 (controller_approved) dims=['voice'] engageΔ=+0.050 didΔ=+0.000 → **beneficial**
- ks_cancel v6 (controller_approved) dims=['welcome_message'] engageΔ=+0.042 didΔ=+0.000 → **beneficial**
- nw_renewal v7 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.067 → **harmful**
- nw_renewal v8 (controller_approved) dims=['welcome_message'] engageΔ=+0.057 didΔ=+0.000 → **beneficial**
- nw_renewal v9 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.159 → **beneficial**

## Governance

```
{
  "approval_requests": 14,
  "decisions": {
    "approved": 13,
    "rejected": 1,
    "expired": 0
  },
  "rejection_reasons": {
    "approver declined (judgement call)": 1
  },
  "applied_via_approval": 13,
  "auto_applied": 3,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nw_renewal": 2,
    "nw_kyc": 2,
    "ks_cancel": 2
  }
}
```

## Incidents

```
[]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{
  "nw_renewal:welcome_message=W3": {
    "4": 0.845,
    "5": 0.851,
    "6": 0.718,
    "7": 0.105
  }
}
```

Credits used: 0 · validation runs: 0 · LLM fix calls: 0